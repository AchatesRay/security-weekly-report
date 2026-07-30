"""全文提取模块 — 对摘要过短的文章抓取原文内容

管道位置: 评分阶段1之后，评分阶段2之前

流程:
  1. 对摘要 <300 字的文章，用 httpx 抓取原文 HTML
  2. BS4 解析 + 启发式去噪 + 正文定位，提取纯文本
  3. 存入 summary（供右栏显示），原摘要备份到 original_summary
  4. 后续 llm_processor 从 summary 中抽取摘要到 ai_summary
"""

import json
import re
import httpx
from pathlib import Path

from bs4 import BeautifulSoup

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

# ── 正文清洗配置 ──────────────────────────────────────────────

# 需要从 DOM 中移除的非正文元素（CSS 选择器）
_BOILERPLATE_SELECTORS = [
    'nav', 'footer', 'header', 'aside',
    '.nav', '.navbar', '.navigation', '.menu', '.topbar', '.header-bar', '.top-nav',
    '.footer', '.foot', '.bottom-bar', '.footer-content', '.site-footer',
    '.sidebar', '.side-bar', '.aside', '.sidepanel', '.right-rail', '.left-rail',
    '.ad', '.ads', '.advertisement', '.advert', '.banner', '.banner-ad', '.sponsored',
    '.social-share', '.share-buttons', '.social-media', '.social-links',
    '.comments', '.comment', '#comments', '.comment-section', '.comment-list', '.comment-form',
    '.related-posts', '.related-articles', '.recommendations', '.suggestions',
    '.popular-posts', '.trending', '.most-read', '.must-read', '.you-may-like',
    '.newsletter', '.subscribe', '.signup', '.mailing-list', '.email-signup',
    '.cookie', '.cookie-banner', '.cookie-consent', '.gdpr', '.notice-consent',
    '.popup', '.modal', '.overlay', '.lightbox', '.dialog',
    '.breadcrumb', '.breadcrumbs',
    '.pagination', '.page-nav', '.page-navigation',
    '.skip-link', '.visually-hidden', '.sr-only', '.screen-reader', '.hidden',
    '.copyright', '.legal', '.disclaimer', '.terms', '.privacy',
    '.search-box', '.search-form', '.search-bar',
    '.widget', '.widget-area', '.widget-title',
    '.author-bio', '.author-info', '.byline',
    '.tag-cloud', '.post-tags', '.category-list',
    '.post-navigation', '.prev-post', '.next-post',
    '#sidebar', '#nav', '#navigation', '#footer', '#comments',
    'script', 'style', 'noscript',
]


def _remove_boilerplate_elements(soup: BeautifulSoup):
    """从 DOM 中移除明显不属于正文的元素"""
    for selector in _BOILERPLATE_SELECTORS:
        for elem in soup.select(selector):
            elem.decompose()


def _has_high_link_density(tag) -> bool:
    """检查元素的链接文本占比是否过高（>=40% 表示可能是导航/索引页）"""
    links = tag.find_all('a')
    if not links:
        return False
    link_text_len = sum(len(a.get_text(strip=True)) for a in links)
    total_text_len = len(tag.get_text(strip=True))
    if total_text_len == 0:
        return True
    return link_text_len / total_text_len >= 0.40


def _find_main_content(soup: BeautifulSoup) -> str | None:
    """启发式定位正文区域，返回纯文本"""
    # 策略 1: 语义标签
    main_candidate = (
        soup.find('article')
        or soup.find('main')
        or soup.select_one('[role="main"]')
        or soup.select_one('[itemprop="articleBody"]')
        or soup.select_one('[itemprop="mainEntity"]')
    )
    if main_candidate:
        return main_candidate.get_text(separator='\n')

    # 策略 2: 对 body 下容器按文本密度评分
    body = soup.find('body') or soup

    scored = []
    for tag in body.find_all(['div', 'section', 'article']):
        text = tag.get_text(separator='\n').strip()
        if len(text) < 200:
            continue

        html_raw_len = len(str(tag))
        if html_raw_len == 0:
            continue

        # 文本密度 = 可见文本 / 原始 HTML 长度
        density = len(text) / html_raw_len

        # 链接密度惩罚
        if _has_high_link_density(tag):
            continue

        # 评分: 文本量 × 密度²（优先选又长又密的块）
        score = len(text) * density ** 2
        scored.append((score, text))

    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    return None


# ── 行级噪音模式（提取后逐行过滤） ──

_LINE_NOISE_PATTERNS = [
    re.compile(r'^Copyright\s+(?:©\s*)?\d{4}', re.IGNORECASE),
    re.compile(r'^All\s+(?:rights\s+)?reserved\.?\s*', re.IGNORECASE),
    re.compile(r'^Privacy\s+(?:Policy|Statement)', re.IGNORECASE),
    re.compile(r'^Terms\s+of\s+(?:Service|Use)', re.IGNORECASE),
    re.compile(r'^Contact\s+Us$', re.IGNORECASE),
    re.compile(r'^Follow\s+us\s+on\s', re.IGNORECASE),
    re.compile(r'^Subscribe\s+to\s+(?:our\s+)?newsletter', re.IGNORECASE),
    re.compile(r'^阅读原文$'),
    re.compile(r'^(返回|查看|阅读) (原文|全部|更多)'),
    re.compile(r'^(分享|收藏|点赞|评论|转发)'),
]


def _clean_noise_lines(text: str) -> str:
    """逐行过滤残余噪音"""
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append('')
            continue
        # 跳过噪音模式
        if any(p.search(stripped) for p in _LINE_NOISE_PATTERNS):
            continue
        cleaned.append(stripped)

    text = '\n'.join(cleaned)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _decode_html_entities(text: str) -> str:
    """解码 HTML 实体"""
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&#x27;', "'")
    text = re.sub(r'&[a-zA-Z]+;', ' ', text)
    return text


def _fallback_extract_text(html_text: str) -> str:
    """回退策略：正则提取纯文本（与重构前一致）"""
    text = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', html_text, flags=re.IGNORECASE)
    text = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', text, flags=re.IGNORECASE)
    for tag in ['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                'li', 'tr', 'td', 'th', 'blockquote', 'pre', 'dl', 'dt', 'dd']:
        text = re.sub(rf'</{tag}>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<hr\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = _decode_html_entities(text)
    return text


def extract_text_from_html(html_text: str) -> str:
    """从 HTML 中提取正文，自动去除导航/广告/版权等噪音

    三层策略:
      1. DOM 级: 移除已知非正文元素（nav, .sidebar, .ad 等）
      2. 内容定位: 语义标签优先，否则按文本密度评分
      3. 行级: 过滤残余噪音模式
    """
    if not html_text:
        return ""

    # 第一层: BS4 解析 + 去噪 + 正文定位
    try:
        soup = BeautifulSoup(html_text, 'lxml')
        _remove_boilerplate_elements(soup)
        text = _find_main_content(soup) or soup.get_text(separator='\n')
    except Exception:
        text = _fallback_extract_text(html_text)

    if not text:
        return ""

    # 统一解码 HTML 实体
    text = _decode_html_entities(text)

    # 合并空白
    text = re.sub(r'[ \t]+', ' ', text)

    # 第三层: 行级噪音过滤
    text = _clean_noise_lines(text)

    return text


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
        # 已有正文（content:encoded）→ 无需 HTTP 抓取
        if item.get("full_body"):
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
    from ..utils import atomic_write
    atomic_write(PARSED_ITEMS_PATH, items, indent=2)

    print(f"[FULLTEXT] 全文提取完成: {fetched_count}/{total} 条获取到正文")


if __name__ == "__main__":
    run()
