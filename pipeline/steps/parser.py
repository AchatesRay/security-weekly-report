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

    # 摘要 = <description>/<summary>（短文本）
    summary = strip_html(entry.get("summary", entry.get("description", "")).strip())

    # 正文 = <content:encoded>（若有，否则与摘要同）
    full_body = ""
    content_list = entry.get("content", [])
    if content_list and isinstance(content_list, list) and len(content_list) > 0:
        raw = content_list[0].get("value", "") if hasattr(content_list[0], "get") else str(content_list[0])
        raw = raw.strip()
        if raw:
            full_body = strip_html(raw)

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

    result = {
        "title": title,
        "url": link,
        "summary": summary,
        "published_date": pub_date,
        "source_name": source_info["source_name"],
        "language": source_info["language"],
        "source_type": TYPE_LABEL.get(source_info.get("type", "rss"), "RSS"),
        "parse_time": datetime.now().isoformat(),
    }

    # content:encoded 与 description 不同时存为 full_body
    if full_body and full_body != summary:
        result["full_body"] = full_body

    return result


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
                "language": source_info["language"],
                "source_type": TYPE_LABEL.get(source_info.get("type", "api"), "API"),
                "parse_time": datetime.now().isoformat(),
            })
    except Exception:
        pass
    return items


def parse_api_arxiv(source_info: dict, raw_text: str) -> list[dict]:
    """解析 arXiv API 返回的 Atom XML 为统一格式"""
    try:
        feed = feedparser.parse(raw_text)
    except Exception:
        return []
    items = []
    for entry in feed.entries:
        title = entry.get("title", "").strip()
        if not title:
            continue
        link = entry.get("link", "").strip()
        summary = entry.get("summary", "").strip()
        published = entry.get("published", "")
        authors = [a.get("name", "") for a in entry.get("authors", []) if a.get("name")]
        categories = [c.get("term", "") for c in entry.get("tags", []) if c.get("term")]
        items.append({
            "title": title,
            "url": link,
            "summary": summary,
            "published_date": published,
            "source_name": source_info["source_name"],
            "language": source_info["language"],
            "source_type": TYPE_LABEL.get(source_info.get("type", "api"), "API"),
            "authors": authors,
            "categories": categories,
            "parse_time": datetime.now().isoformat(),
        })
    return items


def parse_api_semantic_scholar(source_info: dict, raw_text: str) -> list[dict]:
    """解析 Semantic Scholar API 返回的 JSON"""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return []
    items = []
    for paper in data.get("data", []):
        title = paper.get("title", "").strip()
        if not title:
            continue
        link = paper.get("url", "")
        summary = paper.get("abstract", "") or paper.get("title", "")
        published = paper.get("publicationDate", "")
        authors = [a.get("name", "") for a in paper.get("authors", []) if a.get("name")]
        items.append({
            "title": title,
            "url": link,
            "summary": summary,
            "published_date": published,
            "source_name": source_info["source_name"],
            "language": source_info["language"],
            "source_type": TYPE_LABEL.get(source_info.get("type", "api"), "API"),
            "authors": authors,
            "categories": [],
            "parse_time": datetime.now().isoformat(),
        })
    return items


def parse_api_ietf(source_info: dict, raw_text: str) -> list[dict]:
    """解析 IETF Datatracker API 返回的 JSON"""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return []
    items = []
    for doc in data.get("objects", []):
        title = doc.get("title", "").strip()
        if not title:
            continue
        name = doc.get("name", "")
        link = f"https://datatracker.ietf.org/doc/{name}/" if name else ""
        summary_parts = [f"状态: {doc.get('state', '未知')}"]
        if doc.get("intended_std_level"):
            summary_parts.append(f"标准级别: {doc['intended_std_level']}")
        if doc.get("group"):
            summary_parts.append(f"工作组: {doc['group']}")
        summary = " | ".join(summary_parts)
        published = doc.get("time", "")
        items.append({
            "title": f"{name} - {title}" if name else title,
            "url": link,
            "summary": summary,
            "published_date": published,
            "source_name": source_info["source_name"],
            "language": source_info["language"],
            "source_type": TYPE_LABEL.get(source_info.get("type", "api"), "API"),
            "authors": [],
            "categories": ["IETF", "标准草案"],
            "parse_time": datetime.now().isoformat(),
        })
    return items


def parse_api_mitre_attack(source_info: dict, raw_text: str) -> list[dict]:
    """解析 MITRE ATT&CK STIX JSON"""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return []
    items = []
    seen_names = set()
    for obj in data.get("objects", []):
        if obj.get("type") not in ("attack-pattern", "malware", "tool", "intrusion-set", "campaign"):
            continue
        name = obj.get("name", "").strip()
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        description = obj.get("description", "")
        summary = description[:500] if description else name
        ext_refs = obj.get("external_references", [])
        link = ""
        for ref in ext_refs:
            if ref.get("source_name") == "mitre-attack" and ref.get("url"):
                link = ref["url"]
                break
        modified = obj.get("modified", "")
        items.append({
            "title": f"[MITRE {obj['type']}] {name}",
            "url": link,
            "summary": summary,
            "published_date": modified,
            "source_name": source_info["source_name"],
            "language": source_info["language"],
            "source_type": TYPE_LABEL.get(source_info.get("type", "api"), "API"),
            "authors": [],
            "categories": [obj.get("type", ""), "MITRE ATT&CK"],
            "parse_time": datetime.now().isoformat(),
        })
    return items


def parse_api_github(source_info: dict, raw_text: str) -> list[dict]:
    """解析 GitHub API 返回的仓库搜索 JSON"""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return []
    items = []
    for repo in data.get("items", []):
        full_name = repo.get("full_name", "").strip()
        if not full_name:
            continue
        description = repo.get("description", "") or ""
        topics = repo.get("topics", [])
        stars = repo.get("stargazers_count", 0)
        language = repo.get("language") or "未知"
        summary = description[:500] if description else full_name
        summary += f" | stars: {stars} | 语言: {language}"
        if topics:
            summary += f" | 标签: {', '.join(topics[:5])}"
        items.append({
            "title": full_name,
            "url": repo.get("html_url", f"https://github.com/{full_name}"),
            "summary": summary,
            "published_date": repo.get("created_at", ""),
            "source_name": source_info["source_name"],
            "language": source_info["language"],
            "source_type": TYPE_LABEL.get(source_info.get("type", "api"), "API"),
            "authors": [repo.get("owner", {}).get("login", "")] if repo.get("owner") else [],
            "categories": topics[:5],
            "parse_time": datetime.now().isoformat(),
        })
    return items


# API 解析器注册表: source_name -> parse function
API_PARSERS = {
    "安全内参": parse_api_secrss,
    "arXiv cs.CR": parse_api_arxiv,
    "Semantic Scholar": parse_api_semantic_scholar,
    "IETF Datatracker": parse_api_ietf,
    "MITRE ATT&CK": parse_api_mitre_attack,
    "GitHub Security Trending": parse_api_github,
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
            from ..utils.scraper import extract_articles
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
                        "language": source["language"],
                        "source_type": TYPE_LABEL.get(source.get("type", "rss"), "RSS"),
                        "parse_time": datetime.now().isoformat(),
                        "parse_error": True,
                    })
        except Exception as e:
            print(f"  [PARSE ERROR] {source['source_name']} feed: {e}")

    print(f"[PARSER] 解析完成: {len(all_items)} 条")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    from ..utils import atomic_write
    atomic_write(PARSED_ITEMS_PATH, all_items, indent=2)

    return all_items


if __name__ == "__main__":
    parse_all()
