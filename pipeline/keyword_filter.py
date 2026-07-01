"""关键字过滤模块 — 两阶段评分过滤

基于 SecurityScorer 的加权评分机制：

阶段 1（去重后）：快速预筛，用标题+前200字评分，<30 分提前丢弃
阶段 2（全文提取后）：完整评分 + 领域分类 + 阈值判定

关键字配置存储在 config/scoring_keywords.json 中。
"""

import json
from pathlib import Path

from .scorer import SecurityScorer

DATA_DIR = Path("data")
PARSED_ITEMS_PATH = DATA_DIR / "parsed_items.json"
DEDUPED_ITEMS_PATH = DATA_DIR / "deduped_items.json"

# 保留旧的 load_keywords/save_keywords 接口供 config_server 调用
from .scorer import SCORING_CONFIG_PATH

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
    """保存关键字文本列表到 scoring_keywords.json 的 strong 字段（兼容旧版 API）"""
    try:
        with open(SCORING_CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        # 保留现有元数据（categories/content_types），只更新 text
        existing = {kw["text"].lower(): kw for kw in data["strong"]["keywords"] if isinstance(kw, dict)}
        new_kws = []
        for kw in keywords:
            t = kw.strip()
            if not t:
                continue
            if t.lower() in existing:
                new_kws.append(existing[t.lower()])
            else:
                new_kws.append({"text": t})
        # 按 text 排序
        new_kws.sort(key=lambda x: x["text"])
        data["strong"]["keywords"] = new_kws
        with open(SCORING_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def init_default_keywords():
    """确保 scoring_keywords.json 存在并包含默认数据"""
    if not SCORING_CONFIG_PATH.exists():
        # 复制默认配置
        from shutil import copy2
        default = Path(__file__).resolve().parent.parent / "config" / "scoring_keywords.json"
        if default.exists():
            copy2(default, SCORING_CONFIG_PATH)
            print(f"[KEYWORD] 已初始化评分配置: {SCORING_CONFIG_PATH}")


def run_stage1():
    """阶段1过滤：快速预筛，<30 分提前丢弃（读取去重后的数据）"""
    init_default_keywords()
    scorer = SecurityScorer()

    source = DEDUPED_ITEMS_PATH if DEDUPED_ITEMS_PATH.exists() else PARSED_ITEMS_PATH
    if not source.exists():
        print(f"[KEYWORD] 阶段1跳过: {source} 不存在")
        return

    with open(source, "r", encoding="utf-8") as f:
        items = json.load(f)

    total_before = len(items)
    kept = []
    dropped = []

    for item in items:
        result = scorer.quick_score(item)
        item["stage1_score"] = result["score"]
        item["stage1_drop"] = result["drop"]

        if result["drop"]:
            dropped.append(item)
        else:
            kept.append(item)

    from . import atomic_write
    atomic_write(PARSED_ITEMS_PATH, kept, indent=2)

    print(f"[KEYWORD] 阶段1过滤: {total_before} → {len(kept)} 条保留"
          f" ({len(dropped)} 条得分<{scorer.thresholds.get('stage1_drop_below', 30)} 提前丢弃)")


def run_stage2():
    """阶段2过滤：完整评分 + 领域分类 + 阈值判定"""
    scorer = SecurityScorer()

    if not PARSED_ITEMS_PATH.exists():
        print(f"[KEYWORD] 阶段2跳过: {PARSED_ITEMS_PATH} 不存在")
        return

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
        item["confidence_level"] = result["level"]
        item["filter_decision"] = result["decision"]
        item["category"] = result["category"]
        item["content_type"] = result["content_type"]
        item["region"] = result["region"]
        item["scoring_reason"] = result["reason"]
        item["scoring_matched"] = result["matched"]

        if result["decision"] == "accepted":
            accepted.append(item)
        elif result["decision"] == "review":
            review.append(item)
        else:
            discarded.append(item)

    # 写回：保留 accepted + review（review 进入人工审核池）
    final = accepted + review

    from . import atomic_write
    atomic_write(PARSED_ITEMS_PATH, final, indent=2)

    print(f"[KEYWORD] 阶段2过滤: {total_before} → {len(final)} 条保留"
          f" ({len(accepted)} 高置信, {len(review)} 待复核, {len(discarded)} 丢弃)")

    # ── 评分质量仪表盘 ──
    all_scored = accepted + review + discarded

    # 分数段分布
    buckets = [0] * 11  # 0-9, 10-19, ..., 90-100
    for item in all_scored:
        s = item.get("confidence_score", 0)
        idx = min(s // 10, 10)
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

    # 分类分布（使用 item["category"]）
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
    }
    from . import atomic_write
    atomic_write(DATA_DIR / "scoring_stats.json", stats, indent=2)


if __name__ == "__main__":
    init_default_keywords()
    scorer = SecurityScorer()
    print(f"评分引擎就绪，当前强特征词: {len(scorer.strong_kw_list)} 个")
    print(f"中特征词: {len(scorer.medium_kw_list)} 个")
    print(f"弱特征词: {len(scorer.weak_kw_list)} 个")
