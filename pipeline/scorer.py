"""网络安全内容评分引擎 — 评分 + 分类统一

基于「词级加权 + 位置加成 + 组合校验」的累加评分机制。
关键词同时携带分类和内容类型元数据，评分与分类共用同一套关键词。

用法:
    from pipeline.scorer import SecurityScorer
    scorer = SecurityScorer()
    result = scorer.score(item)
    # result = {
    #     "score": 85,
    #     "level": "high",
    #     "decision": "accepted",
    #     "category": "③ 漏洞态势与供应链安全",
    #     "content_type": "漏洞披露",
    #     "region": "cn",
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

        # 权重配置
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

        # 关键词索引：text.lower() -> {text, categories: [], content_types: []}
        # 保持与旧版兼容：同时保留纯文本列表用于简单匹配
        self.strong_kw_list = []
        self.strong_index = {}
        for entry in c["strong"]["keywords"]:
            text = entry["text"]
            self.strong_kw_list.append(text)
            self.strong_index[text.lower()] = {
                "text": text,
                "categories": entry.get("categories", []),
                "content_types": entry.get("content_types", []),
            }

        self.medium_kw_list = []
        self.medium_index = {}
        for entry in c["medium"]["keywords"]:
            text = entry["text"]
            self.medium_kw_list.append(text)
            self.medium_index[text.lower()] = {
                "text": text,
                "categories": entry.get("categories", []),
                "content_types": entry.get("content_types", []),
            }

        self.weak_kw_list = []
        self.weak_index = {}
        for entry in c["weak"]["keywords"]:
            text = entry["text"]
            self.weak_kw_list.append(text)
            self.weak_index[text.lower()] = {
                "text": text,
                "categories": entry.get("categories", []),
                "content_types": entry.get("content_types", []),
            }

        # 歧义规则快速查找
        self.ambiguity_lookup = {}
        for kw, rule in self.ambiguity.items():
            self.ambiguity_lookup[kw.lower()] = rule

        # 分类元数据
        self.categories_meta = c.get("categories", {})
        # 内容类型元数据
        self.content_types_meta = c.get("content_types", {})

        # 地域推断关键词
        self.region_map = {
            "cn": [
                "中国", "国家网信办", "工信部", "cncert", "中国信通院",
                "全国信安标委", "公安三所", "等保", "网信办",
            ],
            "us": [
                "美国", "CISA", "FBI", "NSA", "白宫", "Biden", "Trump",
                "美国政府", "US government",
            ],
            "eu": [
                "欧盟", "ENISA", "GDPR", "欧洲", "EU", "European Union",
            ],
        }

    # ── 文本分段 ──

    def _segment_text(self, item: dict) -> dict[str, str]:
        """将条目文本分割为 title / lead / body / tail"""
        title = item.get("title") or ""

        body_text = item.get("summary") or ""
        orig = item.get("original_summary") or ""
        if orig and orig != body_text and len(orig) > len(body_text):
            body_text = orig

        if not body_text.strip():
            return {"title": title, "lead": "", "body": "", "tail": ""}

        if len(body_text) > self.tail_min:
            lead = body_text[:self.lead_max]
            tail = body_text[-self.lead_max:]
            body = body_text[self.lead_max:-self.lead_max]
        else:
            lead = body_text[:self.lead_max]
            tail = ""
            body = body_text[self.lead_max:] if len(body_text) > self.lead_max else ""

        return {"title": title, "lead": lead, "body": body, "tail": tail}

    # ── 歧义消解 ──

    def _check_ambiguity(self, kw: str, text: str) -> bool:
        """检查关键词是否满足歧义规则，True=通过（可以计分）"""
        rule = self.ambiguity_lookup.get(kw.lower())
        if rule is None:
            return True

        text_lower = text.lower()
        kw_lower = kw.lower()

        for pattern in rule.get("exclude_patterns", []):
            if pattern.lower() in text_lower:
                return False

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

        for pattern in self.neg.get("industry_exclusions", []):
            if pattern.lower() in text_lower:
                return True

        for pattern in self.neg.get("content_type_exclusions", []):
            if pattern.lower() in text_lower:
                return True

        return False

    # ── 领域分类（从匹配关键词聚合） ──

    def _classify(self, matched_all: dict) -> str:
        """基于命中关键词的 categories 聚合出最佳分类"""
        cat_scores = {}
        for kw_text, info in matched_all.items():
            for cat in info.get("categories", []):
                # 强词加权 2×，中/弱词 1×
                weight = 2 if kw_text.lower() in self.strong_index else 1
                cat_scores[cat] = cat_scores.get(cat, 0) + weight

        if not cat_scores:
            return "未分类"

        # 按得分排序取最高分
        ranked = sorted(cat_scores.items(), key=lambda x: -x[1])
        return ranked[0][0]

    # ── 内容类型推断 ──

    def _infer_content_type(self, matched_all: dict, text: str) -> str:
        """从匹配关键词的 content_types 和全文扫描推断内容类型"""
        # 方法1：从关键词 content_type 聚合
        ct_scores = {}
        for kw_text, info in matched_all.items():
            for ct in info.get("content_types", []):
                ct_scores[ct] = ct_scores.get(ct, 0) + 1

        # 方法2：全文模式匹配（覆盖更广）
        text_lower = text.lower()
        broad_map = {
            "研究报告/白皮书": ["白皮书", "研究报告", "whitepaper", "white paper",
                               "研究", "research paper", "技术报告"],
            "漏洞披露": ["cve-", "漏洞披露", "vulnerability disclosure", "0-day",
                         "advisory", "安全公告", "漏洞预警"],
            "攻击活动报告": ["apt", "攻击活动", "threat actor", "threat group",
                            "入侵", "intrusion", "campaign", "攻击链"],
            "工具发布": ["工具", "tool", "发布", "release", "开源项目"],
            "行业分析": ["市场", "market", "报告", "analysis", "趋势",
                         "gartner", "forrester", "行业"],
            "法规/标准发布": ["法规", "regulation", "标准", "standard", "法律",
                             "法案", "合规", "compliance", "nist", "iso"],
        }
        broad_scores = {}
        for ct, patterns in broad_map.items():
            score = sum(1 for p in patterns if p.lower() in text_lower)
            if score > 0:
                broad_scores[ct] = score

        # 合并两种方法，关键词匹配权重更高
        for ct, score in broad_scores.items():
            ct_scores[ct] = ct_scores.get(ct, 0) + score

        return max(ct_scores, key=ct_scores.get) if ct_scores else "综合"

    # ── 地域推断 ──

    def _infer_region(self, text: str) -> str:
        """从全文扫描推断地域"""
        text_lower = text.lower()
        for region, kws in self.region_map.items():
            if any(kw.lower() in text_lower for kw in kws):
                return region
        return ""

    # ── 综合评分 ──

    def score(self, item: dict) -> dict:
        """对单条资讯进行完整评分。

        返回:
            score: 0-100 分
            level: "high" / "medium" / "low" / "non-security"
            decision: "accepted" / "review" / "filtered"
            category: 安全领域分类字符串
            content_type: 内容类型
            region: 地域
            matched: {strong: [...], medium: [...], weak: [...]}
            reason: 判定理由简述
        """
        # 1. 文本分段
        segments = self._segment_text(item)
        all_text = " ".join(v for v in segments.values() if v)

        # 2. 按段匹配关键词（同一词取最高位置加成）
        # matched: {kw_text -> {"mult": max_mult, "categories": [], "content_types": []}}
        strong_matched = {}
        medium_matched = {}
        weak_matched = {}

        for seg_name, seg_text in segments.items():
            if not seg_text:
                continue
            mult = self.position_mult.get(seg_name, 1.0)
            seg_lower = seg_text.lower()

            for kw_text in self.strong_kw_list:
                if not kw_text.strip():
                    continue
                if kw_text.lower() in seg_lower:
                    if kw_text not in strong_matched or mult > strong_matched[kw_text]["mult"]:
                        idx = self.strong_index.get(kw_text.lower(), {})
                        strong_matched[kw_text] = {
                            "mult": mult,
                            "categories": idx.get("categories", []),
                            "content_types": idx.get("content_types", []),
                        }

            for kw_text in self.medium_kw_list:
                if not kw_text.strip():
                    continue
                if kw_text.lower() in seg_lower:
                    if kw_text not in medium_matched or mult > medium_matched[kw_text]["mult"]:
                        idx = self.medium_index.get(kw_text.lower(), {})
                        medium_matched[kw_text] = {
                            "mult": mult,
                            "categories": idx.get("categories", []),
                            "content_types": idx.get("content_types", []),
                        }

            for kw_text in self.weak_kw_list:
                if not kw_text.strip():
                    continue
                if kw_text.lower() in seg_lower:
                    if kw_text not in weak_matched or mult > weak_matched[kw_text]["mult"]:
                        idx = self.weak_index.get(kw_text.lower(), {})
                        weak_matched[kw_text] = {
                            "mult": mult,
                            "categories": idx.get("categories", []),
                            "content_types": idx.get("content_types", []),
                        }

        # 3. 歧义消解
        def filter_ambiguity(kw_dict: dict) -> dict:
            result = {}
            for kw_text, info in kw_dict.items():
                if self._check_ambiguity(kw_text, all_text):
                    result[kw_text] = info
            return result

        strong_matched = filter_ambiguity(strong_matched)
        medium_matched = filter_ambiguity(medium_matched)
        weak_matched = filter_ambiguity(weak_matched)

        # 4. 计算总分
        total = 0.0
        has_strong = len(strong_matched) > 0

        for kw_text, info in strong_matched.items():
            total += self.strong_weight * info["mult"]

        medium_requires_context = self.config["medium"].get("requires_strong_or_medium", False)
        if not medium_requires_context or has_strong:
            for kw_text, info in medium_matched.items():
                total += self.medium_weight * info["mult"]

        for kw_text, info in weak_matched.items():
            total += self.weak_weight * info["mult"]

        # 5. 负向过滤
        source = item.get("source") or ""
        has_negative = self._has_negative_filter(all_text, source)

        site_demotion = self.neg.get("site_demotion", {})
        if site_demotion.get("enabled") and source in site_demotion.get("demoted_sites", []):
            total += site_demotion.get("default_penalty", -20)

        if has_negative and not has_strong:
            total = min(total, 29)
            total = max(total, 10)

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

        # 8. 分类与元数据（合并所有匹配关键词）
        all_matched = {}
        all_matched.update(strong_matched)
        all_matched.update(medium_matched)
        all_matched.update(weak_matched)

        category = self._classify(all_matched)
        content_type = self._infer_content_type(all_matched, all_text)
        region = self._infer_region(all_text)

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
            "category": category,
            "content_type": content_type,
            "region": region,
            "matched": {
                "strong": sorted(strong_matched.keys()),
                "medium": sorted(medium_matched.keys()),
                "weak": sorted(weak_matched.keys()),
            },
            "reason": reason,
        }

    # ── 快速预筛 ──

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

            for kw_text in self.strong_kw_list:
                if not kw_text.strip():
                    continue
                if kw_text.lower() in text_lower:
                    if self._check_ambiguity(kw_text, seg_text):
                        total += self.strong_weight * mult
                        has_strong = True

            if has_strong or not self.config["medium"].get("requires_strong_or_medium", False):
                for kw_text in self.medium_kw_list:
                    if not kw_text.strip():
                        continue
                    if kw_text.lower() in text_lower:
                        if self._check_ambiguity(kw_text, seg_text):
                            total += self.medium_weight * mult

            for kw_text in self.weak_kw_list:
                if not kw_text.strip():
                    continue
                if kw_text.lower() in text_lower:
                    if self._check_ambiguity(kw_text, seg_text):
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
