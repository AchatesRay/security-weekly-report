"""
LLM 摘要模块 — 对获取到原文的内容生成中文摘要

支持两种模式:
  1. 抽取式摘要（默认） — 基于句子评分，无需外部 API
  2. LLM 摘要（配置启用） — 调用外部 LLM API

管道位置: 翻译步骤之后，报告生成之前
"""

import json
import re
import yaml
from pathlib import Path

DATA_DIR = Path("data")
CONFIG_PATH = Path("config/llm_config.yaml")
TRANSLATED_ITEMS_PATH = DATA_DIR / "translated_items.json"
ENHANCED_ITEMS_PATH = DATA_DIR / "enhanced_items.json"

# 安全领域关键词（中英文）
SECURITY_KEYWORDS = {
    "攻击", "漏洞", "威胁", "安全", "恶意", "入侵", "泄露", "数据", "网络",
    "钓鱼", "勒索", "病毒", "木马", "后门", "0day", "补丁", "加密", "权限",
    "attack", "vulnerability", "threat", "security", "malicious", "breach",
    "exploit", "malware", "ransomware", "phishing", "backdoor", "patch",
    "encryption", "privilege", "authentication", "zero-day", "supply chain",
}

# 提示性词语（包含这些词的句子更可能是关键句）
CUE_WORDS = {
    "发现", "报告", "披露", "警告", "分析", "研究", "表明", "证实",
    "发现", "影响", "涉及", "导致", "建议", "紧急", "严重", "高危",
    "discover", "report", "disclose", "warn", "analyze", "research",
    "reveal", "affect", "impact", "critical", "urgent", "important",
    "according", "researcher", "found", "identified", "observed",
}


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


def _score_sentence(sent: str, position: float, total: int) -> float:
    """对单个句子进行评分"""
    score = 0.0
    lower = sent.lower()

    # 1. 位置分：前 30% 的句子有加分
    if position < 0.3:
        score += 2.0 * (1 - position / 0.3)
    elif position < 0.6:
        score += 0.5

    # 2. 安全关键词分
    kw_count = sum(1 for kw in SECURITY_KEYWORDS if kw.lower() in lower)
    score += kw_count * 1.5

    # 3. 提示词分
    cue_count = sum(1 for cw in CUE_WORDS if cw.lower() in lower)
    score += cue_count * 2.0

    # 4. 长度分：偏好 30-150 字符的句子
    length = len(sent)
    if 30 <= length <= 150:
        score += 1.5
    elif 150 < length <= 300:
        score += 0.8
    elif length < 20:
        score -= 1.0

    # 5. 数字/统计数据加分（含具体数字的句子通常更有信息量）
    if re.search(r"\d+", sent):
        score += 0.5
    if re.search(r"[%％]|percent|million|billion|thousand", lower):
        score += 1.0

    return score


def _is_meaningful(sent: str) -> bool:
    """过滤掉无意义的句子"""
    lower = sent.strip().lower()
    # 跳过过短的
    if len(lower) < 15:
        return False
    # 跳过导航/版权/登录类文本
    skip_patterns = [
        r"^(copyright|©|all rights reserved|登录|注册|订阅|点击)",
        r"(subscribe|newsletter|sign up|follow us|@)",
        r"^(home|about|contact|privacy)",
        # 作者简介/广告
        r"^(is a|is an|是一位|是一名|are a|is the)",
        r"(skip this ad|you can skip|广告)",
        r"(linkedin\.com|twitter\.com|facebook\.com)",
        # 纯时间/日期行
        r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}",
    ]
    for p in skip_patterns:
        if re.search(p, lower):
            return False
    return True


def generate_extractive_summary(text: str, max_sentences: int = 5) -> str:
    """
    基于句子评分的抽取式摘要
    返回摘要文本（中文）
    """
    if not text or len(text.strip()) < 100:
        return ""

    sentences = _split_sentences(text)
    sentences = [s for s in sentences if _is_meaningful(s)]
    if not sentences:
        return ""

    total = len(sentences)
    scored = []
    for i, sent in enumerate(sentences):
        pos = i / total if total > 1 else 0
        score = _score_sentence(sent, pos, total)
        scored.append((score, i, sent))

    # 按分数降序排列，取 top N
    scored.sort(key=lambda x: -x[0])
    top_indices = sorted(item[1] for item in scored[:max_sentences])

    result = "".join(sentences[i] for i in top_indices)
    # 限制最大长度
    if len(result) > 500:
        result = result[:497] + "..."

    return result.strip()


def _count_chinese(text: str) -> int:
    """统计中文字符数"""
    return len(re.findall(r"[一-鿿]", text))


def process(items: list[dict], config: dict) -> list[dict]:
    """对每条有原文的条目生成中文摘要"""
    enabled = config.get("enabled", False)
    provider = config.get("provider", "extractive")

    # 如果启用了 LLM 但没配置 API key，回退到抽取式
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

        # 仅在获取到原文时才生成摘要，纯 RSS 摘要无需再提取
        has_fulltext = item.get("fulltext_fetched", False)

        if not has_fulltext:
            item["ai_summary"] = ""
            continue

        ai_summary = ""

        if enabled and provider != "extractive":
            try:
                ai_summary = _call_llm(summary, config)
            except Exception as e:
                print(f"  [LLM] LLM 调用失败 ({item.get('title','')[:40]}): {e}")
                ai_summary = ""

        # 回退到抽取式
        if not ai_summary:
            # 如果原文是英文，先用抽取式提取，再翻译
            if language == "en":
                raw_summary = generate_extractive_summary(summary)
                if raw_summary:
                    ai_summary = translate_text(raw_summary)
            else:
                ai_summary = generate_extractive_summary(summary)

        item["ai_summary"] = ai_summary

        if ai_summary:
            summary_count += 1
            ch_count = _count_chinese(ai_summary)
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
    """给 main.py 调用的入口"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if not TRANSLATED_ITEMS_PATH.exists():
        print("[LLM] 无翻译数据，跳过 LLM 摘要生成")
        return

    with open(TRANSLATED_ITEMS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    result = process(items, config)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(ENHANCED_ITEMS_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    run()
