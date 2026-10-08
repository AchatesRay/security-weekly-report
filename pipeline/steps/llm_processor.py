"""摘要模块 — 为收录内容生成中文摘要（抽取式）

支持两种模式:
  1. 抽取式摘要（默认） — 打分 + 最优连续窗口，无需外部 API
  2. LLM 摘要（配置启用） — 预留位，**尚未实现**（见文件末尾说明）

数据流：classified_items.json --[本步骤]--> enhanced_items.json

2026-10-08 质量改造（第一档：修硬伤；第二档：长度分档 + 来源标记）：
  1. **先洗稿再摘取**：生成摘要前剥离网页噪声（导航、署名、分享按钮、日期、
     "阅读 N 分钟"、裸网址等），并剥掉正文开头的元信息行与个别信源自带的
     "聚合壳"。改前实测 25.4% 的摘要在开头就混着导航/署名（如
     "Blog Featured Unknown Threat Actor Uses AI-Driven ARTEX…"）。
  2. **改成"最优连续窗口"**：原先按句子打分后把高分句东拼一句西拼一句，
     句与句常常不是同一件事（"模型不输出…" 后面接 "烤箱升到 425°F"）；
     现在在句子序列上找"总分最高且不超预算"的一整段连续内容。
  3. **在句子边界收尾**：原先拼完超过 500 字直接从中间切开加省略号，
     改前 74.6% 的摘要是半句话；现在按整句累加到预算为止。
  4. **拼接补分隔符**：中文不需要空格、英文需要。原先统一用空串拼接，
     英文句子粘成 "accessed.The company"（实测 181 处）。
  5. **长度可配置 + 分档**：上限不再写死 500 字，改读 config/settings.json 的
     summary.max_chars；short_categories 里的分类用 summary.short_max_chars
     （默认 ④ 政策法规与标准框架、⑤ 产业动态与技术趋势 为 300 字）。
  6. **摘要来源标记**：每条写入 ai_summary_kind（extractive / fallback / empty），
     报告据此区分"自动提炼"与"原文节选"，避免把兜底截断当成摘要。
  7. **不在本步骤内翻译**（2026-09-29 起）：翻译统一交给翻译步骤，若翻译失败
     报告会如实标注。

注意：本模块产出的摘要**永远是原文里出现过的话**（抽取式），不是模型改写。
想要"读懂后用自己的话概括"，必须实现文件末尾的 LLM 分支（第三档，需外部服务）。
"""

import json
import re

import jieba
import yaml

from ..utils import (DATA_DIR, LLM_CONFIG_PATH, SETTINGS_PATH, atomic_write)

CLASSIFIED_ITEMS_PATH = DATA_DIR / "classified_items.json"
ENHANCED_ITEMS_PATH = DATA_DIR / "enhanced_items.json"

# ── 计算规模上限（防止抽取式摘要在长文上退化） ──
MAX_SENTENCES = 150          # 参与打分的最大句子数
MAX_WINDOW_SENTENCES = 120   # 参与窗口搜索的最大句子数
MAX_SUMMARY_INPUT = 12000    # 参与抽取的最大字符数
MIN_SUMMARY_INPUT = 100      # 低于此长度不生成摘要
MIN_FALLBACK_INPUT = 50      # 低于此长度直接留空

# ── 长度默认值（可被 config/settings.json 的 summary 段覆盖）──
DEFAULT_MAX_CHARS = 500
DEFAULT_SHORT_MAX_CHARS = 300
DEFAULT_SHORT_CATEGORIES = ("④ 政策法规与标准框架", "⑤ 产业动态与技术趋势")
DEFAULT_MIN_CHARS = 80

# 摘要来源标记
KIND_EXTRACTIVE = "extractive"   # 正常提炼（选中了一段连续内容）
KIND_FALLBACK = "fallback"       # 兜底：句子太少，退化为原文开头节选
KIND_EMPTY = "empty"             # 无可用文本

# ── 网页噪声：短行命中即丢弃（导航、分享、订阅、版权、日期、裸网址…）──
_NOISE_PATTERNS = (
    r"^\s*(share|tweet|print|email|featured|home|about|contact|topics|resources|events|webinars|menu|search|blog|news)\b",
    r"\b(share on|share via|share by email)\b",
    r"\b(subscribe|newsletter|sign up|read more|learn more|download)\b",
    r"\b(cookie|advertisement|sponsored|all rights reserved|copyright|privacy policy|terms of service)\b",
    r"^(by|author|posted|written)\b",
    r"\bby\s+[A-Z][a-z]+\s+[A-Z][a-z]+\b",
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},?\s*\d{4}\b",
    r"^\s*\d+\s*(min read|minute read|分钟|阅读)",
    r"https?://\S+",
    r"^\s*[\|\-–—·•]\s*$",
    r"^(推荐理由|正文|AI 导读|编者按|导读|关于我们|责任编辑|登录|注册|订阅|点击)",
    r"(linkedin\.com|twitter\.com|facebook\.com)",
    r"(skip this ad|you can skip|广告)",
    r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}",
)

# 信源专属噪声：不看行长，命中即丢（该信源的聚合壳/编辑推荐语，不属于原文内容）
_SOURCE_EXTRA_NOISE = {
    "AI Hot": (r"^\s*(推荐理由|AI 导读|正文|精选|AI 评分)",),
}

# 聚合器/编辑推荐的标记行：整行（含其后的推荐语）不属于原文内容，直接丢弃。
# 形如「推荐理由」（独占一行，正文在下一行）或「推荐理由 原文梳理了…」（同一行）两种都覆盖。
_META_MARKER_RE = re.compile(
    r"^\s*(推荐理由|AI 导读|导读|编者按|编辑按)\s*[:：]?\s*(.*)$")

# 论坛/站点的版尾信息：几乎不可能出现在正文里，命中的句子直接整句丢弃
_HARD_DROP_RE = re.compile(
    r"(发表于\s*\d{4}[-/]|阅读\s*[\(（]\s*\d+\s*[\)）]|立即登录|注册登录|分享到|"
    r"转载请注明|点击查看原文|责任编辑\s*[:：])")

# 栏目/署名/图片版权/阅读时长这类"一句话的包装行"：不看行长直接丢整行
# （改前实测：Dark Reading 的摘要开头是「News, news analysis, and commentary…」
#  与「Alexander Culafi , Senior News Writer , Dark Reading Source: … via Getty Images」）
_ALWAYS_DROP_LINE = (
    r"\b(Senior|Contributing|Staff|Guest|Freelance)\s+(News\s+)?Writer\b",
    r"\bSource:\s*\S+\s+via\s+\w+",
    r"\bvia\s+(Getty Images|Shutterstock|AP|Reuters|iStock)\b",
    r"\b\d+\s*(Min Read|Minute Read)\b",
    r"(news analysis,? and commentary|commentary on the latest trends)",
    r"^\s*(all rights reserved|copyright|©)",
    r"^\s*(作者|原文链接|来源|编译|翻译)\s*[:：]",
    r"^\s*please enable javascript",
    r"^\s*(abstract|摘要|导读)\s*$",
)

# 零宽字符（网页复制残留）与 Markdown 痕迹
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
_MD_LINK_RE = re.compile(r"\[([^\]\n]{1,80})\]\((?:[^)\n]*)\)")
_MD_HEADING_RE = re.compile(r"(?m)^\s*#{1,6}\s+")
_MD_BOLD_RE = re.compile(r"\*\*([^*\n]{1,80})\*\*")

# 句首包装语（作者：/原文链接：/来源：等）与 URL：逐段剥掉，直到句子开头是正文
_PREFIX_SCRUB_RE = re.compile(
    r"^\s*(作者|原文链接|来源网站|来源|编译|翻译|发布|日期|时间|Author|Authors|Source|By)\s*[:：]?\s*")
_URL_PREFIX_RE = re.compile(r"^\s*https?://\S+\s*")
# 正文起点标记：出现即把标记之前的内容整段切掉（如「…原文链接：… 摘要 未来的生成式…」）
_CONTENT_MARKER_RE = re.compile(r"(?:^|[\s|｜])(?:摘要|内容摘要|正文|Abstract)\s*[:：]?\s*")


def _scrub_sentence(s: str) -> str:
    """剥掉句子开头的元信息包装（作者、原文链接、摘要标记等）"""
    for _ in range(6):
        before = s
        s = _PREFIX_SCRUB_RE.sub("", s, count=1).strip()
        s = _URL_PREFIX_RE.sub("", s, count=1).strip()
        marker = _CONTENT_MARKER_RE.search(s[:160])
        if marker:
            s = s[marker.end():].strip()
        if s == before:
            break
    return s

_ABBR_RE = re.compile(
    r"\b(U\.S|U\.K|Mr|Ms|Mrs|Dr|Inc|Ltd|Co|Corp|vs|etc|eg|ie|No|Vol|"
    r"Oct|Nov|Dec|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|St|Jr|al)\.", re.I)
_DOT_PLACEHOLDER = "\u0001"

# 句子结尾标点（判断"这一行到底是不是一句正经话"）
_SENT_END_RE = re.compile(r"[。！？.!?…][\"'”’)\]]?\s*$")
# 正文开头常见的元信息行（作者、时间、评分、栏目名等）
_LEADING_META_MAX = 150

# 中文停用词表（基础）
_STOP_WORDS: set[str] = set()


def _load_stop_words():
    if not _STOP_WORDS:
        _STOP_WORDS.update({
            "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都",
            "一", "一个", "上", "也", "很", "到", "说", "要", "去", "你",
            "会", "着", "没有", "看", "好", "自己", "这", "他", "她", "它",
            "们", "那", "里", "为", "与", "及", "等", "或", "但", "而",
            "从", "被", "把", "对", "以", "之", "所", "其", "中", "将",
            "并", "个", "两", "多", "少", "只", "已", "还", "又", "再",
            "能", "可", "该", "此", "每", "某", "各", "几", "哪", "何",
            "让", "使", "用", "做", "成", "如", "比", "向", "同", "跟",
            "a", "an", "the", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "can", "could", "may", "might", "shall", "should",
            "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "as", "into", "through", "during", "before", "after", "about",
            "this", "that", "these", "those", "it", "its", "they", "them",
            "their", "we", "our", "you", "your", "he", "she", "his", "her",
        })


# ────────────────────────────── 配置 ──────────────────────────────

def _load_summary_config() -> dict:
    """读取 config/settings.json 的 summary 段（缺省时用默认值）"""
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        section = cfg.get("summary", {})
        return section if isinstance(section, dict) else {}
    except Exception:
        return {}


def _positive_int(value, fallback: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return fallback
    return v if v > 0 else fallback


def summary_max_chars(category: str = "", cfg: dict | None = None) -> int:
    """按分类返回摘要长度上限（分档由 settings.json 的 summary 段控制）"""
    cfg = cfg if cfg is not None else _load_summary_config()
    base = _positive_int(cfg.get("max_chars"), DEFAULT_MAX_CHARS)
    short_cats = cfg.get("short_categories")
    if not isinstance(short_cats, list) or not short_cats:
        short_cats = list(DEFAULT_SHORT_CATEGORIES)
    if category and any(category == str(c) for c in short_cats):
        return _positive_int(cfg.get("short_max_chars"), DEFAULT_SHORT_MAX_CHARS)
    return base


def summary_min_chars(cfg: dict | None = None) -> int:
    cfg = cfg if cfg is not None else _load_summary_config()
    return _positive_int(cfg.get("min_chars"), DEFAULT_MIN_CHARS)


# ────────────────────────────── 清洗 ──────────────────────────────

def _clean_text(text: str, source_name: str = "") -> str:
    """剥离网页噪声与开头元信息行；若清洗后什么都不剩则回退原文"""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = _ZERO_WIDTH_RE.sub("", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_HEADING_RE.sub("", text)
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = re.sub(r"[ \t\u00a0]+", " ", text)

    extra = _SOURCE_EXTRA_NOISE.get(source_name, ())
    lines: list[str] = []
    raw_lines = [ln.strip() for ln in text.split("\n")]
    idx = 0
    while idx < len(raw_lines):
        line = raw_lines[idx]
        idx += 1
        if not line:
            continue
        # 聚合器标记行：「推荐理由」独占一行时连同下一行的推荐语一起丢弃
        marker = _META_MARKER_RE.match(line)
        if marker:
            if not marker.group(2).strip() and idx < len(raw_lines):
                idx += 1
            continue
        if extra and any(re.search(p, line, re.I) for p in extra):
            continue
        # 包装行（署名/版权/阅读时长/站内标语）：无论多长都丢，但超长行可能是被
        # 压成一行的正文，故只对 300 字以内的行生效
        if len(line) < 300 and any(re.search(p, line, re.I) for p in _ALWAYS_DROP_LINE):
            continue
        if len(line) < 80 and any(re.search(p, line, re.I) for p in _NOISE_PATTERNS):
            continue
        lines.append(line)

    # 剥掉开头的元信息行：短、且不是一句完整的话（作者/时间/评分/标题行）
    while lines and len(lines[0]) < _LEADING_META_MAX and not _SENT_END_RE.search(lines[0]):
        lines.pop(0)

    cleaned = "\n".join(lines).strip()
    return cleaned if cleaned else re.sub(r"[ \t\u00a0]+", " ", text).strip()


def _split_sentences(text: str) -> list[str]:
    """切分为句子；对英文缩写与版本号做保护，避免把 "U.S." 之类切成两句"""
    text = re.sub(r"\s+", " ", text)
    text = _ABBR_RE.sub(lambda m: m.group(1) + _DOT_PLACEHOLDER, text)
    text = re.sub(r"(\d)\.(\d)", r"\1" + _DOT_PLACEHOLDER + r"\2", text)
    raw = re.split(r"(?<=[。！？!?])\s*|(?<=\.)\s+(?=[A-Z0-9“\"(])", text)
    sentences = []
    for s in raw:
        s = s.replace(_DOT_PLACEHOLDER, ".").strip()
        if not s:
            continue
        if len(re.sub(r"[^\w\u4e00-\u9fff]", "", s)) < 6:
            continue
        sentences.append(s)
    return sentences


def _is_meaningful(sent: str) -> bool:
    """过滤掉无意义的句子"""
    lower = sent.strip().lower()
    if len(lower) < 15:
        return False
    skip_patterns = [
        r"^(copyright|©|all rights reserved|登录|注册|订阅|点击)",
        r"(subscribe|newsletter|sign up|follow us|@)",
        r"^(home|about|contact|privacy)",
        r"^(is a|is an|是一位|是一名|are a|is the)",
        r"(skip this ad|you can skip|广告)",
        r"(linkedin\.com|twitter\.com|facebook\.com)",
        r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}",
    ]
    for p in skip_patterns:
        if re.search(p, lower):
            return False
    return True


# ────────────────────────────── 打分 ──────────────────────────────

def _tokenize(text: str) -> set[str]:
    """结巴分词，返回去停用词的词集（长度≥2的有效词）"""
    _load_stop_words()
    words = jieba.lcut(text)
    return {w.lower().strip() for w in words
            if w.strip() and w.lower().strip() not in _STOP_WORDS and len(w.strip()) > 1}


def _sentence_similarity(words_i: set[str], words_j: set[str]) -> float:
    """Jaccard 相似度"""
    if not words_i or not words_j:
        return 0
    union = len(words_i | words_j)
    return len(words_i & words_j) / union if union else 0


def _textrank(tokenized: list[set[str]], damping: float = 0.85,
              max_iter: int = 200, tol: float = 1e-4) -> list[float]:
    """TextRank 图排序，返回每个句子的 PageRank 分数（纯 Python 实现，规模已限）"""
    import numpy as np

    n = len(tokenized)
    if n == 0:
        return []
    if n == 1:
        return [1.0]

    sim = np.zeros((n, n))
    for i in range(n):
        ti = tokenized[i]
        if not ti:
            continue
        for j in range(i + 1, n):
            s = _sentence_similarity(ti, tokenized[j])
            if s:
                sim[i, j] = s
                sim[j, i] = s

    col_sums = sim.sum(axis=0)
    for j in range(n):
        if col_sums[j] > 0:
            sim[:, j] /= col_sums[j]
        else:
            sim[:, j] = 1.0 / n

    pr = np.ones(n) / n
    for _ in range(max_iter):
        prev = pr.copy()
        pr = (1 - damping) / n + damping * sim.dot(pr)
        if np.linalg.norm(pr - prev, 1) < tol:
            break

    return pr.tolist()


def _score_sentences(tokenized: list[set[str]], title_tokens: set[str],
                     source_name: str = "") -> list[float]:
    """句子最终得分：TextRank × 位置加成 × 标题相关加成 − 噪声/标题行/过短惩罚"""
    scores = _textrank(tokenized)
    n = len(scores)
    extra = _SOURCE_EXTRA_NOISE.get(source_name, ())
    for i, tokens in enumerate(tokenized):
        # 导语位置加成（越靠前越可能是要点）
        scores[i] *= 1.0 + 0.30 * max(0.0, 1.0 - i / max(1.0, n * 0.2))
        # 与标题相关加成
        if title_tokens and tokens:
            scores[i] *= 1.0 + 0.40 * _sentence_similarity(tokens, title_tokens)
            # 与小标题/标题行本身高度重合的，多半是栏目名而不是正文
            if _sentence_similarity(tokens, title_tokens) > 0.5:
                scores[i] *= 0.3
        # 过短的行（导航残渣、小标题）降权
        if len(tokens) <= 4:
            scores[i] *= 0.5
    return scores


def _sentence_noise_penalty(sent: str, source_name: str = "") -> float:
    """句子含网页噪声时大幅降权"""
    if source_name and source_name in _SOURCE_EXTRA_NOISE:
        if any(re.search(p, sent, re.I) for p in _SOURCE_EXTRA_NOISE[source_name]):
            return 0.15
    if any(re.search(p, sent, re.I) for p in _NOISE_PATTERNS):
        return 0.15
    return 1.0


# ────────────────────────────── 选取与裁剪 ──────────────────────────────

def _sentence_sep(sent: str) -> str:
    """拼接分隔符：中文不加空格，英文加空格（原实现统一用空串，英文句子会粘连）"""
    return "" if re.search(r"[\u4e00-\u9fff]", sent) else " "


def _fit_budget(sentences: list[str], max_chars: int) -> tuple[str, str]:
    """按整句累加到预算为止，避免半句截断。返回 (文本, 收尾方式)"""
    parts: list[str] = []
    used = 0
    for s in sentences:
        add = (_sentence_sep(s) if parts else "") + s
        if used + len(add) > max_chars:
            if not parts:
                # 单句就超预算：只能硬裁，但保证有省略号提示
                return s[: max(1, max_chars - 1)].rstrip() + "…", "硬裁"
            break
        parts.append(add)
        used += len(add)
    return "".join(parts).strip(), "句边界"


def _best_window(sentences: list[str], scores: list[float], max_chars: int) -> tuple[int, int]:
    """在句子序列上滑窗，返回"总分最高且不超预算"的连续区间 [i, j]"""
    n = len(sentences)
    soft_cap = max(120.0, max_chars * 0.4)   # 长度收益的封顶值：够长即可，避免一味求长
    best, best_val = (0, 0), -1.0
    for i in range(n):
        used = 0
        running = 0.0
        for j in range(i, n):
            running += scores[j]
            used += len(sentences[j]) + (1 if j > i else 0)
            if used > max_chars:
                break
            length_factor = min(used, soft_cap) / soft_cap
            val = running / (j - i + 1) ** 0.5 * length_factor
            if val > best_val:
                best, best_val = (i, j), val
    return best


def _summarize(text: str, title: str = "", source_name: str = "",
               max_chars: int = DEFAULT_MAX_CHARS,
               min_chars: int = DEFAULT_MIN_CHARS) -> tuple[str, str]:
    """生成抽取式摘要，返回 (摘要文本, 来源标记)"""
    if not text or len(text.strip()) < MIN_FALLBACK_INPUT:
        return "", KIND_EMPTY

    work = _clean_text(text[:MAX_SUMMARY_INPUT], source_name)
    if len(work.strip()) < MIN_FALLBACK_INPUT:
        return "", KIND_EMPTY

    sentences = [_scrub_sentence(s) for s in _split_sentences(work)]
    sentences = [s for s in sentences if s and _is_meaningful(s)]
    sentences = [s for s in sentences if not _HARD_DROP_RE.search(s)]
    # 正文里重复一遍标题的句子（很多站点先给标语再给标题）：整句丢弃
    if title and sentences:
        title_key = re.sub(r"[^\w\u4e00-\u9fff]", "", title)[:40]
        if len(title_key) >= 12:
            kept = [s for s in sentences
                    if not re.sub(r"[^\w\u4e00-\u9fff]", "", s).startswith(title_key)]
            if kept:
                sentences = kept
    if not sentences:
        # 清洗后没有可用句子：退回原文开头的完整句
        fallback = [_scrub_sentence(s) for s in _split_sentences(text[:MAX_SUMMARY_INPUT])]
        fallback = [s for s in fallback
                    if s and _is_meaningful(s) and not _HARD_DROP_RE.search(s)]
        if not fallback:
            return "", KIND_EMPTY
        body, _ = _fit_budget(fallback, max_chars)
        return body, KIND_FALLBACK

    # 预算很小或句子很少时不做窗口搜索，直接取开头的完整句
    if len(sentences) <= 3:
        body, _ = _fit_budget(sentences, max_chars)
        return body, KIND_FALLBACK

    window_sentences = sentences[:MAX_WINDOW_SENTENCES]
    tokenized = [_tokenize(s) for s in window_sentences]
    title_tokens = _tokenize(title) if title else set()
    scores = _score_sentences(tokenized, title_tokens, source_name)
    scores = [s * _sentence_noise_penalty(window_sentences[i], source_name)
              for i, s in enumerate(scores)]

    i, j = _best_window(window_sentences, scores, max_chars)
    body, _how = _fit_budget(window_sentences[i:j + 1], max_chars)

    if len(body) < min_chars:
        # 选出的内容过短：退回"原文开头的完整句"，但仍然是完整句而不是硬裁
        head, _ = _fit_budget(window_sentences, max_chars)
        if len(head) > len(body):
            body = head
        if len(body) < min_chars:
            return body, KIND_FALLBACK

    return body, KIND_EXTRACTIVE


def generate_extractive_summary(text: str, max_sentences: int = 5) -> str:
    """兼容旧调用：仅返回摘要文本。

    2026-10-08 起内部改为"最优连续窗口"，`max_sentences` 不再参与决策，
    仅为保持既有调用签名而保留。
    """
    return _summarize(text, "", "", DEFAULT_MAX_CHARS)[0]


def _count_chinese(text: str) -> int:
    """统计中文字符数"""
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def _is_chinese_text(text: str) -> bool:
    """检测文本是否主要是中文（>30% 字符为中文）"""
    if not text:
        return False
    head = text[:200]
    return _count_chinese(head) > len(head) * 0.3 if head else False


def _pick_source_text(item: dict) -> str:
    """选择用于抽取摘要的源文本。

    - 摘要曾被全文替换过（original_summary 存在）→ 用替换后的全文
    - 否则若有更长的 full_body → 用 full_body
    - 都没有 → 用摘要本身
    """
    summary = item.get("summary") or ""
    if item.get("original_summary"):
        return summary
    body = item.get("full_body") or ""
    if body and len(body) > len(summary) * 2:
        return body
    return summary


def process(items: list[dict], config: dict) -> list[dict]:
    """为每条内容生成摘要（不做翻译，翻译由后续翻译步骤统一处理）"""
    enabled = config.get("enabled", False)
    provider = config.get("provider", "extractive")

    if enabled and provider != "extractive":
        api_key = config.get("api_key", "")
        if not api_key:
            print("[LLM] LLM 已启用但未配置 API Key，回退到抽取式摘要")
            enabled = False
    if enabled and provider != "extractive":
        print(f"[LLM] 注意: provider={provider} 的外部调用尚未实现，本次仍使用抽取式摘要")

    summary_cfg = _load_summary_config()
    min_chars = summary_min_chars(summary_cfg)
    base_max = summary_max_chars("", summary_cfg)
    short_max = summary_max_chars("④ 政策法规与标准框架", summary_cfg)
    print(f"[LLM] 摘要长度上限: {base_max} 字"
          + (f"（④/⑤ 类为 {short_max} 字）" if short_max != base_max else "")
          + f"，最短 {min_chars} 字；来源标记写入 ai_summary_kind")

    total = len(items)
    kinds = {KIND_EXTRACTIVE: 0, KIND_FALLBACK: 0, KIND_EMPTY: 0}

    for idx, item in enumerate(items):
        source_text = _pick_source_text(item)
        max_chars = summary_max_chars(item.get("category", ""), summary_cfg)
        if not source_text or len(source_text.strip()) < MIN_FALLBACK_INPUT:
            item["ai_summary"] = ""
            item["ai_summary_kind"] = KIND_EMPTY
            kinds[KIND_EMPTY] += 1
            continue

        ai_summary, kind = _summarize(
            source_text,
            title=item.get("title") or "",
            source_name=item.get("source_name") or "",
            max_chars=max_chars,
            min_chars=min_chars,
        )
        item["ai_summary"] = ai_summary
        item["ai_summary_kind"] = kind
        kinds[kind] = kinds.get(kind, 0) + 1

        if (idx + 1) % 20 == 0:
            print(f"  [LLM] 进度: {idx+1}/{total}")

    print(f"[LLM] 摘要生成完成: {total - kinds[KIND_EMPTY]}/{total} 条生成了摘要"
          f"（自动提炼 {kinds[KIND_EXTRACTIVE]}，原文节选 {kinds[KIND_FALLBACK]}，"
          f"无摘要 {kinds[KIND_EMPTY]}）")
    return items


def _call_llm(text: str, config: dict) -> str:
    """调用外部 LLM API 生成摘要（预留实现）"""
    provider = config.get("provider", "openai")
    prompt = config.get("prompt_template", "").format(
        title="", content=text[:4000]
    )
    if provider == "openai":
        import openai
        client = openai.OpenAI(
            api_key=config.get("api_key", ""),
            base_url=config.get("base_url") or None,
        )
        resp = client.chat.completions.create(
            model=config.get("model", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=300,
        )
        return resp.choices[0].message.content.strip()
    elif provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=config.get("api_key", ""))
        resp = client.messages.create(
            model=config.get("model", "claude-sonnet-4-20250514"),
            max_tokens=300,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    else:
        raise ValueError(f"不支持的 LLM provider: {provider}")


def run():
    """管道调用入口"""
    if not LLM_CONFIG_PATH.exists():
        raise FileNotFoundError(f"缺少 LLM 配置 {LLM_CONFIG_PATH}")
    with open(LLM_CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    if not CLASSIFIED_ITEMS_PATH.exists():
        raise FileNotFoundError(
            f"缺少阶段2产物 {CLASSIFIED_ITEMS_PATH}，无法生成摘要（请检查评分步骤）")

    with open(CLASSIFIED_ITEMS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    result = process(items, config)

    atomic_write(ENHANCED_ITEMS_PATH, result, indent=2)
    return result


if __name__ == "__main__":
    run()
