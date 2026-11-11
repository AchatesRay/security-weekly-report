"""去重模块 — URL 规范化去重 + 标题相似度去重 + 过期过滤

2026-09-29 修复：
  1. 过期过滤此前对**带时区**的时间戳必然抛 TypeError 并被 except 吞掉，
     导致任意年代的旧文章都能进本周报告。现在统一经 parse_datetime_utc()
     归一到 UTC 无时区后再比较。
  2. URL 与标题先做规范化（去追踪参数、统一大小写与末尾斜杠、去标点），
     减少“同一篇文章因链接/标题细微差异绕过去重”的情况。
  3. 标题比对改用 rapidfuzz.process.extractOne（C++ 实现），替代原来的
     Python 双重循环（条目多时是 O(n²) 次纯 Python 调用）。
  4. 相似度阈值默认值与 config/settings.json、README 对齐（75），
     此前代码默认 85 与文档不一致。
"""

import json
from datetime import timedelta

import yaml
from rapidfuzz import fuzz, process

from ..utils import (DATA_DIR, SETTINGS_PATH, SOURCES_PATH, atomic_write,
                     parse_datetime_utc, utc_now_naive)
from ..utils.textnorm import normalize_title, normalize_url

PARSED_ITEMS_PATH = DATA_DIR / "parsed_items.json"
DEDUPED_ITEMS_PATH = DATA_DIR / "deduped_items.json"

DEFAULT_SIMILARITY_THRESHOLD = 75
DEFAULT_MAX_DAYS = 7

def _load_config() -> dict:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("dedup", {})
    except Exception:
        return {}


def load_thresholds() -> tuple[int, int]:
    """返回 (相似度阈值, 最大天数)；配置缺失时使用与文档一致的默认值"""
    cfg = _load_config()
    try:
        thr = int(cfg.get("similarity_threshold", DEFAULT_SIMILARITY_THRESHOLD))
    except (TypeError, ValueError):
        thr = DEFAULT_SIMILARITY_THRESHOLD
    try:
        days = int(cfg.get("max_days", DEFAULT_MAX_DAYS))
    except (TypeError, ValueError):
        days = DEFAULT_MAX_DAYS
    return max(0, min(100, thr)), max(1, days)


SIMILARITY_THRESHOLD, MAX_DAYS = load_thresholds()

_GROUP_PRIORITY = {
    "政府与CERT": 5, "标准框架": 5, "安全厂商": 4, "开发者社区": 4,
    "安全媒体": 3, "国内信源": 3, "AI厂商": 2,
}
_TIER_PRIORITY = {"S": 5, "A": 4, "B": 3, "C": 2}

def _load_source_priority() -> dict:
    """source_name -> 优先级（越大越权威），用于同名事件保留最佳版本。"""
    priority: dict = {}
    try:
        with open(SOURCES_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        for src in cfg.get("sources", []):
            if isinstance(src, dict) and src.get("name"):
                tier = str(src.get("tier", "")).upper()
                priority[src["name"]] = _TIER_PRIORITY.get(
                    tier, _GROUP_PRIORITY.get(src.get("group", ""), 3))
    except Exception:
        pass
    return priority

SOURCE_PRIORITY = _load_source_priority()

def filter_by_date(items: list[dict], max_days: int = MAX_DAYS) -> list[dict]:
    """过滤掉超出 max_days 天的旧条目；无日期或无法解析日期的条目保留。"""
    cutoff = utc_now_naive() - timedelta(days=max_days)
    kept: list[dict] = []
    dropped = 0
    unparsable = 0
    for item in items:
        raw = item.get("published_date", "")
        if not raw:
            kept.append(item)
            continue
        pub = parse_datetime_utc(raw)
        if pub is None:
            unparsable += 1
            kept.append(item)
            continue
        if pub >= cutoff:
            kept.append(item)
        else:
            dropped += 1
    if dropped:
        print(f"  [FILTER] 过滤掉 {dropped} 条过期内容（>{max_days}天）")
    if unparsable:
        print(f"  [FILTER] {unparsable} 条时间无法解析，按“保留”处理")
    return kept


def deduplicate(items: list[dict]) -> list[dict]:
    """URL 规范化去重 + 标题相似度模糊去重。

    合并同类项时保留多信源信息，标记为 merged_sources。
    """
    seen_urls: set[str] = set()
    seen_norms: list[str] = []          # 与 result 一一对应的规范化标题
    result: list[dict] = []
    url_dups = 0
    title_dups = 0

    for item in items:
        url = item.get("url", "") or ""
        title = item.get("title", "") or ""
        source_name = item.get("source_name", "") or ""

        norm_url = normalize_url(url)
        if norm_url and norm_url in seen_urls:
            url_dups += 1
            continue
        if norm_url:
            seen_urls.add(norm_url)

        norm_title = normalize_title(title)
        if norm_title:
            match = process.extractOne(
                norm_title, seen_norms,
                scorer=fuzz.token_sort_ratio,
                score_cutoff=SIMILARITY_THRESHOLD,
            )
            if match is not None:
                _choice, _score, idx = match
                existing = result[idx]
                merged = existing.setdefault(
                    "merged_sources", [existing.get("source_name", "")])
                if source_name and source_name not in merged:
                    merged.append(source_name)
                # 同一事件保留“最佳版本”：信源等级优先，其次摘要更长
                new_pri = SOURCE_PRIORITY.get(source_name, 3)
                old_pri = SOURCE_PRIORITY.get(existing.get("source_name", ""), 3)
                new_len = len(item.get("summary") or "")
                old_len = len(existing.get("summary") or "")
                if new_pri > old_pri or (new_pri == old_pri and new_len > old_len * 1.2):
                    item["merged_sources"] = merged
                    result[idx] = item
                    seen_norms[idx] = norm_title
                title_dups += 1
                continue

        seen_norms.append(norm_title)
        result.append(item)

    print(f"[DEDUP] 去重: {len(items)} -> {len(result)} 条"
          f"（URL 重复 {url_dups} 条，标题相似 {title_dups} 条）")
    return result


def run():
    if not PARSED_ITEMS_PATH.exists():
        raise FileNotFoundError(
            f"缺少上一步产物 {PARSED_ITEMS_PATH}，无法去重（请检查解析步骤是否失败）")
    with open(PARSED_ITEMS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    items = filter_by_date(items)
    result = deduplicate(items)

    atomic_write(DEDUPED_ITEMS_PATH, result, indent=2)

    return result


if __name__ == "__main__":
    run()
