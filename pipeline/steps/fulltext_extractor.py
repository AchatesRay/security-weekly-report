"""全文提取模块 — 对摘要过短的文章抓取原文内容，并清洗出真正的正文

管道位置: 评分阶段1之后，评分阶段2之前

流程:
  1. 对摘要 <300 字的文章，抓取原文 HTML
  2. BS4 解析 + 定位正文容器 + 去模板 + 行级过滤，提取纯文本
  3. 存入 summary（供右栏显示），原摘要备份到 original_summary
  4. 后续 llm_processor 从 summary 中抽取摘要到 ai_summary

2026-09-29 修复：
  1. **SSRF 防护**：此前对 RSS 条目里的任意 URL 直接 httpx.get，且
     follow_redirects=True。条目链接由外部内容方控制，可指向 127.0.0.1、
     内网服务或云元数据地址，抓回的内容会写进周报正文，形成“内网探测 +
     内容外带”通道，还能把管理后台的配置读进公开周报。现在统一走
     utils.safe_get()：仅允许公网 http/https，起始地址与**每一次重定向**
     都校验，并限制响应体积。
  2. **串行抓取**：原本一条一条同步抓取、每条 15 秒超时，数百条时耗时可达
     数十分钟。改为有界线程池并发。
  3. **实体二次解码**：BS4 的 get_text 已经解码过 HTML 实体，代码又解码一次，
     遇到 "&amp;lt;" 这类内容会被二次解码成 "<"。现在只在正则回退路径解码。

2026-10-08 正文清洗优化（实测基准：102 篇真实抓回的网页）：
  1. **块级换行**：此前用 get_text(separator="\n")，它在**每个文本节点**之间
     都插换行，加粗/链接/行内代码会把一句话切成十几段（实测某篇 621 行里只有
     约 270 句）。周报正文按换行分段，读者看到的就是碎句。现改为只在块级标签
     边界换行。实测平均行长 50→78 字，噪音行 1457→345，耗时 20.7s→7.4s。
  2. **正文保护（根治性）**：此前"先删模板、再找正文"，规则一旦命中正文的
     祖先就整页删光（实测 Unit42 因 [class*="sidebar"] 命中 WordPress
     <body class="...no-sidebar...">，47,518 字 → 363 字）。现改为**先定位
     正文容器、再删模板**，删除任何元素前跳过正文容器及其所有祖先——
     将来再加选择器也不会重演这类事故。
  3. **正文定位**：此前取第一个 <article> 就返回，页面里有多个文章小卡片时会
     抓错；改为候选容器全部参评，取"文本量达最大值 90% 以上者中最紧凑的"。
  4. **安全边界**：不删 [class*="sidebar"]、[aria-hidden="true"]、textarea
     —— 三者都实测误伤过正文（详见 §5 的决策表）。
  5. **模板选择器**：70 → 155 条，且改为单遍遍历 + 属性判断（逐个 select 会把
     整棵树扫 120 遍，占清洗总耗时 80%）。选择器列表仍是唯一事实来源。
  6. **行级过滤**：11 → 60 余条，分"任意长度"与"仅短行（≤60 字）"两档；
     新增纯符号行、裸域名、时间元信息、零宽字符清理、同页重复行收敛，
     以及**同一行内连续重复短语**的收敛（论坛登录墙提示会在一行里重复十遍）。
"""

import html
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup, NavigableString, Tag

from ..utils import DATA_DIR, UnsafeURLError, atomic_write, safe_get

PARSED_ITEMS_PATH = DATA_DIR / "parsed_items.json"

SUMMARY_MIN_LENGTH = 300
REQUEST_TIMEOUT = 15.0
MAX_BODY_LENGTH = 20000
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_WORKERS = 8

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# ── 块级标签：只有这些标签边界才产生换行 ──────────────────────
_BLOCK_TAGS = frozenset({
    'p', 'div', 'section', 'article', 'main', 'header', 'footer', 'aside', 'nav',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'li', 'ul', 'ol', 'dl', 'dt', 'dd',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th', 'caption',
    'blockquote', 'figure', 'figcaption', 'hr', 'br',
    'fieldset', 'details', 'summary', 'address', 'form',
})
# 内容直接丢弃的标签（不可能承载正文）
_DROP_TAGS = frozenset({'script', 'style', 'noscript', 'template', 'svg', 'canvas'})

# ── 语义正文容器 ──────────────────────────────────────────────
_SEMANTIC_SELECTORS = (
    'article', 'main', '[role="main"]',
    '[itemprop="articleBody"]', '[itemprop="mainEntity"]',
)

# ── 正文类名提示（第 2 层候选） ────────────────────────────────
_CONTENT_SELECTORS = (
    '[class*="article-body"]', '[class*="articleBody"]',
    '[class*="article-content"]', '[class*="article__content"]', '[class*="article-text"]',
    '[class*="post-content"]', '[class*="post-body"]', '[class*="post__content"]',
    '[class*="entry-content"]', '[class*="entry_text"]', '[class*="entry-text"]',
    '[class*="story-body"]', '[class*="story-content"]',
    '[class*="content-body"]', '[class*="markdown-body"]', '[class*="rich_media_content"]',
    '[class*="blog-content"]', '[class*="blog-post-content"]',
    '[class*="detail-content"]', '[class*="news-content"]', '[class*="art-content"]',
    '[id*="article-body"]', '[id*="article-content"]', '[id*="post-content"]',
    '[id*="entry-content"]', '[id*="main-content"]', '[id*="content-main"]',
)

# ── 需要从 DOM 中移除的非正文元素（CSS 选择器） ─────────────────
# 注意（踩过的坑）：
#   * 不用 [class*="sidebar"]：WordPress 会给 <body> 加 "no-sidebar"、
#     CrowdStrike 用 "container-wp--no-sidebar"，命中后整页正文被删
#   * 不用 [class*="search"] / [class*="comment"]：会误匹配 "research" / "commentary"
#   * 不删 textarea：Discuz（看雪论坛）的正文就存在隐藏 textarea 里
_BOILERPLATE_SELECTORS = [
    'nav', 'footer', 'aside',
    '.nav', '.navbar', '.navigation', '.menu', '.topbar', '.header-bar', '.top-nav',
    '.footer', '.foot', '.bottom-bar', '.footer-content', '.site-footer',
    '.sidebar', '.side-bar', '.aside', '.sidepanel', '.right-rail', '.left-rail',
    '.ad', '.ads', '.advertisement', '.advert', '.banner', '.banner-ad', '.sponsored',
    '.social-share', '.share-buttons', '.social-media', '.social-links',
    '.comments', '.comment', '#comments', '.comment-section', '.comment-list',
    '.comment-form', '.comment-respond', '.comment-content',
    '.related-posts', '.related-articles', '.recommendations', '.suggestions',
    '.popular-posts', '.trending', '.most-read', '.must-read', '.you-may-like',
    '.newsletter', '.subscribe', '.signup', '.mailing-list', '.email-signup',
    '.cookie', '.cookie-banner', '.cookie-consent', '.gdpr', '.notice-consent',
    '.popup', '.modal', '.overlay', '.lightbox', '.dialog',
    '.breadcrumb', '.breadcrumbs',
    '.pagination', '.page-nav', '.page-navigation',
    '.skip-link', '.visually-hidden', '.sr-only', '.screen-reader', '.hidden',
    '.copyright', '.legal', '.disclaimer', '.terms', '.privacy',
    '.search-box', '.search-form', '.search-bar', '.search-input',
    '.widget', '.widget-area', '.widget-title',
    '.author-bio', '.author-info', '.byline',
    '.tag-cloud', '.post-tags', '.category-list',
    '.post-navigation', '.prev-post', '.next-post',
    '#sidebar', '#nav', '#navigation', '#footer', '#comments', '#search',
    # ── 新增：表单与交互控件（textarea 见上方说明，刻意不删） ──
    'form', 'button', 'input', 'select', 'iframe',
    # ── 新增：角色属性（不删 [aria-hidden="true"]：轮播/选项卡会把
    #    非当前面板整块标记为 aria-hidden，实测删掉过 2,000 字客户证言正文） ──
    '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
    '[role="complementary"]', '[role="search"]', '[role="dialog"]',
    # ── 新增：类名通配（已经正文保护，不会连带删掉正文容器） ──
    '[class*="advert"]', '[id*="advert"]',
    '[class*="banner"]', '[class*="cookie"]', '[class*="consent"]',
    '[class*="newsletter"]', '[class*="subscribe"]',
    '[class*="share"]', '[class*="social"]',
    '[class*="related"]', '[class*="recommend"]',
    '[class*="popular"]', '[class*="promo"]', '[class*="sponsor"]',
    '[class*="breadcrumb"]', '[class*="pagination"]',
    '[class*="login"]', '[class*="signin"]', '[class*="user-menu"]',
    '[class*="toolbar"]', '[class*="dropdown"]', '[class*="mega-menu"]',
    '[class*="post-meta"]', '[class*="entry-meta"]', '[class*="article-meta"]',
    '[class*="tag-list"]', '[class*="post-tags"]', '[class*="tags-"]',
    '[class*="read-more"]', '[class*="more-link"]',
    '[class*="byline"]', '[class*="author"]',
    '[class*="rating"]', '[class*="vote"]', '[class*="reaction"]',
    '[class*="site-header"]', '[class*="main-nav"]', '[class*="primary-menu"]',
    '[class*="menu-item"]', '[class*="site-nav"]',
    'script', 'style', 'noscript', 'template',
]

def _compile_selector_rules(selectors):
    """把选择器列表编译成"单遍遍历 + 属性判断"所需的结构。

    本项目用到的选择器全是简单形态（tag / .class / #id / [role=..] /
    [class*=..] / [id*=..]），没必要为它们跑 CSS 引擎。把同一个列表解析成
    集合与子串表后，一次 find_all(True) 即可判定全部模式，实测比逐个
    select 快一个数量级，且**选择器列表仍是唯一事实来源**。
    """
    tags, classes, ids, roles = set(), set(), set(), set()
    class_sub, id_sub, others = [], [], []
    for sel in selectors:
        sel = sel.strip()
        m = re.fullmatch(r'\.([\w-]+)', sel)
        if m:
            classes.add(m.group(1))
            continue
        m = re.fullmatch(r'#([\w-]+)', sel)
        if m:
            ids.add(m.group(1))
            continue
        m = re.fullmatch(r'([A-Za-z][\w-]*)', sel)
        if m:
            tags.add(m.group(1).lower())
            continue
        m = re.fullmatch(r'\[role="([\w-]+)"\]', sel)
        if m:
            roles.add(m.group(1))
            continue
        m = re.fullmatch(r'\[class\*="([\w-]+)"\]', sel)
        if m:
            class_sub.append(m.group(1))
            continue
        m = re.fullmatch(r'\[id\*="([\w-]+)"\]', sel)
        if m:
            id_sub.append(m.group(1))
            continue
        others.append(sel)
    return tags, classes, ids, roles, class_sub, id_sub, others


(_DROP_BY_TAG, _DROP_BY_CLASS, _DROP_BY_ID,
 _DROP_BY_ROLE, _DROP_BY_CLASS_SUB, _DROP_BY_ID_SUB,
 _DROP_OTHER_SELECTORS) = _compile_selector_rules(_BOILERPLATE_SELECTORS)

# 正文容器候选的最小文本量：低于此值不认为找到了正文
_MIN_CONTENT_LEN = 200
# 视为"同一量级"的文本量比例：保留全部正文的前提下取最紧凑容器
_NEAR_MAX_RATIO = 0.90


# ── 正文定位 ──────────────────────────────────────────────────

def _has_high_link_density(tag, text_len: int, threshold: float = 0.50) -> bool:
    """元素的链接文本占比是否过高（可能是导航/索引页）。

    text_len 由调用方传入（已经算过一次），避免重复 get_text。
    """
    if text_len == 0:
        return True
    links = tag.find_all('a')
    if not links:
        return False
    link_text_len = sum(len(a.get_text(strip=True)) for a in links)
    return link_text_len / text_len >= threshold


def _pick_container(candidates, min_len: int = _MIN_CONTENT_LEN,
                    link_density: float = 0.50):
    """从候选元素中挑正文容器。

    规则：先按文本量留下"最大的那一档"（>= 最大值的 90%），再在这一档里取
    HTML 最紧凑的元素 —— 既不会被页面头部/侧栏的大包裹元素抢走，也不会
    像"文本量 × 密度²"那样被一个又小又密的卡片（实测 219 字的 card-body）
    盖过真正的正文。
    """
    sized = []
    seen = set()
    for tag in candidates:
        key = id(tag)
        if key in seen:
            continue
        seen.add(key)
        text = tag.get_text(' ', strip=True)
        text_len = len(text)
        if text_len < min_len:
            continue
        if _has_high_link_density(tag, text_len, link_density):
            continue
        sized.append((text_len, tag))

    if not sized:
        return None

    max_len = max(t for t, _ in sized)
    near = [tag for t, tag in sized if t >= max_len * _NEAR_MAX_RATIO]
    return min(near, key=lambda tag: len(str(tag)))


def _locate_content(soup: BeautifulSoup):
    """定位正文容器，返回元素或 None"""
    # 第 1 层：语义标签（article / main / role=main / itemprop）
    best = _pick_container(soup.select(", ".join(_SEMANTIC_SELECTORS)))
    if best is not None:
        return best

    # 第 2 层：常见正文类名
    best = _pick_container(soup.select(", ".join(_CONTENT_SELECTORS)))
    if best is not None:
        return best

    # 第 3 层：回退 —— 对 body 下容器按文本量筛选（链接密度收紧）
    body = soup.find('body') or soup
    best = _pick_container(body.find_all(['div', 'section', 'article']),
                           link_density=0.40)
    if best is not None:
        return best

    # 第 4 层：任一语义标签（即使很短）
    for sel in ('article', 'main', '[role="main"]'):
        found = soup.select_one(sel)
        if found is not None and found.get_text(strip=True):
            return found
    return None


def _protected_ids(container) -> set:
    """正文容器及其所有祖先的 id —— 这些元素绝不允许被去噪删除

    这是"通配符选择器误伤正文"的根治手段：即使某个选择器命中了正文的某个
    祖先（例如 WordPress 给 <body> 加的 no-sidebar），也只跳过该元素本身，
    不会把正文一起删掉。
    """
    ids = set()
    if container is None:
        return ids
    ids.add(id(container))
    for parent in container.parents:
        ids.add(id(parent))
    return ids


def _is_boilerplate_element(elem) -> bool:
    """按编译好的规则判断元素是否属于模板/导航（等价于原选择器组）"""
    if elem.name in _DROP_BY_TAG:
        return True

    classes = elem.get('class')
    if classes:
        if any(c in _DROP_BY_CLASS for c in classes):
            return True
        if _DROP_BY_CLASS_SUB:
            joined = ' '.join(classes)
            if any(s in joined for s in _DROP_BY_CLASS_SUB):
                return True

    elem_id = elem.get('id')
    if elem_id:
        if elem_id in _DROP_BY_ID:
            return True
        if any(s in elem_id for s in _DROP_BY_ID_SUB):
            return True

    role = elem.get('role')
    if role and role in _DROP_BY_ROLE:
        return True

    return False


def _remove_boilerplate_elements(soup: BeautifulSoup, protected: set | None = None):
    """从 DOM 中移除明显不属于正文的元素（跳过正文容器及其祖先）"""
    protected = protected or set()
    for elem in soup.find_all(True):
        key = id(elem)
        if key in protected:
            continue
        # 父节点已被删除（元素随祖先一起移除）→ 跳过
        if elem.parent is None:
            continue
        if _is_boilerplate_element(elem):
            elem.decompose()

    # 兜底：无法用属性判断表达的复杂选择器（当前为空）
    for sel in _DROP_OTHER_SELECTORS:
        for elem in soup.select(sel):
            if id(elem) in protected or elem.parent is None:
                continue
            elem.decompose()

    # <header> 既可能是站点页头，也可能是"文章内标题+作者"。
    # 无条件删除会把正文标题删掉，故只在确认是站点页头时移除。
    for elem in soup.find_all('header'):
        if id(elem) in protected:
            continue
        if elem.parent is None:
            continue
        if elem.find_parent(['article', 'main']) is not None:
            continue
        if elem.find('h1') is not None:
            continue
        elem.decompose()


def _prepare_pre_blocks(soup: BeautifulSoup):
    """把 <pre> 内部结构压成单节点，保留其内部换行（避免代码被逐 token 拆行）"""
    for pre in soup.find_all('pre'):
        text = pre.get_text()
        pre.clear()
        if text:
            pre.append(NavigableString(text))


def _extract_block_text(container) -> str:
    """按块级标签边界换行提取文本；内联标签之间不插换行"""
    out: list[str] = []

    def walk(node):
        for child in node.children:
            if isinstance(child, NavigableString):
                s = str(child)
                if s:
                    out.append(s)
                continue
            if not isinstance(child, Tag):
                continue
            name = child.name
            if name in _DROP_TAGS:
                continue
            if name == 'pre':
                out.append('\n')
                out.append(child.get_text())
                out.append('\n')
                continue
            if name == 'br':
                out.append('\n')
                continue
            block = name in _BLOCK_TAGS
            if block:
                out.append('\n')
            walk(child)
            if block:
                out.append('\n')

    walk(container)
    return ''.join(out)


# ── 行级噪音模式 ──────────────────────────────────────────────

# 整行即为噪音（锚定匹配，无论长短都丢弃）
_LINE_NOISE_PATTERNS = [
    re.compile(r'^(?:copyright|©|\(c\))\s*(?:©\s*)?\d{4}', re.IGNORECASE),
    re.compile(r'^all\s+(?:rights\s+)?reserved\.?\s*$', re.IGNORECASE),
    re.compile(r'^privacy\s+(?:policy|statement)\s*$', re.IGNORECASE),
    re.compile(r'^terms\s+of\s+(?:service|use)\s*$', re.IGNORECASE),
    re.compile(r'^(?:版权所有|免责声明|法律声明|备案号|京ICP|沪ICP|粤ICP).{0,40}$'),
    re.compile(r'^(?:subscribe|sign\s+up|newsletter|follow\s+us|share\s+(?:this|on)[^\n]{0,20})'
               r'\s*$', re.IGNORECASE),
    re.compile(r'^(?:read\s+more|more|next|previous|prev)\s*$', re.IGNORECASE),
    re.compile(r'^(?:comments?|leave\s+a\s+(?:comment|reply)|post\s+a\s+comment)\s*$', re.IGNORECASE),
    re.compile(r'^(?:登录|注册|登陆|立即登录|免费注册|退出登录|返回顶部)$'),
    re.compile(r'^(?:分享|收藏|点赞|评论|转发|打赏|赞赏|赞赏记录|留言|回复)'
               r'(?:支持|作者|一下|我们|本文)?\s*\d*$'),
    re.compile(r'^(?:返回|查看|阅读)\s*(?:原文|全部|更多|详情)$'),
    re.compile(r'^阅读\s*[（(]\s*\d+\s*[)）]$'),
    re.compile(r'^(?:登录|注册)(?:后)?(?:方可)?(?:查看|浏览|阅读)(?:全文|全部|内容)?[！!]?$'),
    re.compile(r'^阅读原文$'),
    # 聚合器 / 站内模板（本项目信源高频）
    re.compile(r'^AI\s*(?:评分|导读)\s*\d*$', re.IGNORECASE),
    re.compile(r'^(?:推荐理由|精选|正文\s*·\s*AI\s*翻译|来源[:：]\s*|作者[:：]\s*|编辑[:：]\s*)$'),
    re.compile(r'^(?:参与人|雪币|最新回复|游客)$'),
    re.compile(r'^(?:查看被引用的帖子|在新标签页中打开|扫码关注|关注我们)$'),
    # 标签 / 分类
    re.compile(r'^(?:tags?|标签|分类)[:：][^\n]{0,40}$', re.IGNORECASE),
    re.compile(r'^#\s*\S{0,18}$'),
    # 时间元信息
    re.compile(r'^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?$'),
    re.compile(r'^[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}$'),
    re.compile(r'^[A-Z][a-z]{2,8}\.?\s+\d{1,2},\s+\d{4}$'),          # October 1, 2026
    re.compile(r'^\d{1,2}\s+[A-Z][a-z]{2,8}\s+\d{4}$'),              # 1 October 2026
    re.compile(r'^\d+\s*(?:秒|分钟|小时|天|周|个月|月|年)前$'),
    re.compile(r'^(?:发表于|发布于|更新于|最后更新)\s*[:：]?[^\n]{0,30}$'),
    # 裸域名
    re.compile(r'^(?:[\w-]+\.)+(?:com|org|net|cn|io|ai|dev|gov|edu|co|me|xyz|info|'
               r'uk|de|jp|fr|ru|tv|news|blog|app|site)$', re.IGNORECASE),
    # 翻页 / 站内导航
    re.compile(r'^(?:首页|主页|网站首页|返回首页|上一篇|下一篇|上一页|下一页|查看更多|更多内容)$'),
    re.compile(r'^(?:posted\s+(?:on|in|by)|published\s+on|written\s+by|filed\s+under)'
               r'[^\n]{0,40}$', re.IGNORECASE),
]

# 纯站内导航词：整行**恰好**是这些词才丢（锚定，避免误伤正文里的同名词）
_NAV_WORD_RE = re.compile(
    r'^(?:blog|news|recent|featured|video|videos|category|categories|tags?|home|menu|'
    r'search|login|log\s?in|sign\s?in|sign\s?up|register|subscribe|newsletter|contact|'
    r'about|resources|events|webinars?|podcasts?|case\s+studies|whitepapers?|'
    r'start\s+free\s+trial|free\s+trial|request\s+a?\s*demo|read\s+more|see\s+more|'
    r'learn\s+more|view\s+all|show\s+more|more|next|previous|prev|share|follow|'
    r'organisation|organization|company|team|careers|press|publications|archive)$',
    re.IGNORECASE)

# 元信息串：由 ·/| 等分隔的一串账号、日期、栏目名（聚合器卡片头部）
_META_SEP_RE = re.compile(r'\s*[·•|｜]\s*')
_META_HINT_RE = re.compile(
    r'@|\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}|\d+\s*(?:秒|分钟|小时|天|周|个月|月|年)前|'
    r'精选|AI\s*评分|AI\s*导读|推荐理由|来源\s*[:：]|作者\s*[:：]|编辑\s*[:：]',
    re.IGNORECASE)
_SENT_END_RE = re.compile(r'[。！？!?；;.]\s*$')
# 标签串：一行里出现 2 个及以上 # 标签
_TAG_RUN_RE = re.compile(r'(?:#\s*[^\s#]{1,20}\s*){2,}')

# 仅对"短行"生效的噪音模式（长句里出现这些词属正常内容，不能删）
# 实测放到 80 字会把「关注/订阅/搜索」等正常内容句一起删掉（某页丢失率 13%→26%），
# 只换来噪音行 358→311，得不偿失，故维持 60。
_SHORT_LINE_MAX = 60
_SHORT_LINE_NOISE_PATTERNS = [
    re.compile(r'搜索|search', re.IGNORECASE),
    re.compile(r'登录|注册|sign\s*in|sign\s*up|log\s*in', re.IGNORECASE),
    re.compile(r'评论|点赞|收藏|转发|打赏|留言|回复'),
    re.compile(r'相关(?:阅读|文章|推荐)|延伸阅读|推荐阅读|猜你喜欢|热门(?:文章|推荐|阅读)'),
    re.compile(r'最新文章|更多精彩|更多内容|下一篇|上一篇'),
    re.compile(r'分享到|扫码|二维码|关注我们|微信公众号|订阅'),
    re.compile(r'follow\s+us|share\s+(?:this|on)', re.IGNORECASE),
    re.compile(r'首页|主页|导航|栏目|菜单|网站地图|sitemap', re.IGNORECASE),
    re.compile(r'newsletter|邮箱订阅|邮件订阅|subscribe|delivered\s+daily', re.IGNORECASE),
    re.compile(r'广告|推广|赞助|sponsored|advertisement|affiliate', re.IGNORECASE),
    re.compile(r'隐私政策|服务条款|使用条款|privacy\s+policy|terms\s+of\s+(?:service|use)',
               re.IGNORECASE),
    re.compile(r'cookie|consent', re.IGNORECASE),
    re.compile(r'查看更多|阅读全文|阅读原文|read\s+more|min\s+read', re.IGNORECASE),
    re.compile(r'copyright|all\s+rights\s+reserved|版权所有|©', re.IGNORECASE),
    re.compile(r'tags?\s*[:：]|标签\s*[:：]|filed\s+under|posted\s+in', re.IGNORECASE),
    re.compile(r'阅读量|浏览量|已阅|views?\s*[:：]\s*\d', re.IGNORECASE),
    re.compile(r'责任编辑|编辑\s*[:：]|作者\s*[:：]|来源\s*[:：]|转载|本文来自|编译\s*[:：]'),
    re.compile(r'featured|精选|推荐理由|AI\s*评分|AI\s*导读', re.IGNORECASE),
    re.compile(r'参与人|雪币|最新回复|游客'),
    re.compile(r'版权|免责声明|备案号'),
    re.compile(r'下载\s*APP|客户端下载|app\s*store|google\s*play', re.IGNORECASE),
]

# 零宽 / 双向控制 / 软连字符
_INVISIBLE_RE = re.compile(r'[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff\u00ad]')
# 纯符号行：不含任何字母、数字或汉字
_PURE_SYMBOL_RE = re.compile(r'^[\W_]+$')
_MULTI_NEWLINE_RE = re.compile(r'\n{3,}')
_SPACE_RE = re.compile(r'[ \t\u00a0\u3000\u2000-\u200a]+')

# 同页重复行收敛参数
_DUP_MIN_LEN = 6
_DUP_MIN_COUNT = 3


def _is_metadata_run(stripped: str) -> bool:
    """判断是否为"元信息串"。

    典型形态（聚合器站点卡片头部，拆行后本应逐项过滤，但块级换行会把它们
    并成一行，导致单条规则失效）：
        ClaudeDevs· @ ClaudeDevs · X·2026-10-08 02:08· 21 小时前精选AI 评分70

    判据：<=120 字、无句末标点、含时间/账号等元信息线索、按 ·/| 切分后
    至少 3 段且每段都很短。
    """
    if len(stripped) > 120:
        return False
    if _SENT_END_RE.search(stripped):
        return False
    if not _META_HINT_RE.search(stripped):
        return False
    parts = [p.strip() for p in _META_SEP_RE.split(stripped) if p.strip()]
    if len(parts) < 3:
        return False
    return all(len(p) <= 40 for p in parts)


def _is_noise_line(stripped: str) -> bool:
    for pat in _LINE_NOISE_PATTERNS:
        if pat.search(stripped):
            return True
    if _NAV_WORD_RE.match(stripped):
        return True
    if _TAG_RUN_RE.search(stripped) and len(stripped) <= 80:
        return True
    if _is_metadata_run(stripped):
        return True
    if len(stripped) <= _SHORT_LINE_MAX:
        for pat in _SHORT_LINE_NOISE_PATTERNS:
            if pat.search(stripped):
                return True
    if _PURE_SYMBOL_RE.match(stripped):
        return True
    return False


def _collapse_repeated_lines(lines: list[str]) -> list[str]:
    """收敛同页重复行：模板块（作者名、相关文章标题等）会重复 3 次以上"""
    counts = Counter(l for l in lines if len(l) >= _DUP_MIN_LEN)
    repeated = {l for l, n in counts.items() if n >= _DUP_MIN_COUNT}
    if not repeated:
        return lines
    seen = set()
    out = []
    for l in lines:
        if l in repeated:
            if l in seen:
                continue
            seen.add(l)
        out.append(l)
    return out


def _collapse_inline_repeats(text: str, min_unit: int = 6, min_count: int = 3,
                             max_line: int = 3000) -> str:
    """收敛**同一行内**连续重复的短语。

    踩过的坑：部分论坛（实测 奇安信攻防社区）把"登录吧！不然什么都看不到的！"
    这类登录墙提示放在十几个行内 span 里重复一遍。块级换行不会拆开它们，
    于是并成"一行里重复十遍"的垃圾，而按整行比对的去重规则抓不到。
    """
    if len(text) > max_line:
        return text
    n = len(text)
    out = []
    i = 0
    while i < n:
        # 分隔空白原样保留（收敛后不会再多出空格）
        if text[i] in ' \t':
            out.append(text[i])
            i += 1
            continue
        # 从最短单元往上找：最短的那个满足"连续重复≥3次"的单元才是真正的重复周期。
        # 若从长往短找，会先匹配到"两个单元拼起来"的假周期，收敛不干净。
        limit = min(120, (n - i) // min_count)
        collapsed = False
        for L in range(min_unit, limit + 1):
            unit = text[i:i + L]
            if len(unit.strip()) < min_unit:
                continue
            cnt = 1
            j = i + L
            while True:
                k = j
                while k < n and text[k] in ' \t':
                    k += 1
                if text[k:k + L] == unit:
                    cnt += 1
                    j = k + L
                else:
                    break
            if cnt >= min_count:
                out.append(unit)
                i = j
                collapsed = True
                break
        if not collapsed:
            out.append(text[i])
            i += 1
    return ''.join(out).strip()


def _has_adjacent_repeat(text: str, min_unit: int = 6, max_gap: int = 160,
                         max_line: int = 3000) -> bool:
    """廉价探测：同一段 >=6 字片段是否在很近的距离内再次出现。

    只用来决定"值不值得做行内收敛"——真正的收敛判据由
    _collapse_inline_repeats 的"连续重复≥3次"把关，所以这里允许误报
    （误报只是白跑一次收敛，不会改动文本）。

    踩过的坑：不能用固定周期去查（曾用固定 8 字），因为重复单元长度是任意的；
    实测 奇安信攻防社区的登录墙提示单元是 14 字，固定周期探测查不出来。
    """
    if len(text) > max_line:
        return False
    n = len(text)
    seen = {}
    for i in range(n - min_unit + 1):
        gram = text[i:i + min_unit]
        prev = seen.get(gram)
        if prev is not None and i - prev <= max_gap:
            return True
        seen[gram] = i
    return False


def _clean_noise_lines(text: str) -> str:
    """逐行过滤残余噪音，并清理零宽字符与重复行"""
    text = _INVISIBLE_RE.sub('', text)
    lines = []
    for line in text.split('\n'):
        stripped = line.strip()
        if not stripped:
            continue
        if _is_noise_line(stripped):
            continue
        # 行内连续重复短语（先去空格做廉价探测，命中才做收敛）
        if _has_adjacent_repeat(stripped.replace(' ', '')):
            stripped = _collapse_inline_repeats(stripped)
            if not stripped or _is_noise_line(stripped):
                continue
        lines.append(stripped)

    deduped = []
    for l in lines:
        if deduped and deduped[-1] == l:
            continue
        deduped.append(l)
    lines = _collapse_repeated_lines(deduped)

    text = '\n'.join(lines)
    text = _MULTI_NEWLINE_RE.sub('\n\n', text)
    return text.strip()


def _fallback_extract_text(html_text: str) -> str:
    """回退策略：正则提取纯文本（此处需要显式解码 HTML 实体）"""
    text = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', html_text, flags=re.IGNORECASE)
    text = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', text, flags=re.IGNORECASE)
    for tag in ['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                'li', 'tr', 'td', 'th', 'blockquote', 'pre', 'dl', 'dt', 'dd']:
        text = re.sub(rf'</{tag}>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<hr\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    # 仅此处解码一次（BS4 路径下 get_text 已解码，不能再解一次）
    return html.unescape(text)


def extract_text_from_html(html_text: str) -> str:
    """从 HTML 中提取正文，自动去除导航/广告/版权等噪音

    五层策略:
      1. 定位: 语义标签 / 正文类名 / 文本量回退，选出正文容器
      2. DOM 去噪: 移除已知非正文元素，但绝不删除正文容器及其祖先
      3. 提取: 仅块级标签边界换行，内联标签不拆行
      4. 重定位: 去噪后再选一次容器（此时更准）
      5. 行级: 过滤残余噪音、纯符号行、重复行
    """
    if not html_text:
        return ""

    try:
        soup = BeautifulSoup(html_text, 'lxml')
        _prepare_pre_blocks(soup)
        # 先在**未去噪**的树上定位正文，用它给去噪过程划保护圈
        guarded = _locate_content(soup)
        _remove_boilerplate_elements(soup, _protected_ids(guarded))
        # 去噪后再定位一次；失败则回退到保护圈那次的结果
        container = _locate_content(soup) or guarded or soup
        text = _extract_block_text(container)
    except Exception:
        text = _fallback_extract_text(html_text)

    if not text:
        return ""

    text = _SPACE_RE.sub(' ', text)
    text = _clean_noise_lines(text)
    return text


# ── 以下与现有实现一致（抓取与写回） ──────────────────────────

def fetch_article_text(url: str) -> tuple[str | None, str]:
    """抓取文章 URL 并提取纯文本。

    返回 (正文或 None, 状态说明)。状态用于统计被安全策略拦下的条目数。
    """
    try:
        resp = safe_get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
            max_bytes=MAX_RESPONSE_BYTES,
        )
    except UnsafeURLError as e:
        return None, f"blocked:{e}"
    except Exception as e:
        return None, f"error:{type(e).__name__}"

    content_type = resp.header("content-type", "")
    if "html" not in content_type.lower():
        return None, "skipped_content_type"

    text = extract_text_from_html(resp.text)
    if len(text) < 200:
        return None, "too_short"
    return text[:MAX_BODY_LENGTH], ("truncated" if resp.truncated else "ok")


def _fetch_one(item: dict) -> tuple[dict, str | None, str]:
    url = item.get("url", "") or ""
    body, status = fetch_article_text(url)
    return item, body, status


def run():
    if not PARSED_ITEMS_PATH.exists():
        raise FileNotFoundError(
            f"缺少阶段1产物 {PARSED_ITEMS_PATH}，无法提取全文")

    with open(PARSED_ITEMS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    total = len(items)
    targets = []
    for item in items:
        summary = item.get("summary", "") or ""
        url = item.get("url", "") or ""
        if len(summary) >= SUMMARY_MIN_LENGTH or not url:
            continue
        if not url.startswith("http"):
            continue
        if item.get("full_body"):
            continue
        targets.append(item)

    print(f"[FULLTEXT] 需抓取全文: {len(targets)}/{total} 条（并发 {MAX_WORKERS}）")

    fetched_count = 0
    blocked_count = 0
    status_counter: dict[str, int] = {}
    if targets:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = [pool.submit(_fetch_one, it) for it in targets]
            done = 0
            for fut in as_completed(futures):
                item, body, status = fut.result()
                done += 1
                key = status.split(":", 1)[0]
                status_counter[key] = status_counter.get(key, 0) + 1
                if key == "blocked":
                    blocked_count += 1
                    print(f"  [FULLTEXT] 已拦截不安全链接: {item.get('url','')[:80]}"
                          f" ({status.split(':', 1)[1][:60]})")

                if body:
                    item["original_summary"] = item.get("summary", "")
                    item["summary"] = body
                    item["fulltext_fetched"] = True
                    fetched_count += 1
                else:
                    item["fulltext_fetched"] = False

                if done % 20 == 0:
                    print(f"  [FULLTEXT] 进度: {done}/{len(targets)}")

    atomic_write(PARSED_ITEMS_PATH, items, indent=2)

    detail = ", ".join(f"{k}={v}" for k, v in sorted(status_counter.items()))
    print(f"[FULLTEXT] 全文提取完成: {fetched_count}/{len(targets)} 条获取到正文"
          + (f"（{detail}）" if detail else ""))
    if blocked_count:
        print(f"[FULLTEXT] 其中 {blocked_count} 条链接指向内网/非法地址，已按安全策略拦截")
    return items


if __name__ == "__main__":
    run()
