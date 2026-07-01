"""全文提取模块 — 对摘要过短的文章抓取原文内容

管道位置: 评分阶段1之后，评分阶段2之前

流程:
  1. 对摘要 <300 字的文章，用 httpx 抓取原文 HTML
  2. 提取纯文本后存入 summary（供右栏显示），原摘要备份到 original_summary
  3. 后续 llm_processor 从 summary 中抽取摘要到 ai_summary
"""

import json
import re
import httpx
from pathlib import Path

DATA_DIR = Path("data")
PARSED_ITEMS_PATH = DATA_DIR / "parsed_items.json"

# 摘要长度阈值：低于此值的文章需要抓取全文
SUMMARY_MIN_LENGTH = 300
# 请求超时
REQUEST_TIMEOUT = 15.0
# 正文最大长度（远超摘要，足够右栏显示）
MAX_BODY_LENGTH = 20000

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def extract_text_from_html(html_text: str) -> str:
    """从 HTML 中提取纯文本内容（保留段落边界）"""
    # 移除 script 和 style 块
    text = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', html_text, flags=re.IGNORECASE)
    text = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', text, flags=re.IGNORECASE)
    # 块级标签 → 换行（保留段落结构）
    for tag in ['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                'li', 'tr', 'td', 'th', 'blockquote', 'pre', 'dl', 'dt', 'dd']:
        text = re.sub(rf'</{tag}>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<hr\s*/?>', '\n', text, flags=re.IGNORECASE)
    # 移除剩余 HTML 标签
    text = re.sub(r'<[^>]+>', '', text)
    # 解码 HTML 实体
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&#x27;', "'")
    text = re.sub(r'&[a-zA-Z]+;', ' ', text)
    # 合并空白（保留换行）
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def fetch_article_text(url: str) -> str | None:
    """抓取文章 URL 并提取纯文本（供右栏详情显示）"""
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "html" not in content_type.lower():
            return None
        text = extract_text_from_html(resp.text)
        if len(text) < 200:
            return None
        return text[:MAX_BODY_LENGTH]
    except Exception:
        return None


def run():
    with open(PARSED_ITEMS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    total = len(items)
    fetched_count = 0

    for idx, item in enumerate(items):
        summary = item.get("summary", "")
        url = item.get("url", "")
        # 仅对摘要过短且有 URL 的条目抓取
        if len(summary) >= SUMMARY_MIN_LENGTH or not url:
            continue
        if not url.startswith("http"):
            continue

        body = fetch_article_text(url)
        if body:
            item["original_summary"] = item.get("summary", "")
            item["summary"] = body
            item["fulltext_fetched"] = True
            fetched_count += 1
        else:
            item["fulltext_fetched"] = False

        if (idx + 1) % 20 == 0:
            print(f"  [FULLTEXT] 进度: {idx+1}/{total}")

    # 写回 parsed_items.json（后续步骤从中读取更新后的数据）
    from . import atomic_write
    atomic_write(PARSED_ITEMS_PATH, items, indent=2)

    print(f"[FULLTEXT] 全文提取完成: {fetched_count}/{total} 条获取到正文")


if __name__ == "__main__":
    run()
