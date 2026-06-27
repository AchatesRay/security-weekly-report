"""全文提取模块 — 对摘要过短的文章抓取原文内容

管道位置: 解析步骤之后，去重步骤之前
"""

import json
import re
import httpx
from pathlib import Path
from datetime import datetime

DATA_DIR = Path("data")
PARSED_ITEMS_PATH = DATA_DIR / "parsed_items.json"

# 摘要长度阈值：低于此值的文章需要抓取全文
SUMMARY_MIN_LENGTH = 300
# 最大并发请求数
MAX_CONCURRENT = 5
# 请求超时
REQUEST_TIMEOUT = 15.0

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def extract_text_from_html(html_text: str) -> str:
    """从 HTML 中提取纯文本内容"""
    # 移除 script 和 style 块
    text = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', html_text, flags=re.IGNORECASE)
    text = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', text, flags=re.IGNORECASE)
    # 移除 HTML 标签
    text = re.sub(r'<[^>]+>', ' ', text)
    # 解码 HTML 实体
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&#x27;', "'")
    text = re.sub(r'&[a-zA-Z]+;', ' ', text)
    # 合并空白
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def fetch_article(url: str) -> str | None:
    """抓取文章 URL 并提取纯文本"""
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
        # 至少200字符才有意义
        if len(text) < 200:
            return None
        return text[:5000]
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

        fulltext = fetch_article(url)
        if fulltext:
            item["original_summary"] = item.get("summary", "")
            item["summary"] = fulltext
            item["fulltext_fetched"] = True
            fetched_count += 1
        else:
            item["fulltext_fetched"] = False

        if (idx + 1) % 20 == 0:
            print(f"  [FULLTEXT] 进度: {idx+1}/{total}")

    # 写回 parsed_items.json（后续步骤从中读取更新后的数据）
    with open(PARSED_ITEMS_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    print(f"[FULLTEXT] 全文提取完成: {fetched_count}/{total} 条获取到全文")


if __name__ == "__main__":
    run()
