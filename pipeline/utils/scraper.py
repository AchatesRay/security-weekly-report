"""
HTTP 爬虫 — 用于解析不支持 RSS/API 的信源的 HTML 页面

支持两种模式:
  1. 列表页模式: 给定列表页 URL，提取文章链接列表
  2. 单页模式: 给定要素选择器，从页面中提取内容

用法:
    python scraper.py --url https://example.com/news
"""

import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from . import UnsafeURLError, safe_get


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def _resolve_link(href: str, base: str) -> str:
    """把页面里的链接补全为绝对地址。

    2026-09-29 修复：此前只在链接以 "/" 开头时才拼接 link_base，
    形如 "thread-293107.htm" 这种**不带斜杠的相对链接**被原样保留，
    结果周报里出现点不开的死链（实测看雪论坛 10 条全部如此）。
    现改用 urljoin 统一处理全部相对形式：
      绝对地址 / 协议相对 "//host/x" / 站根相对 "/x" / 普通相对 "x" / "./x" / "../x"

    返回空串表示链接不可用（页内锚点、javascript:/mailto: 等非 http 链接）。
    """
    if not href:
        return ""
    href = href.strip()
    if not href or href.startswith("#"):
        return ""
    scheme = urlsplit(href).scheme.lower()
    if scheme and scheme not in ("http", "https"):
        return ""
    try:
        absolute = urljoin(base, href) if base else href
    except ValueError:
        return ""
    if not absolute.lower().startswith(("http://", "https://")):
        return ""
    return absolute


def extract_articles(source: dict, html_text: str) -> list[dict]:
    """从 HTML 页面中提取文章列表，返回与 parser.parse_entry() 兼容的格式

    Args:
        source: 信源配置，支持以下可选字段:
            scraper_config:
                article_selector: 文章容器的 CSS 选择器，默认 'article'
                title_selector: 标题元素选择器，默认 'h2 a, h3 a, .entry-title a'
                summary_selector: 摘要元素选择器，默认 'p, .summary, .excerpt, .description'
                date_selector: 日期元素选择器，默认 'time, .date, .published, .post-date'
                link_selector: 如果文章链接与标题分离，单独指定链接选择器
                link_base: 相对链接的基础 URL
        html_text: 页面的原始 HTML
    """
    cfg = source.get("scraper_config", {})
    soup = BeautifulSoup(html_text, "lxml")

    article_selector = cfg.get("article_selector", "article")
    title_selector = cfg.get("title_selector", "h2 a, h3 a, .entry-title a")
    summary_selector = cfg.get("summary_selector", "p, .summary, .excerpt, .description")
    date_selector = cfg.get("date_selector", "time, .date, .published, .post-date")
    link_selector = cfg.get("link_selector", "")
    link_base = cfg.get("link_base", "")
    # 链接补全基准：显式配置的 link_base 优先，否则用被抓取页面的地址
    base_url = link_base or source.get("url", "") or ""

    items = []
    articles = soup.select(article_selector) if article_selector else [soup]

    for art in articles:
        if not isinstance(art, Tag):
            continue

        # 提取标题和链接
        title_el = art.select_one(title_selector) if title_selector else None
        if not title_el:
            continue

        title = title_el.get_text(strip=True)
        if not title or len(title) < 4:
            continue

        # 提取链接
        link = ""
        if link_selector:
            link_el = art.select_one(link_selector)
        else:
            link_el = title_el if title_el.name == "a" else title_el.find("a")
        # 回退：如果 article 本身就是 <a> 且未找到内部链接
        if not (link_el and link_el.name == "a" and link_el.get("href")):
            link_el = art if art.name == "a" and art.get("href") else link_el
        # 回退：如果 article 的父节点是 <a>
        if not (link_el and link_el.name == "a" and link_el.get("href")):
            parent = art.parent
            if parent and parent.name == "a" and parent.get("href"):
                link_el = parent
        if link_el and link_el.name == "a" and link_el.get("href"):
            link = _resolve_link(link_el["href"], base_url)

        # 提取摘要
        summary = ""
        summary_el = art.select_one(summary_selector) if summary_selector else None
        if summary_el:
            summary = summary_el.get_text(strip=True)
        if not summary:
            # 回退：取第一个非空的 p
            for p in art.find_all("p"):
                txt = p.get_text(strip=True)
                if len(txt) > 20:
                    summary = txt
                    break

        # 提取日期
        published_date = ""
        date_el = art.select_one(date_selector) if date_selector else None
        if date_el:
            date_text = date_el.get("datetime", "") or date_el.get_text(strip=True)
            if date_text:
                published_date = date_text

        items.append({
            "title": title,
            "url": link,
            "summary": summary[:2000] if summary else "",
            "published_date": published_date,
            "source_name": source.get("source_name") or source["name"],
            "language": source.get("language", "en"),
            "parse_time": datetime.now().isoformat(),
        })

    return items


def fetch_and_extract(source: dict) -> list[dict]:
    """抓取并提取文章的便捷方法

    Args:
        source: 信源配置，必须包含 url 字段

    注意: 抓取统一走 safe_get()（仅公网 http/https，逐跳校验重定向，
    并限制响应体积），避免配置里的地址指向内网服务。
    """
    url = source.get("url", "")
    if not url:
        print(f"  [SCRAPER ERROR] {source.get('name', '?')}: 缺少 url")
        return []

    try:
        resp = safe_get(url, headers={"User-Agent": USER_AGENT}, timeout=30.0,
                        max_bytes=5 * 1024 * 1024)
        items = extract_articles(source, resp.text)
        print(f"  [SCRAPER] {source.get('name', '?')}: 提取 {len(items)} 条")
        return items
    except UnsafeURLError as e:
        print(f"  [SCRAPER BLOCKED] {source.get('name', '?')}: 地址被安全策略拦截 ({e})")
        return []
    except Exception as e:
        print(f"  [SCRAPER ERROR] {source.get('name', '?')}: {e}")
        return []


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="HTTP 爬虫工具")
    parser.add_argument("--url", required=True, help="要抓取的页面 URL")
    parser.add_argument("--selector", default="article", help="文章容器 CSS 选择器")
    args = parser.parse_args()

    source = {
        "name": "test",
        "url": args.url,
        "scraper_config": {"article_selector": args.selector},
    }
    items = fetch_and_extract(source)
    print(f"\n共提取 {len(items)} 条:")
    for item in items[:10]:
        print(f"  - {item['title']}")
        print(f"    {item['url']}")
        if item['summary']:
            print(f"    {item['summary'][:100]}...")
