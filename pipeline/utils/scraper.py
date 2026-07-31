"""
HTTP 爬虫 — 用于解析不支持 RSS/API 的信源的 HTML 页面

支持两种模式:
  1. 列表页模式: 给定列表页 URL，提取文章链接列表
  2. 单页模式: 给定要素选择器，从页面中提取内容

用法:
    python scraper.py --url https://example.com/news
"""

import re
import httpx
from datetime import datetime
from bs4 import BeautifulSoup, Tag


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


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
            link = link_el["href"]
            if link.startswith("/") and link_base:
                link = link_base.rstrip("/") + link

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
    """
    url = source.get("url", "")
    if not url:
        print(f"  [SCRAPER ERROR] {source.get('name', '?')}: 缺少 url")
        return []

    try:
        resp = httpx.get(url, headers={"User-Agent": USER_AGENT},
                         timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        items = extract_articles(source, resp.text)
        print(f"  [SCRAPER] {source.get('name', '?')}: 提取 {len(items)} 条")
        return items
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
