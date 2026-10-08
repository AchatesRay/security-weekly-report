"""LLM/抽取式摘要模块 — 为收录内容生成中文摘要

支持两种模式:
  1. 抽取式摘要（默认） — TextRank 图排序，无需外部 API
  2. LLM 摘要（配置启用） — 调用外部 LLM API

数据流：classified_items.json --[本步骤]--> enhanced_items.json

2026-09-29 修复：
  1. **输入文件改为阶段2 的独立产物** classified_items.json（此前读
     parsed_items.json，与阶段1 的产物混用，某步失败时会读到上一轮旧数据）。
  2. **TextRank 规模上限**：句子相似度是纯 Python 的双重循环，句子数 n 时
     需要 n²/2 次集合运算。全文提取上限 2 万字时可产生数百句，单条就要数秒。
     现在限制参与排序的句子数与输入长度。
  3. **不在本步骤内翻译**：原先对英文摘要在本步骤调用翻译 API，与后续
     翻译步骤重复。现在本步骤只负责“选出关键句”，翻译统一交给翻译步骤，
     职责单一且减少 API 调用。若后续翻译失败，报告会如实标注。
"""

import json
import re

import yaml

from ..utils import (DATA_DIR, LLM_CONFIG_PATH, atomic_write)

CLASSIFIED_ITEMS_PATH = DATA_DIR / "classified_items.json"
ENHANCED_ITEMS_PATH = DATA_DIR / "enhanced_items.json"

import jieba

# ── 计算规模上限（防止抽取式摘要在长文上退化） ──
MAX_SENTENCES = 150          # 参与 TextRank 的最大句子数
MAX_SUMMARY_INPUT = 12000    # 参与抽取的最大字符数
MIN_SUMMARY_INPUT = 100      # 低于此长度不生成摘要
SUMMARY_MAX_CHARS = 500      # 摘要输出上限

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
    text = re.sub(r"\n\s*\n", " ¶ ", text)
    text = re.sub(r"\n", " ", text)

    raw = re.split(r"(?<=[。！？.!?])\s*", text)
    sentences = []
    for s in raw:
        s = s.strip()
        if not s or s == "¶":
            continue
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
    """TextRank 图排序，返回每个句子的 PageRank 分数（纯 Python 实现，规模已限）"""
    import numpy as np

    n = len(sentences)
    if n == 0:
        return []
    if n == 1:
        return [1.0]

    tokenized = [_tokenize(s) for s in sentences]

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


def generate_extractive_summary(text: str, max_sentences: int = 5) -> str:
    """TextRank 抽取式摘要"""
    import numpy as np

    if not text or len(text.strip()) < MIN_SUMMARY_INPUT:
        return ""

    # 限制输入长度与句子数：相似度矩阵是 O(n²) 的纯 Python 运算
    work = text[:MAX_SUMMARY_INPUT]
    sentences = _split_sentences(work)
    sentences = [s for s in sentences if _is_meaningful(s)]
    if not sentences:
        return ""
    if len(sentences) > MAX_SENTENCES:
        sentences = sentences[:MAX_SENTENCES]

    if len(sentences) <= 3:
        return work[:SUMMARY_MAX_CHARS].strip()

    scores = _textrank(sentences)

    n = min(max_sentences, len(sentences))
    top_indices = sorted(np.argsort(scores)[-n:])

    result = "".join(sentences[i] for i in top_indices)
    if len(result) > SUMMARY_MAX_CHARS:
        result = result[:SUMMARY_MAX_CHARS - 3] + "..."

    return result.strip()


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

    total = len(items)
    summary_count = 0

    for idx, item in enumerate(items):
        source_text = _pick_source_text(item)
        if not source_text or len(source_text.strip()) < 50:
            item["ai_summary"] = ""
            continue

        ai_summary = generate_extractive_summary(source_text)
        if not ai_summary:
            ai_summary = source_text[:SUMMARY_MAX_CHARS].strip()

        item["ai_summary"] = ai_summary

        if ai_summary:
            summary_count += 1

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
