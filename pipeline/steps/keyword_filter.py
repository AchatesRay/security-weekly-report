"""关键字过滤模块 — 两阶段评分过滤

基于 SecurityScorer 的加权评分机制：

阶段 1（去重后）：快速预筛，用标题+前200字评分，<30 分提前丢弃
阶段 2（全文提取后）：完整评分 + 领域分类 + 阈值判定

数据流（2026-09-29 修复：阶段产物独立留档）：
    去重结果 deduped_items.json --[阶段1]--> parsed_items.json
                                --[全文提取，原地增强]-->
                                --[阶段2]--> classified_items.json
    此前阶段 2 直接覆盖 parsed_items.json，各阶段产物混在一个文件里，
    某一阶段失败时下游会读到上一轮遗留的数据，导致“用旧数据重发周报”。

关键字配置存储在 config/scoring_keywords.json 中。
"""

import json
import re
from urllib.parse import urlparse

from .scorer import SecurityScorer, SCORING_CONFIG_PATH
from ..utils import DATA_DIR, atomic_write

PARSED_ITEMS_PATH = DATA_DIR / "parsed_items.json"
DEDUPED_ITEMS_PATH = DATA_DIR / "deduped_items.json"
CLASSIFIED_ITEMS_PATH = DATA_DIR / "classified_items.json"

# 默认网络安全关键字列表（兼容旧版 API）
DEFAULT_KEYWORDS = sorted([
    "security", "cybersecurity", "vulnerability", "cve", "漏洞",
    "attack", "exploit", "malware", "ransomware", "攻击", "恶意软件",
    "勒索", "木马", "后门", "数据泄露", "钓鱼", "黑客",
    "入侵", "渗透", "防火墙", "加密", "补丁",
])


def load_keywords() -> list[str]:
    """从 scoring_keywords.json 读取强特征词文本列表（兼容旧版 API）"""
    try:
        with open(SCORING_CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("strong", {}).get("keywords", [])
        # 兼容新旧格式：新格式是 [{text: ...}, ...]，旧格式是 [str, ...]
        return [kw["text"] if isinstance(kw, dict) else kw for kw in raw]
    except Exception:
        return []


def save_keywords(keywords: list[str]) -> bool:
    """保存关键字文本列表到 scoring_keywords.json 的 strong 字段（兼容旧版 API）

    写入前校验结构完整性；使用原子替换，避免中断留下截断的 JSON。
    """
    import json as _json
    try:
        with open(SCORING_CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        # 保留现有元数据（categories/content_types），只更新 text
        existing = {kw["text"].lower(): kw
                    for kw in data["strong"]["keywords"] if isinstance(kw, dict)}
        new_kws = []
        for kw in keywords:
            t = (kw or "").strip()
            if not t:
                continue
            if t.lower() in existing:
                new_kws.append(existing[t.lower()])
            else:
                new_kws.append({"text": t})
        # 按 text 排序
        new_kws.sort(key=lambda x: x["text"])
        if not new_kws:
            print("[KEYWORD] 拒绝写入空的关键词列表")
            return False
        data["strong"]["keywords"] = new_kws
        from ..utils import atomic_write_text
        atomic_write_text(
            SCORING_CONFIG_PATH,
            _json.dumps(data, ensure_ascii=False, indent=2),
        )
        return True
    except Exception as e:
        print(f"[KEYWORD] 保存关键字失败: {e}")
        return False


def init_default_keywords() -> bool:
    """确认评分配置存在且可用。

    原实现把“默认模板路径”和“目标路径”指向同一个文件，`if not exists`
    永远为假（空操作）；一旦从其他目录启动，又会相对当前工作目录创建
    config/ 并把配置写到错误位置。这里改为显式检查并如实报告。
    """
    if SCORING_CONFIG_PATH.exists():
        return True
    print(f"[KEYWORD] 评分配置缺失: {SCORING_CONFIG_PATH}")
    print("[KEYWORD] 该文件随仓库提供，请确认工作副本完整（缺失会导致评分不可用）")
    return False


# 关注源直采：栏目 → 主题分类（仅在评分为「未分类」时补位，不覆盖已有分类）
PASS_THROUGH_SECTION_CATEGORY = {
    "cybersecurity-operations": "⑤ 产业动态与技术趋势",
    "cyber-risk": "⑤ 产业动态与技术趋势",
    "cyberattacks-data-breaches": "② 威胁情报与攻防对抗",
    "endpoint-security": "② 威胁情报与攻防对抗",
    "mobile-security": "② 威胁情报与攻防对抗",
    "ics-ot-security": "② 威胁情报与攻防对抗",
    "application-security": "③ 漏洞态势与供应链安全",
}


def _kw_hit(text: str, kw: str) -> bool:
    """ASCII 关键词按词边界匹配（避免 AI 命中 said/email），中文按子串。"""
    if not kw:
        return False
    if kw.isascii():
        return re.search(r"(?<![A-Za-z0-9])" + re.escape(kw) + r"(?![A-Za-z0-9])",
                         text, re.I) is not None
    return kw in text


def _pass_through_rule(item: dict) -> dict | None:
    """命中关注源直采规则时返回命中信息，否则返回 None。

    规则来自 source_config.yaml 的 pass_through 段，由 parser 随条目下沉到
    item["source_flags"]。设计意图：观点/访谈类内容摘要极短、没有攻击技术词，
    关键词体系天然识别不了，因此按源 + 栏目先放行到「待复核」等人工过目，
    主榜的 80 分线与词表完全不动。
    """
    flags = item.get("source_flags") or {}
    cfg = flags.get("pass_through")
    if not isinstance(cfg, dict) or not cfg.get("enabled"):
        return None

    url_path = urlparse(item.get("url") or "").path.strip("/")
    section = url_path.split("/")[0] if url_path else ""
    text = f'{item.get("title", "")} {item.get("summary", "")}'
    hits = [kw for kw in (cfg.get("keywords") or []) if _kw_hit(text, kw)]
    section_hit = bool(section) and section in (cfg.get("sections") or [])
    if not hits and not section_hit:
        return None

    reason = []
    if section_hit:
        reason.append(f"栏目 {section}")
    if hits:
        reason.append("关键词 " + "/".join(hits))
    return {
        "section": section,
        "hits": hits,
        "reason": "；".join(reason),
        "max_items": int(cfg.get("max_items") or 6),
    }


def run_stage1():
    """阶段1过滤：快速预筛，<30 分提前丢弃（读取去重结果）"""
    init_default_keywords()

    if DEDUPED_ITEMS_PATH.exists():
        source = DEDUPED_ITEMS_PATH
    elif PARSED_ITEMS_PATH.exists():
        source = PARSED_ITEMS_PATH
    else:
        raise FileNotFoundError(
            f"缺少去重产物 {DEDUPED_ITEMS_PATH}，无法执行阶段1（请检查去重步骤）")

    scorer = SecurityScorer()

    with open(source, "r", encoding="utf-8") as f:
        items = json.load(f)

    total_before = len(items)
    kept = []
    dropped = 0
    drop_threshold = scorer.thresholds.get("stage1_drop_below", 30)

    for item in items:
        result = scorer.quick_score(item)
        item["stage1_score"] = result["score"]
        item["stage1_drop"] = result["drop"]

        # 关注源直采条目不受阶段1 的 30 分线限制（观点类天然拿不到分）
        pt = _pass_through_rule(item)
        if pt:
            item["pass_through"] = pt
            kept.append(item)
            continue

        if result["drop"]:
            dropped += 1
        else:
            kept.append(item)

    atomic_write(PARSED_ITEMS_PATH, kept, indent=2)

    print(f"[KEYWORD] 阶段1过滤: {total_before} → {len(kept)} 条保留"
          f" ({dropped} 条得分<{drop_threshold} 提前丢弃)")


def run_stage2():
    """阶段2过滤：完整评分 + 领域分类 + 阈值判定，产出 classified_items.json"""
    scorer = SecurityScorer()

    if not PARSED_ITEMS_PATH.exists():
        raise FileNotFoundError(
            f"缺少阶段1产物 {PARSED_ITEMS_PATH}，无法执行阶段2（请检查阶段1是否失败）")

    with open(PARSED_ITEMS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    total_before = len(items)
    accepted = []
    review = []
    discarded = []

    for item in items:
        # 完整评分
        result = scorer.score(item)
        item["confidence_score"] = result["score"]
        # 不截顶的原始证据强度：用于报告内排序（截顶后大量条目同为 100 分）
        item["raw_score"] = result["raw_score"]
        item["confidence_level"] = result["level"]
        item["filter_decision"] = result["decision"]
        item["category"] = result["category"]
        item["content_type"] = result["content_type"]
        item["scoring_reason"] = result["reason"]
        item["scoring_matched"] = result["matched"]
        # 读者画像与可解释字段（排序、详情展示均依赖这些）
        for key in ("keyword_score", "relevance_score", "topic_fit", "depth_score",
                    "commercial_score", "authority_tier", "authority_multiplier",
                    "selection_reason", "score_breakdown",
                    "depth_hits", "commercial_hits", "series_hit"):
            if key in result:
                item[key] = result[key]

        # 关注源直采：不走 80 分线，直接进「待复核」等人工过目。
        # 注：已收录（>=80）的条目不会被降级，避免同一篇在主榜与待复核重复；
        # 商业惩罚 >=30 的（发布稿/软文/会议推广）不放行——否则「Launches」
        # 这类稿子会靠一个 AI 关键词占掉关注配额。
        pt = item.get("pass_through")
        if (pt and item.get("filter_decision") != "accepted"
                and int(item.get("commercial_score") or 0) < 30):
            item["filter_decision"] = "review"
            item["confidence_level"] = "focus"
            item["focus_reason"] = pt.get("reason", "")
            if item.get("category") in (None, "", "未分类"):
                item["category"] = PASS_THROUGH_SECTION_CATEGORY.get(
                    pt.get("section", ""), "⑤ 产业动态与技术趋势")

        if item.get("filter_decision") == "accepted":
            accepted.append(item)
        elif item.get("filter_decision") == "review":
            review.append(item)
        else:
            discarded.append(item)

    # 关注源直采配额：同一信源每期最多 max_items 条，
    # 排序口径「关键词命中数降序 → 发布时间降序」，超出的退回丢弃。
    focus_items = [i for i in review if i.get("focus_reason")]
    if focus_items:
        overflow: set[int] = set()
        per_source: dict[str, int] = {}
        for it in sorted(focus_items,
                         key=lambda x: (len((x.get("pass_through") or {}).get("hits") or []),
                                        x.get("published_date") or ""),
                         reverse=True):
            src = it.get("source_name") or ""
            cap = int((it.get("pass_through") or {}).get("max_items") or 6)
            if per_source.get(src, 0) >= cap:
                it["filter_decision"] = "filtered"
                it["focus_reason"] = ""
                overflow.add(id(it))
                continue
            per_source[src] = per_source.get(src, 0) + 1
        if overflow:
            review = [i for i in review if id(i) not in overflow]
        print(f"[KEYWORD] 关注源直采: {len(focus_items) - len(overflow)} 条进待复核"
              + (f"，{len(overflow)} 条超配额丢弃" if overflow else ""))

    # 写回：accepted + review 都保留继续走管道（字段已带 filter_decision 区分）
    final = accepted + review
    atomic_write(CLASSIFIED_ITEMS_PATH, final, indent=2)

    accept_threshold = scorer.thresholds.get("accept_threshold", 80)
    review_threshold = scorer.thresholds.get("review_threshold", 50)
    print(f"[KEYWORD] 阶段2过滤: {total_before} → {len(final)} 条保留"
          f" ({len(accepted)} 收录>={accept_threshold}, "
          f"{len(review)} 待复核>={review_threshold}, {len(discarded)} 丢弃)")

    # ── 评分质量仪表盘 ──
    # 统计口径：收录 + 待复核 + 丢弃（此前漏掉 review，导致周对比的百分比失真）
    all_scored = accepted + review + discarded

    # 分数段分布
    buckets = [0] * 11  # 0-9, 10-19, ..., 90-100
    for item in all_scored:
        s = item.get("confidence_score", 0)
        try:
            idx = min(max(int(s), 0) // 10, 10)
        except (TypeError, ValueError):
            idx = 0
        buckets[idx] += 1

    print(f"[KEYWORD] 评分分布:")
    for i in range(11):
        lo, hi = i * 10, min(i * 10 + 9, 100)
        count = buckets[i]
        if count > 0 or i in (0, 5, 8, 10):
            bar = "█" * min(count, 20) + ("…" if count > 20 else "")
            print(f"  {lo:3d}-{hi:3d}: {count:3d} {bar}")

    # 分发决策分布
    print(f"[KEYWORD] 决策分布: accepted={len(accepted)}, "
          f"review={len(review)}, discarded={len(discarded)}")

    # 分类分布
    cat_dist = {}
    for item in final:
        cat = item.get("category", "未分类")
        cat_dist[cat] = cat_dist.get(cat, 0) + 1
    if cat_dist:
        print(f"[KEYWORD] 分类分布:")
        for cat, count in sorted(cat_dist.items(), key=lambda x: -x[1]):
            print(f"  {cat}: {count}")

    # 保存本轮统计数据供后续对比
    stats = {
        "total_input": total_before,
        "total_accepted": len(accepted),
        "total_review": len(review),
        "total_discarded": len(discarded),
        "score_buckets": buckets,
        "category_distribution": cat_dist,
        "thresholds": {
            "accept_threshold": accept_threshold,
            "review_threshold": review_threshold,
            "stage1_drop_below": scorer.thresholds.get("stage1_drop_below", 30),
        },
    }
    atomic_write(DATA_DIR / "scoring_stats.json", stats, indent=2)


if __name__ == "__main__":
    init_default_keywords()
    scorer = SecurityScorer()
    print(f"评分引擎就绪，当前强特征词: {len(scorer.strong_kw_list)} 个")
    print(f"中特征词: {len(scorer.medium_kw_list)} 个")
    print(f"弱特征词: {len(scorer.weak_kw_list)} 个")
