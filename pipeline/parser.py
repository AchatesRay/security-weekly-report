import feedparser
import html
import json
import re
from pathlib import Path
from datetime import datetime
from email.utils import parsedate_to_datetime

DATA_DIR = Path("data")
RAW_ITEMS_PATH = DATA_DIR / "raw_items.json"
PARSED_ITEMS_PATH = DATA_DIR / "parsed_items.json"


def strip_html(html_text: str) -> str:
    """去除 HTML 标签，解码 HTML 实体，保留纯文本（保留段落边界）"""
    text = html_text
    # 块级标签 → 换行
    for tag in ['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                'li', 'tr', 'td', 'th', 'blockquote', 'pre', 'dl', 'dt', 'dd']:
        text = re.sub(rf'</{tag}>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<hr\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def extract_body(entry) -> str:
    """提取条目正文：优先使用完整正文 <content>，否则回退 <summary>/<description>"""
    content_list = entry.get("content", [])
    if content_list and isinstance(content_list, list) and len(content_list) > 0:
        raw = content_list[0].get("value", "") if hasattr(content_list[0], "get") else str(content_list[0])
        raw = raw.strip()
        if raw:
            return strip_html(raw)
    return strip_html(entry.get("summary", entry.get("description", "")).strip())


TYPE_LABEL = {"rss": "RSS", "api": "API", "scraper": "HTTP"}


def parse_entry(source_info: dict, entry) -> dict:
    """将 feedparser 的 entry 转换为统一结构"""
    title = entry.get("title", "").strip()
    link = entry.get("link", "").strip()
    summary = extract_body(entry)

    # 尝试 feedparser 解析；失败时用 email.utils 回退
    pub_date = ""
    published = entry.get("published_parsed", entry.get("updated_parsed", None))
    if published:
        try:
            pub_date = datetime(*published[:6]).isoformat()
        except Exception:
            pub_date = ""
    if not pub_date:
        raw = entry.get("published", entry.get("updated", ""))
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                pub_date = dt.isoformat()
            except Exception:
                pass

    return {
        "title": title,
        "url": link,
        "summary": summary,
        "published_date": pub_date,
        "source_name": source_info["source_name"],
        "source_level": source_info["source_level"],
        "region": source_info["region"],
        "language": source_info["language"],
        "source_type": TYPE_LABEL.get(source_info.get("type", "rss"), "RSS"),
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
                "source_type": TYPE_LABEL.get(source_info.get("type", "api"), "API"),
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

        # HTTP 爬虫类型
        if source_type == "scraper":
            from .scraper import extract_articles
            try:
                items = extract_articles(source, source["xml_text"])
                all_items.extend(items)
            except Exception as e:
                print(f"  [PARSE ERROR] {source['source_name']} scraper: {e}")
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
                        "source_type": TYPE_LABEL.get(source.get("type", "rss"), "RSS"),
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
