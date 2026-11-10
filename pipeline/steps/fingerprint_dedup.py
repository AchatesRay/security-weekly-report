"""正文指纹去重 + 跨周发布台账（第 5 步的 5.5 子阶段）

为什么必须放在 fulltext_extractor 之后：
  step 3 只有 RSS 摘要，很多源只给一两句话；同一篇稿子被多家转载时，
  摘要往往被各站改写，标题相似度也未必过线。只有抓回正文后做 SimHash，
  才能稳定识别“正文几乎逐字相同”的重复。

为什么台账单独放 data/state/：
  Orchestrator 开跑会清空 INTERMEDIATE_FILES；台账是跨周持久状态，
  放进 data/ 根目录会在下一轮开跑时被删掉，跨周去重就永远不生效。
"""

import json
from datetime import datetime

from ..utils import DATA_DIR, SETTINGS_PATH, atomic_write, parse_datetime_utc
from ..utils.fingerprint import (DEFAULT_LEDGER_TTL_DAYS, content_hash,
                                 fingerprint_text, hamming64, load_ledger,
                                 match_published, simhash64, week_key)
from ..utils.textnorm import normalize_url
from .deduplicator import SOURCE_PRIORITY

PARSED_ITEMS_PATH = DATA_DIR / "parsed_items.json"

DEFAULT_NEAR_DUP_HAMMING = 3
DEFAULT_WINDOW_HOURS = 72
MIN_FINGERPRINT_CHARS = 200


def _load_config() -> dict:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f).get("dedup", {}) or {}
    except Exception:
        return {}


def _body_len(item: dict) -> int:
    return len(item.get("full_body") or item.get("summary") or "")


def _published_ts(item: dict) -> float:
    dt = parse_datetime_utc(item.get("published_date", ""))
    return dt.timestamp() if dt else float("-inf")


def _within_window(a: dict, b: dict, hours: int) -> bool:
    ta, tb = _published_ts(a), _published_ts(b)
    if ta == float("-inf") or tb == float("-inf"):
        # 缺时间时不跨周判断：同一轮抓取本身就落在本周窗口内
        return True
    return abs(ta - tb) <= hours * 3600


def _representative_key(item: dict) -> tuple:
    """信源等级 → 正文长度 → 发布时间（越早越接近原始来源）。"""
    ts = _published_ts(item)
    # max() 取最大值；发布时间越早 timestamp 越小，故取负值
    return (
        SOURCE_PRIORITY.get(item.get("source_name", ""), 3),
        _body_len(item),
        -ts if ts != float("-inf") else float("-inf"),
    )


def _merge_cluster(members: list[dict]) -> dict:
    rep = max(members, key=_representative_key)
    sources = []
    urls = []
    for m in members:
        name = m.get("source_name", "")
        if name and name not in sources:
            sources.append(name)
        url = m.get("url", "")
        if url and url not in urls:
            urls.append(url)
    merged = rep.setdefault("merged_sources", [])
    for name in sources:
        if name not in merged:
            merged.append(name)
    if len(members) > 1:
        rep["duplicate_count"] = int(rep.get("duplicate_count", 0)) + len(members) - 1
        rep["duplicate_urls"] = urls
        rep["cluster_sources"] = sources
    return rep


def deduplicate_by_fingerprint(items: list[dict], hamming: int = DEFAULT_NEAR_DUP_HAMMING,
                               window_hours: int = DEFAULT_WINDOW_HOURS) -> list[dict]:
    n = len(items)
    if n < 2:
        return items

    fingerprints = []
    for item in items:
        text = fingerprint_text(item)
        fingerprints.append(simhash64(text) if len(text) >= MIN_FINGERPRINT_CHARS else 0)

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        if not fingerprints[i]:
            continue
        for j in range(i + 1, n):
            if not fingerprints[j]:
                continue
            if hamming64(fingerprints[i], fingerprints[j]) > hamming:
                continue
            if not _within_window(items[i], items[j], window_hours):
                continue
            union(i, j)

    clusters: dict[int, list[dict]] = {}
    for idx, item in enumerate(items):
        clusters.setdefault(find(idx), []).append(item)

    result = [_merge_cluster(members) for members in clusters.values()]
    # 并查集按首次出现顺序输出，保持报告原始顺序稳定
    order = {id(item): idx for idx, item in enumerate(items)}
    result.sort(key=lambda it: order.get(id(it), 0))
    return result


def run():
    if not PARSED_ITEMS_PATH.exists():
        raise FileNotFoundError(
            f"缺少上一步产物 {PARSED_ITEMS_PATH}，无法做正文指纹去重")

    with open(PARSED_ITEMS_PATH, encoding="utf-8") as f:
        items = json.load(f)

    cfg = _load_config()
    try:
        hamming = int(cfg.get("near_dup_hamming", DEFAULT_NEAR_DUP_HAMMING))
    except (TypeError, ValueError):
        hamming = DEFAULT_NEAR_DUP_HAMMING
    try:
        window_hours = int(cfg.get("window_hours", DEFAULT_WINDOW_HOURS))
    except (TypeError, ValueError):
        window_hours = DEFAULT_WINDOW_HOURS
    try:
        ttl_days = int(cfg.get("ledger_ttl_days", DEFAULT_LEDGER_TTL_DAYS))
    except (TypeError, ValueError):
        ttl_days = DEFAULT_LEDGER_TTL_DAYS

    current_week = week_key()
    ledger_enabled = bool(cfg.get("ledger_enabled", True))
    near_dup_enabled = bool(cfg.get("near_dup_enabled", True))
    kept = []
    published_dropped = 0
    ledger = load_ledger() if ledger_enabled else {}
    for item in items:
        if ledger_enabled:
            hit = match_published(item, ledger, current_week, ttl_days=ttl_days)
            if hit:
                item["already_published_week"] = hit.get("week", "")
                published_dropped += 1
                continue
        kept.append(item)

    before = len(kept)
    if near_dup_enabled:
        result = deduplicate_by_fingerprint(kept, hamming=hamming, window_hours=window_hours)
    else:
        result = kept

    atomic_write(PARSED_ITEMS_PATH, result, indent=2)
    merged = before - len(result)
    print(f"[FINGERPRINT] 跨周台账过滤: {published_dropped} 条已发布内容不再收录；"
          f"正文近重复合并: {merged} 条（{before} → {len(result)}）"
          f"（台账={'开' if ledger_enabled else '关'}，"
          f"近重复={'开' if near_dup_enabled else '关'}）")
    return result


if __name__ == "__main__":
    run()
