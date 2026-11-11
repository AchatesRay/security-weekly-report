"""正文 HTML 白名单净化 — 把外部网页正文变成可安全内联的 HTML 片段

为什么必须做净化（而不是直接 `innerHTML = 原文`）：
  报告页与后台配置接口同源（Basic Auth），正文里的 `<script>` 或
  `onerror=` 一旦执行，等于把 `/api/config/*` 写接口交给外部内容方。
  因此这里采用**白名单**：只保留语义标签与少量属性，其余一律拆壳或删除。

为什么选择"保留 HTML"而不是继续用纯文本：
  纯文本在抽取阶段就丢掉了标题层级、列表、代码块、表格与图片，
  前端再按句号强行分段只会越排越碎。结构化 HTML 能同时解决
  "格式乱"与"图片丢失"，也是后续导出 Markdown 的唯一可靠来源。

规则：
  - 保留：h1-h6 / p / ul / ol / li / blockquote / pre / code / table 系列 /
    figure / figcaption / img / a / strong / em / br / hr / sup / sub
  - 丢弃：script / style / iframe / form / input / button / svg / video /
    audio / object / embed / link / meta / canvas / template
  - 属性：a[href]（仅 http/https）、img[src/alt/title]、td/th[colspan/rowspan]
  - 相对链接按文章 URL 补全；`.svg` 图片与 1x1 跟踪像素直接丢弃
"""

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag

# 单篇正文 HTML 上限：防止个别页面把 data_*.json 撑爆
MAX_FRAGMENT_CHARS = 200_000

ALLOWED_TAGS = frozenset({
    "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "dl", "dt", "dd",
    "blockquote", "pre", "code", "kbd", "samp",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption",
    "figure", "figcaption", "img",
    "a", "strong", "b", "em", "i", "u", "s", "mark", "small",
    "br", "hr", "sup", "sub", "span",
})

DROP_TAGS = frozenset({
    "script", "style", "noscript", "template", "iframe", "frame", "frameset",
    "form", "input", "button", "select", "option", "textarea",
    "svg", "canvas", "video", "audio", "source", "track", "object", "embed",
    "link", "meta", "base", "map", "area", "dialog",
})

# 空块清理：这些标签里既没有文字也没有图片就删掉
_EMPTY_BLOCK_TAGS = ("p", "li", "h1", "h2", "h3", "h4", "h5", "h6",
                     "blockquote", "figcaption", "td", "th")

# 图片懒加载常见属性：src 为空/占位图时按顺序回退
_IMG_SRC_ATTRS = ("src", "data-src", "data-original", "data-lazy-src",
                  "data-actualsrc", "data-url", "data-original-src")

# 登录/注册链接：正文里没有信息量，且会泄漏站内路径
_LOGIN_PATH_RE = re.compile(r"/(?:login|signin|signup|register)(?:[/?#]|$)", re.IGNORECASE)

_PLACEHOLDER_SRC_RE = re.compile(
    r"^(?:data:|about:blank|javascript:|#|$)", re.IGNORECASE)

# 1x1 跟踪像素：gif/png 常见文件名特征
_TRACKER_SRC_RE = re.compile(
    r"(?:pixel|spacer|blank|transparent|1x1|tracking)[^/]*\.(?:gif|png|jpg)$",
    re.IGNORECASE)

_WS_RE = re.compile(r"[ \t\r\n\u00a0\u3000]+")


def _abs_url(base_url: str, raw: str) -> str:
    """相对地址补全；只接受 http/https"""
    if not raw:
        return ""
    value = raw.strip()
    if value.startswith("//"):
        value = ("https:" if base_url.startswith("https://") else "http:") + value
    if base_url:
        try:
            value = urljoin(base_url, value)
        except ValueError:
            return ""
    if not value.lower().startswith(("http://", "https://")):
        return ""
    return value


def _pick_img_src(tag: Tag, base_url: str) -> str:
    """选出可用的图片地址（兼容懒加载写法）"""
    candidates = []
    for attr in _IMG_SRC_ATTRS:
        value = tag.get(attr)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
    srcset = tag.get("srcset")
    if isinstance(srcset, str) and srcset.strip():
        # 取第一个候选地址（通常是最小图，够报告阅读）
        candidates.append(srcset.split(",")[0].strip().split(" ")[0])

    for candidate in candidates:
        if _PLACEHOLDER_SRC_RE.match(candidate):
            continue
        url = _abs_url(base_url, candidate)
        if not url:
            continue
        path = urlparse(url).path.lower()
        if path.endswith(".svg") or _TRACKER_SRC_RE.search(path):
            continue
        return url
    return ""


def _clean_tag(tag: Tag, base_url: str) -> None:
    """按标签类型重写属性；不在白名单的属性全部丢弃"""
    name = tag.name
    attrs: dict[str, str] = {}

    if name == "a":
        href = _abs_url(base_url, tag.get("href") or "")
        if href and _LOGIN_PATH_RE.search(urlparse(href).path or ""):
            tag.decompose()
            return
        if href:
            attrs["href"] = href
            attrs["target"] = "_blank"
            attrs["rel"] = "noopener noreferrer"
    elif name == "img":
        src = _pick_img_src(tag, base_url)
        if not src:
            tag.decompose()
            return
        # 1x1 跟踪像素：宽高属性 ≤2 的直接丢弃
        for dim in ("width", "height"):
            raw = str(tag.get(dim) or "").strip().rstrip("px")
            if raw.isdigit() and int(raw) <= 2:
                tag.decompose()
                return
        attrs["src"] = src
        alt = (tag.get("alt") or "").strip()
        if alt:
            attrs["alt"] = alt[:200]
        title = (tag.get("title") or "").strip()
        if title:
            attrs["title"] = title[:200]
        attrs["loading"] = "lazy"
        attrs["decoding"] = "async"
        attrs["referrerpolicy"] = "no-referrer"
    elif name in ("td", "th"):
        for dim in ("colspan", "rowspan"):
            raw = str(tag.get(dim) or "").strip()
            if raw.isdigit() and 1 <= int(raw) <= 50:
                attrs[dim] = str(int(raw))

    tag.attrs = attrs


def _normalize_text(soup: BeautifulSoup) -> None:
    """压缩空白（pre/code 内部保留原样），并清理空块"""
    for node in soup.find_all(string=True):
        parent = node.parent
        if isinstance(parent, Tag) and parent.name in ("pre", "code"):
            continue
        text = _WS_RE.sub(" ", str(node))
        if text != str(node):
            node.replace_with(NavigableString(text))

    for tag in soup.find_all(_EMPTY_BLOCK_TAGS):
        if tag.get_text(strip=True):
            continue
        if tag.find("img"):
            continue
        tag.decompose()

    # 去掉首尾空行（<br> 连续的情况交给 CSS）
    for tag in soup.find_all(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6",
                              "blockquote", "figcaption"]):
        if not tag.contents:
            tag.decompose()


def _truncate(html_text: str, max_chars: int) -> str:
    """在块级边界处截断，避免把标签切坏"""
    if len(html_text) <= max_chars:
        return html_text
    cut = html_text.rfind("</p>", 0, max_chars)
    if cut < 0:
        cut = html_text.rfind("</li>", 0, max_chars)
    if cut < 0:
        cut = html_text.rfind("</h2>", 0, max_chars)
    if cut < 0:
        return ""
    return html_text[:cut + 4]


def sanitize_fragment(html_text: str, base_url: str = "",
                      max_chars: int = MAX_FRAGMENT_CHARS) -> str:
    """返回净化后的 HTML 片段（无 html/body 包裹）。异常输入返回空串。"""
    if not html_text or not isinstance(html_text, str):
        return ""
    try:
        soup = BeautifulSoup(html_text, "lxml")
    except Exception:
        return ""

    for tag in soup.find_all(DROP_TAGS):
        tag.decompose()

    # 反向遍历：先处理深层的节点，再拆外层未知标签，避免已清理的节点被重建
    for tag in reversed(soup.find_all(True)):
        if tag.name in DROP_TAGS:
            continue
        if tag.name not in ALLOWED_TAGS:
            tag.unwrap()
            continue
        _clean_tag(tag, base_url)

    _normalize_text(soup)

    body = soup.body or soup
    try:
        fragment = body.decode_contents()
    except Exception:
        fragment = str(body)
    fragment = fragment.strip()
    if not fragment:
        return ""
    return _truncate(fragment, max_chars)


def fragment_text_length(html_text: str) -> int:
    """片段内的可见文字长度（用于判断抽取结果是否偏短）"""
    if not html_text:
        return 0
    try:
        soup = BeautifulSoup(html_text, "lxml")
        return len(_WS_RE.sub(" ", soup.get_text(" ")).strip())
    except Exception:
        return 0


def count_images(html_text: str) -> int:
    if not html_text:
        return 0
    return html_text.count("<img ")


def count_blocks(html_text: str) -> int:
    """统计块级元素数量：用于判断"有结构"还是"只有一段话" """
    if not html_text:
        return 0
    try:
        soup = BeautifulSoup(html_text, "lxml")
        return len(soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6",
                                  "li", "blockquote", "pre", "table", "figure"]))
    except Exception:
        return 0
