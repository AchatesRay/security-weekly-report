import feedparser
import json
from pathlib import Path
from datetime import datetime

DATA_DIR = Path("data")
RAW_ITEMS_PATH = DATA_DIR / "raw_items.json"
PARSED_ITEMS_PATH = DATA_DIR / "parsed_items.json"


def parse_entry(source_info: dict, entry) -> dict:
    """将 feedparser 的 entry 转换为统一结构"""
    title = entry.get("title", "").strip()
    link = entry.get("link", "").strip()
    summary = entry.get("summary", entry.get("description", "")).strip()
    published = entry.get("published_parsed", entry.get("updated_parsed", None))

    pub_date = ""
    if published:
        try:
            pub_date = datetime(*published[:6]).isoformat()
        except Exception:
            pub_date = ""

    return {
        "title": title,
        "url": link,
        "summary": summary,
        "published_date": pub_date,
        "source_name": source_info["source_name"],
        "source_level": source_info["source_level"],
        "region": source_info["region"],
        "language": source_info["language"],
        "parse_time": datetime.now().isoformat(),
    }


def parse_api_secrss(source_info: dict, raw_text: str) -> list[dict]:
    """解析 安全内参 API JSON 为统一格式"""
    items = []
    try:
        data = json.loads(raw_text)
        if data.get("code") != "10000":
            return items
        for article in data.get("data", []):
            title = article.get("title", "").strip()
            if not title:
                continue
            link = f"https://www.secrss.com/articles/{article.get('id', '')}"
            summary = article.get("summary", "").strip()
            published = article.get("published_at", "")
            items.append({
                "title": title,
                "url": link,
                "summary": summary,
                "published_date": published,
                "source_name": source_info["source_name"],
                "source_level": source_info["source_level"],
                "region": source_info["region"],
                "language": source_info["language"],
                "parse_time": datetime.now().isoformat(),
            })
    except Exception:
        pass
    return items


# API 解析器注册表: source_name -> parse function
API_PARSERS = {
    "安全内参": parse_api_secrss,
}


def parse_all() -> list[dict]:
    """解析所有已抓取的原始数据 (RSS + API)"""
    with open(RAW_ITEMS_PATH, "r", encoding="utf-8") as f:
        raw_sources = json.load(f)

    all_items = []
    for source in raw_sources:
        if source["error"] or not source["xml_text"]:
            continue

        source_type = source.get("type", "rss")

        # API 类型信源
        if source_type == "api" and source["source_name"] in API_PARSERS:
            parser_fn = API_PARSERS[source["source_name"]]
            try:
                items = parser_fn(source, source["xml_text"])
                all_items.extend(items)
            except Exception as e:
                print(f"  [PARSE ERROR] {source['source_name']} API: {e}")
            continue

        # RSS/Atom 类型
        try:
            feed = feedparser.parse(source["xml_text"])
            for entry in feed.entries:
                try:
                    item = parse_entry(source, entry)
                    all_items.append(item)
                except Exception as e:
                    print(f"  [PARSE ERROR] {source['source_name']}: {e}")
                    all_items.append({
                        "title": entry.get("title", "").strip(),
                        "url": entry.get("link", "").strip(),
                        "summary": "",
                        "published_date": "",
                        "source_name": source["source_name"],
                        "source_level": source["source_level"],
                        "region": source["region"],
                        "language": source["language"],
                        "parse_time": datetime.now().isoformat(),
                        "parse_error": True,
                    })
        except Exception as e:
            print(f"  [PARSE ERROR] {source['source_name']} feed: {e}")

    print(f"[PARSER] 解析完成: {len(all_items)} 条")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(PARSED_ITEMS_PATH, "w", encoding="utf-8") as f:
        json.dump(all_items, f, ensure_ascii=False, indent=2)

    return all_items


if __name__ == "__main__":
    parse_all()
