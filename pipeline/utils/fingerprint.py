"""内容指纹与跨周发布台账

用途：
  1. 64 位 SimHash：判断两篇正文是否“换了 URL 的同一篇稿子”；
  2. 内容哈希：给跨周台账提供精确比对键；
  3. 台账：记录已经随周报发布过的条目，下一周不再重复收录。

口径说明：
  - 中文按**字符 3-gram**、英文按**词 3-gram** 生成特征，先做 NFKC 与
    空白归一化；标点不计入特征。
  - 正文优先级：full_body > summary；两者都太短时退回“标题 + 摘要”。
  - 台账只记录**已经成功生成报告与移动版**的条目，且同周重跑不生效
    （否则重跑一次就会把本周内容全部当成“已发布”而清空周报）。
"""

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta

import numpy as np

from . import DATA_DIR, atomic_write, parse_datetime_utc, utc_now_naive
from .textnorm import normalize_url

LEDGER_PATH = DATA_DIR / "state" / "published_ledger.json"
DEFAULT_LEDGER_TTL_DAYS = 56          # 8 周
MAX_LEDGER_ENTRIES = 10000

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_+#.\-]*", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """NFKC + 小写 + 空白压缩；指纹与内容哈希共用同一口径。"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text)).lower()
    return _WS_RE.sub(" ", text).strip()


def _features(text: str, max_features: int = 8000) -> list[str]:
    """中文字符 3-gram + 英文词 3-gram；超长正文等距抽样，避免只取开头。"""
    text = normalize_text(text)
    if not text:
        return []
    features: list[str] = []
    for run in _CJK_RE.findall(text):
        if len(run) <= 3:
            features.append(run)
        else:
            features.extend(run[i:i + 3] for i in range(len(run) - 2))
    words = _WORD_RE.findall(text)
    if len(words) <= 3:
        features.extend(words)
    else:
        features.extend(" ".join(words[i:i + 3]) for i in range(len(words) - 2))
    if len(features) > max_features:
        step = max(1, len(features) // max_features)
        features = features[::step][:max_features]
    return features


def simhash64(text: str, max_features: int = 8000) -> int:
    """返回 64 位 SimHash；空文本返回 0（调用方应跳过 0）。"""
    feats = _features(text, max_features=max_features)
    if not feats:
        return 0
    weights: dict[str, int] = {}
    for f in feats:
        weights[f] = weights.get(f, 0) + 1

    vec = np.zeros(64, dtype=np.int32)
    for feat, weight in weights.items():
        digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
        bits = np.unpackbits(np.frombuffer(digest, dtype=np.uint8))
        vec += np.where(bits == 1, weight, -weight)

    packed = np.packbits((vec > 0).astype(np.uint8))
    return int.from_bytes(packed.tobytes(), "big")


def hamming64(a: int, b: int) -> int:
    return int(a ^ b).bit_count()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def fingerprint_text(item: dict) -> str:
    """取用于指纹的文本：优先正文，其次摘要，最后标题。"""
    title = item.get("title") or ""
    body = item.get("full_body") or ""
    summary = item.get("summary") or ""
    text = body if len(body) >= len(summary) else summary
    if len(text) < 80:
        return f"{title}\n{text}".strip()
    return text


def week_key(dt=None) -> str:
    """与报告一致的 ISO 周号：2026W46。

    必须用**本地时间**：report_generator 用 datetime.now() 生成周号并据此命名
    data_<week>.json；台账若用 UTC 周号，在“本地已进入新一周、UTC 尚未跨周”
    的时段会去读上一周的 data 文件，导致重复登记旧内容。
    """
    dt = dt or datetime.now()
    iso = dt.isocalendar()
    return f"{iso[0]}W{iso[1]:02d}"


def _empty_ledger() -> dict:
    return {"version": 1, "updated_at": "", "entries": {}}


def load_ledger() -> dict:
    if not LEDGER_PATH.exists():
        return _empty_ledger()
    try:
        with open(LEDGER_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("entries"), dict):
            return data
    except Exception:
        pass
    return _empty_ledger()


def _entry_is_relevant(entry: dict, current_week: str, ttl_days: int) -> bool:
    """同周条目对重跑不生效；超过 TTL 的条目不再参与比对。"""
    if not isinstance(entry, dict):
        return False
    if entry.get("week") == current_week:
        return False
    last_seen = parse_datetime_utc(entry.get("last_seen", ""))
    if last_seen is None:
        return False
    return last_seen >= utc_now_naive() - timedelta(days=ttl_days)


def match_published(item: dict, ledger: dict, current_week: str,
                    ttl_days: int = DEFAULT_LEDGER_TTL_DAYS) -> dict | None:
    """返回命中的台账条目；URL 相同但内容哈希不同视为“文章已更新”，不拦截。"""
    url = normalize_url(item.get("url", ""))
    text = fingerprint_text(item)
    if not text.strip():
        return None
    chash = content_hash(text)
    for key, entry in (ledger.get("entries") or {}).items():
        if not _entry_is_relevant(entry, current_week, ttl_days):
            continue
        if entry.get("content_hash") != chash:
            continue
        if url and key == url:
            return entry
        # 不同 URL、完全相同的内容：转载/镜像，同样视为已发布
        return entry
    return None


def record_published(items: list[dict], current_week: str | None = None,
                     ttl_days: int = DEFAULT_LEDGER_TTL_DAYS) -> int:
    """把本轮实际发布的条目写入台账；返回写入条数。

    只应在报告与移动版都成功生成后调用。写入失败由调用方记录告警，
    不反过来判定周报失败。
    """
    current_week = current_week or week_key()
    ledger = load_ledger()
    entries = ledger.setdefault("entries", {})
    now = utc_now_naive()
    written = 0

    for item in items:
        url = normalize_url(item.get("url", ""))
        if not url:
            continue
        text = fingerprint_text(item)
        if not text.strip():
            continue
        chash = content_hash(text)
        old = entries.get(url) or {}
        first_seen = old.get("first_seen") if old.get("content_hash") == chash else now.isoformat()
        entries[url] = {
            "content_hash": chash,
            "fingerprint": f"{simhash64(text):016x}",
            "title": (item.get("title") or "")[:300],
            "source_name": item.get("source_name") or "",
            "week": current_week,
            "first_seen": first_seen or now.isoformat(),
            "last_seen": now.isoformat(),
        }
        written += 1

    cutoff = now - timedelta(days=ttl_days)
    kept = {}
    for key, entry in entries.items():
        last_seen = parse_datetime_utc(entry.get("last_seen", ""))
        if last_seen is None or last_seen >= cutoff:
            kept[key] = entry
    if len(kept) > MAX_LEDGER_ENTRIES:
        ordered = sorted(kept.items(),
                         key=lambda kv: kv[1].get("last_seen", ""), reverse=True)
        kept = dict(ordered[:MAX_LEDGER_ENTRIES])

    ledger["entries"] = kept
    ledger["updated_at"] = now.isoformat()
    atomic_write(LEDGER_PATH, ledger, indent=2)
    return written
