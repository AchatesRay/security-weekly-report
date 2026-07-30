import json
from pathlib import Path
from datetime import datetime, timedelta
from rapidfuzz import fuzz

DATA_DIR = Path("data")
CONFIG_PATH = Path("config/settings.json")
PARSED_ITEMS_PATH = DATA_DIR / "parsed_items.json"
DEDUPED_ITEMS_PATH = DATA_DIR / "deduped_items.json"


def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("dedup", {})
    except Exception:
        return {}


SIMILARITY_THRESHOLD = _load_config().get("similarity_threshold", 85)
MAX_DAYS = _load_config().get("max_days", 7)


def normalize_title(title: str) -> str:
    """规范化标题用于相似度比较"""
    title = title.lower().strip()
    # 移除常见前后缀
    prefixes = [
        "cve alert: ", "alert: ", "news: ", "update: ",
        "critical: ", "high: ", "medium: ", "low: ",
    ]
    for p in prefixes:
        if title.startswith(p):
            title = title[len(p):]
            break
    return title.strip()


def filter_by_date(items: list[dict], max_days: int = MAX_DAYS) -> list[dict]:
    """过滤掉超出 max_days 天的旧条目，无日期条目保留"""
    cutoff = datetime.now() - timedelta(days=max_days)
    kept = []
    dropped = 0
    for item in items:
        pub_str = item.get("published_date", "")
        if not pub_str:
            kept.append(item)
            continue
        try:
            pub_date = datetime.fromisoformat(pub_str)
            if pub_date >= cutoff:
                kept.append(item)
            else:
                dropped += 1
        except Exception:
            kept.append(item)
    if dropped:
        print(f"  [FILTER] 过滤掉 {dropped} 条过期内容（>{max_days}天）")
    return kept


def deduplicate(items: list[dict]) -> list[dict]:
    """
    URL 精确去重 + 标题相似度模糊去重。
    合并同类项时保留多信源信息，标记为 merged_source。
    """
    seen_urls: set[str] = set()
    seen_titles: list[tuple[str, dict]] = []
    result = []

    for item in items:
        url = item.get("url", "")
        title = item.get("title", "")

        # URL 精确去重
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)

        # 标题相似度去重
        norm_title = normalize_title(title)
        is_dup = False
        for existing_title, existing_item in seen_titles:
            score = fuzz.token_sort_ratio(norm_title, existing_title)
            if score >= SIMILARITY_THRESHOLD:
                # 合并信源: 记录来自多个信源
                if "merged_sources" not in existing_item:
                    existing_item["merged_sources"] = [existing_item["source_name"]]
                if item["source_name"] not in existing_item["merged_sources"]:
                    existing_item["merged_sources"].append(item["source_name"])
                is_dup = True
                break

        if not is_dup:
            seen_titles.append((norm_title, item))
            result.append(item)

    print(f"[DEDUP] 去重: {len(items)} -> {len(result)} 条")
    return result


def run():
    with open(PARSED_ITEMS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    items = filter_by_date(items)
    result = deduplicate(items)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    from ..utils import atomic_write
    atomic_write(DEDUPED_ITEMS_PATH, result, indent=2)

    return result


if __name__ == "__main__":
    run()
