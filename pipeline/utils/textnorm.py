"""标题与 URL 规范化（去重、指纹、台账共用）

单独成模块的原因：全项目只能有一套规范化口径。此前只存在于
`pipeline/steps/deduplicator.py`，正文指纹与跨周台账如果再抄一份，
两边规则一旦漂移，就会出现“去重认为不同、台账认为相同”的静默不一致。
"""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 常见追踪参数：参与去重比较前剥离
TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_reader", "fbclid", "gclid", "dclid", "msclkid",
    "igshid", "yclid", "mc_cid", "mc_eid", "_hsenc", "_hsmi", "ref", "referrer",
    "spm", "from", "share_token", "wt_mc", "cmpid", "ncid", "sr_share",
})

# 标题规范化：去掉前缀标记、括号标签与标点，压缩空白
_PREFIX_RE = re.compile(
    r"^\s*(?:cve\s*alert|alert|news|update|critical|high|medium|low|"
    r"breaking|exclusive|analysis|opinion|video|podcast)\s*[:：\-–—]\s*",
    re.IGNORECASE)
_BRACKET_RE = re.compile(r"[\[【(（][^\]】)）]{0,30}[\]】)）]")
_NON_WORD_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def normalize_title(title: str) -> str:
    """规范化标题用于相似度比较"""
    if not title:
        return ""
    text = title.strip().lower()
    # 反复剥离前缀（如 "Update: Alert: xxx"）
    for _ in range(3):
        new = _PREFIX_RE.sub("", text)
        if new == text:
            break
        text = new
    text = _BRACKET_RE.sub(" ", text)
    text = _NON_WORD_RE.sub(" ", text)
    return " ".join(text.split())


def normalize_url(url: str) -> str:
    """规范化 URL：去片段、剥离追踪参数、统一主机大小写与末尾斜杠"""
    if not url:
        return ""
    raw = url.strip()
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    if not parts.scheme or not parts.netloc:
        return raw
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    # 去掉默认端口
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]
    path = parts.path.rstrip("/") or "/"
    try:
        pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                 if k.lower() not in TRACKING_PARAMS]
    except ValueError:
        pairs = []
    query = urlencode(sorted(pairs))
    return urlunsplit((scheme, netloc, path, query, ""))
