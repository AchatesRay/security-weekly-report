"""
LLM 摘要模块 — 对获取到原文的内容生成中文摘要

支持两种模式:
  1. 抽取式摘要（默认） — TextRank 图排序，无需外部 API
  2. LLM 摘要（配置启用） — 调用外部 LLM API

管道位置: 评分过滤阶段2之后，翻译步骤之前
"""

import json
import re
import yaml
from pathlib import Path

DATA_DIR = Path("data")
CONFIG_PATH = Path("config/llm_config.yaml")
PARSED_ITEMS_PATH = DATA_DIR / "parsed_items.json"
ENHANCED_ITEMS_PATH = DATA_DIR / "enhanced_items.json"

import jieba
import numpy as np

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


def _split_sentences(text: str) -> list[str]:
    """将文本分割为句子列表"""
    # 统一换行符为空格，保留段落分隔
    text = re.sub(r"\n\s*\n", " ¶ ", text)
    text = re.sub(r"\n", " ", text)

    # 按中英文句末标点分割
    raw = re.split(r"(?<=[。！？.!?])\s*", text)
    sentences = []
    for s in raw:
        s = s.strip()
        if not s or s == "¶":
            continue
        # 跳过纯标点/空白
        if len(re.sub(r"[^\w]", "", s)) < 3:
            continue
        sentences.append(s.replace("¶ ", "").replace("¶", ""))
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


def _textrank(sentences: list[str], damping: float = 0.85,
              max_iter: int = 200, tol: float = 1e-4) -> list[float]:
    """TextRank 图排序，返回每个句子的 PageRank 分数"""
    n = len(sentences)
    if n == 0:
        return []
    if n == 1:
        return [1.0]

    # 预处理：所有句子分词
    tokenized = [_tokenize(s) for s in sentences]

    # 构建相似度矩阵
    sim = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            s = _sentence_similarity(tokenized[i], tokenized[j])
            sim[i, j] = s
            sim[j, i] = s

    # 列归一化
    col_sums = sim.sum(axis=0)
    for j in range(n):
        if col_sums[j] > 0:
            sim[:, j] /= col_sums[j]
        else:
            sim[:, j] = 1.0 / n

    # PageRank 迭代
    pr = np.ones(n) / n
    for _ in range(max_iter):
        prev = pr.copy()
        pr = (1 - damping) / n + damping * sim.dot(pr)
        if np.linalg.norm(pr - prev, 1) < tol:
            break

    return pr.tolist()


def generate_extractive_summary(text: str, max_sentences: int = 5) -> str:
    """
    TextRank 抽取式摘要
    """
    if not text or len(text.strip()) < 100:
        return ""

    sentences = _split_sentences(text)
    sentences = [s for s in sentences if _is_meaningful(s)]
    if not sentences:
        return ""

    # 句子太少时直接截取前 500 字
    if len(sentences) <= 3:
        return text[:500].strip()

    scores = _textrank(sentences)

    # 取 top-k，按原文顺序重排
    n = min(max_sentences, len(sentences))
    top_indices = sorted(np.argsort(scores)[-n:])

    result = "".join(sentences[i] for i in top_indices)
    if len(result) > 500:
        result = result[:497] + "..."

    return result.strip()


def _count_chinese(text: str) -> int:
    """统计中文字符数"""
    return len(re.findall(r"[一-鿿]", text))


def process(items: list[dict], config: dict) -> list[dict]:
    """为每条内容生成中文摘要

    策略:
      - RSS 已有摘要 → 直接使用（英文则翻译为中文）
      - RSS 摘要过短，已由 fulltext_extractor 抓取到全文 → 从全文抽取关键句（英文则翻译）
      - 无内容 → 不生成摘要
    """
    enabled = config.get("enabled", False)
    provider = config.get("provider", "extractive")

    if enabled and provider != "extractive":
        api_key = config.get("api_key", "")
        if not api_key:
            print("[LLM] LLM 已启用但未配置 API Key，回退到抽取式摘要")
            enabled = False

    from .translator import translate_text

    total = len(items)
    summary_count = 0

    for idx, item in enumerate(items):
        summary = item.get("summary", "")
        language = item.get("language", "")
        has_fulltext = item.get("fulltext_fetched", False)
        # original_summary 存在说明摘要曾被全文替换过
        original_summary = item.get("original_summary", "")

        # 无内容 → 跳过
        if not summary or len(summary.strip()) < 50:
            item["ai_summary"] = ""
            continue

        ai_summary = ""

        # 情况 A: 摘要曾被全文替换 → 从全文中抽取关键句
        if original_summary:
            if language == "en":
                raw = generate_extractive_summary(summary)
                ai_summary = translate_text(raw) if raw else ""
            else:
                ai_summary = generate_extractive_summary(summary)

        # 情况 B: RSS 摘要性内容可直接使用
        else:
            if language == "en":
                ai_summary = translate_text(summary[:800])
            else:
                ai_summary = summary[:500]

        item["ai_summary"] = ai_summary

        if ai_summary:
            summary_count += 1
            ch_count = _count_chinese(ai_summary[:100])
            print(f"  [LLM] ✓ {item.get('source_name','?')}: {item.get('title','')[:40]}... "
                  f"→ {ch_count} 字摘要")

        if (idx + 1) % 20 == 0:
            print(f"  [LLM] 进度: {idx+1}/{total}")

    print(f"[LLM] 摘要生成完成: {summary_count}/{total} 条生成了摘要")
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
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if not PARSED_ITEMS_PATH.exists():
        print("[LLM] 无评分数据，跳过 LLM 摘要生成")
        return

    with open(PARSED_ITEMS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    result = process(items, config)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(ENHANCED_ITEMS_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    run()
