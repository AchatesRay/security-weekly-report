"""网络安全内容评分引擎

基于「词级加权 + 位置加成 + 组合校验」的累加评分机制。
支持三级特征词、歧义消解、负向过滤、领域分类。

用法:
    from pipeline.scorer import SecurityScorer
    scorer = SecurityScorer()
    result = scorer.score(item)
    # result = {
    #     "score": 85,
    #     "level": "high",
    #     "decision": "accepted",
    #     "categories": ["主分类", "副分类"],
    #     "matched": {...},
    #     "reason": "..."
    # }
"""

import json
from pathlib import Path

SCORING_CONFIG_PATH = Path("config/scoring_keywords.json")


class SecurityScorer:
    """网络安全内容评分引擎"""

    def __init__(self, config_path: Path = SCORING_CONFIG_PATH):
        self.config = self._load_config(config_path)
        self._build_indices()

    # ── 配置加载 ──

    def _load_config(self, path: Path) -> dict:
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _build_indices(self):
        c = self.config
        self.strong_kw_list = c["strong"]["keywords"]
        self.medium_kw_list = c["medium"]["keywords"]
        self.weak_kw_list = c["weak"]["keywords"]
        self.strong_weight = c["strong"]["weight"]
        self.medium_weight = c["medium"]["weight"]
        self.weak_weight = c["weak"]["weight"]
        self.requires_pair = c["medium"].get("requires_pair", True)
        self.position_mult = c["position_multipliers"]
        self.thresholds = c["thresholds"]
        self.lead_max = c.get("lead_max_chars", 200)
        self.tail_min = c.get("tail_min_total_chars", 400)
        self.neg = c.get("negative_filters", {})
        self.ambiguity = c.get("ambiguity_rules", {})
        self.categories = c.get("categories", {})

        # 构建歧义规则的快速查找：keyword -> rule
        self.ambiguity_lookup = {}
        for kw, rule in self.ambiguity.items():
            self.ambiguity_lookup[kw.lower()] = rule

    # ── 文本分段 ──

    def _segment_text(self, item: dict) -> dict[str, str]:
        """将条目文本分割为 title / lead / body / tail"""
        title = item.get("title") or ""

        # 正文：优先 summary（阶段2可能是全文），回退原摘要
        body_text = item.get("summary") or ""
        orig = item.get("original_summary") or ""
        if orig and orig != body_text and len(orig) > len(body_text):
            body_text = orig

        if not body_text.strip():
            return {"title": title, "lead": "", "body": "", "tail": ""}

        # 定位 lead（首段/导语）和 tail（尾部）
        if len(body_text) > self.tail_min:
            lead = body_text[:self.lead_max]
            tail = body_text[-self.lead_max:]
            body = body_text[self.lead_max:-self.lead_max]
        else:
            lead = body_text[:self.lead_max]
            tail = ""
            body = body_text[self.lead_max:] if len(body_text) > self.lead_max else ""

        return {"title": title, "lead": lead, "body": body, "tail": tail}

    # ── 关键词匹配 ──

    def _match_keywords(self, text: str, keywords: list[str]) -> list[str]:
        """返回文本中匹配到的关键词列表（不区分大小写）"""
        if not text:
            return []
        text_lower = text.lower()
        matched = []
        for kw in keywords:
            if not kw.strip():
                continue
            if kw.lower() in text_lower:
                matched.append(kw)
        return matched

    # ── 歧义消解 ──

    def _check_ambiguity(self, kw: str, text: str) -> bool:
        """检查关键词是否满足歧义规则，True=通过（可以计分）"""
        rule = self.ambiguity_lookup.get(kw.lower())
        if rule is None:
            return True  # 无歧义规则，直接通过

        text_lower = text.lower()
        kw_lower = kw.lower()

        # 排除模式（exclude_patterns）：命中则不计分
        for pattern in rule.get("exclude_patterns", []):
            if pattern.lower() in text_lower:
                return False

        # 前缀要求（requires_prefix）：前缀出现在文本中任意位置即通过
        # 注：不要求紧邻关键词，避免 "CVE-2024: ...漏洞" 等间隔场景被误拦
        prefixes = rule.get("requires_prefix", [])
        if prefixes:
            has_valid_prefix = any(p.lower() in text_lower for p in prefixes)
            if not has_valid_prefix:
                return False

        return True

    # ── 负向过滤 ──

    def _has_negative_filter(self, text: str, source: str = "") -> bool:
        """检查是否命中负向过滤规则"""
        text_lower = text.lower()

        # 行业排除
        for pattern in self.neg.get("industry_exclusions", []):
            if pattern.lower() in text_lower:
                return True

        # 内容类型排除
        for pattern in self.neg.get("content_type_exclusions", []):
            if pattern.lower() in text_lower:
                return True

        return False

    # ── 领域分类 ──

    def _classify(self, matched_strong: list, matched_medium: list, text: str) -> list[str]:
        """基于命中的关键词匹配安全领域分类，返回 [主分类, 副分类, ...]"""
        text_lower = text.lower()
        cat_scores = {}

        for cat_name, cat_config in self.categories.items():
            score = 0
            for kw in cat_config.get("keywords", []):
                if kw.lower() in text_lower:
                    score += 1
            # 额外加权：命中强特征词的分类优先
            for kw in matched_strong:
                if kw.lower() in text_lower:
                    # 检查该强特征词是否属于此分类
                    if kw.lower() in [k.lower() for k in cat_config.get("keywords", [])]:
                        score += 3
            if score > 0:
                cat_scores[cat_name] = score

        if not cat_scores:
            return ["未分类"]

        # 按得分排序
        ranked = sorted(cat_scores.items(), key=lambda x: -x[1])
        result = [ranked[0][0]]
        if len(ranked) > 1 and ranked[1][1] >= ranked[0][1] * 0.5:
            result.append(ranked[1][0])

        return result

    # ── 综合评分 ──

    def score(self, item: dict) -> dict:
        """对单条资讯进行完整评分。

        返回:
            score: 0-100 分
            level: "high" / "medium" / "low" / "non-security"
            decision: "accepted" / "review" / "filtered"
            categories: [主分类, 副分类]
            matched: {strong: [...], medium: [...], weak: [...]}
            reason: 判定理由简述
        """
        # 1. 文本分段
        segments = self._segment_text(item)
        all_text = " ".join(v for v in segments.values() if v)

        # 2. 按段匹配关键词（同一词取最高位置加成）
        strong_matched = {}   # keyword -> max multiplier
        medium_matched = {}
        weak_matched = {}

        for seg_name, seg_text in segments.items():
            if not seg_text:
                continue
            mult = self.position_mult.get(seg_name, 1.0)

            for kw in self.strong_kw_list:
                if kw.lower() in seg_text.lower():
                    if kw not in strong_matched or mult > strong_matched[kw]:
                        strong_matched[kw] = mult

            for kw in self.medium_kw_list:
                if kw.lower() in seg_text.lower():
                    if kw not in medium_matched or mult > medium_matched[kw]:
                        medium_matched[kw] = mult

            for kw in self.weak_kw_list:
                if kw.lower() in seg_text.lower():
                    if kw not in weak_matched or mult > weak_matched[kw]:
                        weak_matched[kw] = mult

        # 3. 歧义消解
        def filter_ambiguity(kw_dict: dict) -> dict:
            result = {}
            for kw, mult in kw_dict.items():
                if self._check_ambiguity(kw, all_text):
                    result[kw] = mult
            return result

        strong_matched = filter_ambiguity(strong_matched)
        medium_matched = filter_ambiguity(medium_matched)
        weak_matched = filter_ambiguity(weak_matched)

        # 4. 计算总分
        total = 0.0
        has_strong = len(strong_matched) > 0

        # 强特征词：直接计分
        for kw, mult in strong_matched.items():
            total += self.strong_weight * mult

        # 中特征词：若配置 requires_strong_or_medium 则需有强词搭配
        medium_requires_context = self.config["medium"].get("requires_strong_or_medium", False)
        if not medium_requires_context or has_strong:
            for kw, mult in medium_matched.items():
                total += self.medium_weight * mult

        # 弱特征词：独立计分（CVE/Exploit/0day 等无需搭配）
        for kw, mult in weak_matched.items():
            total += self.weak_weight * mult

        # 5. 负向过滤（无强特征词时生效）
        source = item.get("source") or ""
        has_negative = self._has_negative_filter(all_text, source)

        # 站点级降权
        site_demotion = self.neg.get("site_demotion", {})
        if site_demotion.get("enabled") and source in site_demotion.get("demoted_sites", []):
            total += site_demotion.get("default_penalty", -20)

        if has_negative and not has_strong:
            total = min(total, 29)  # 强制低于 30 分
            total = max(total, 10)  # 但保留最低分以示记录

        # 6. 封顶
        total = max(0, min(100, total))

        # 7. 阈值判定
        score_int = round(total)
        if score_int >= self.thresholds["direct_accept"]:
            decision = "accepted"
            level = "high"
        elif score_int >= self.thresholds["review_lower"]:
            decision = "review"
            level = "medium"
        else:
            decision = "filtered"
            if score_int < 30:
                level = "non-security"
            else:
                level = "low"

        # 8. 领域分类
        categories = self._classify(
            list(strong_matched.keys()),
            list(medium_matched.keys()),
            all_text,
        )

        # 9. 判定理由
        reason_parts = []
        if strong_matched:
            reason_parts.append(f"命中{len(strong_matched)}个强特征词")
        if medium_matched:
            reason_parts.append(f"命中{len(medium_matched)}个中特征词")
        if has_negative and not has_strong:
            reason_parts.append("负向过滤规则命中")
        reason_parts.append(f"最终得分{score_int}")
        reason = "，".join(reason_parts)

        return {
            "score": score_int,
            "level": level,
            "decision": decision,
            "categories": categories,
            "matched": {
                "strong": sorted(strong_matched.keys()),
                "medium": sorted(medium_matched.keys()),
                "weak": sorted(weak_matched.keys()),
            },
            "reason": reason,
        }

    # ── 快速预筛（阶段1用，仅用标题+前200字） ──

    def quick_score(self, item: dict) -> dict:
        """快速预评分：只用 title + lead，适用于阶段1。

        返回简化的评分结果，不包含完整分类信息。
        """
        title = item.get("title") or ""
        summary = item.get("summary") or ""
        lead_text = summary[:self.lead_max] if summary else ""

        has_strong = False
        total = 0.0

        text_blocks = {"title": title, "lead": lead_text}
        for seg_name, seg_text in text_blocks.items():
            if not seg_text:
                continue
            mult = self.position_mult.get(seg_name, 1.0)
            text_lower = seg_text.lower()

            for kw in self.strong_kw_list:
                if not kw.strip():
                    continue
                if kw.lower() in text_lower:
                    if self._check_ambiguity(kw, seg_text):
                        total += self.strong_weight * mult
                        has_strong = True

            # 中特征词：若配置 requires_strong_or_medium 则需有强词搭配
            if has_strong or not self.config["medium"].get("requires_strong_or_medium", False):
                for kw in self.medium_kw_list:
                    if not kw.strip():
                        continue
                    if kw.lower() in text_lower:
                        if self._check_ambiguity(kw, seg_text):
                            total += self.medium_weight * mult

            for kw in self.weak_kw_list:
                if not kw.strip():
                    continue
                if kw.lower() in text_lower:
                    if self._check_ambiguity(kw, seg_text):
                        total += self.weak_weight * mult

        all_text = f"{title} {summary}"
        has_negative = self._has_negative_filter(all_text, item.get("source", ""))
        if has_negative and not has_strong:
            total = min(total, 29)

        total = max(0, min(100, round(total)))
        drop = total < self.thresholds.get("stage1_drop_below", 30)

        return {
            "score": total,
            "drop": drop,
            "reason": f"快速预筛得分{total}{'，提前丢弃' if drop else ''}" if drop else "",
        }
