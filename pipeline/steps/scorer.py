"""网络安全内容评分引擎 — 评分 + 分类统一

基于「词级加权 + 位置加成 + 分类梯度」的累加评分机制。
关键词同时携带分类和内容类型元数据，评分与分类共用同一套关键词。

2026-09-29 修复：
  1. **同一关键词被重复计分**：关键词若标注了多个分类，原实现在每个分类下
     各累加一次（`total += cat_total`），导致同一个词贡献 2 倍分数，把分数
     推向 100 分上限。现在每个关键词只计一次，取其所属分类中最高的梯度系数；
     分类明细仍按分类归因（仅供展示，可能重叠）。
  2. **阶段1 与阶段2 口径不一致**：阶段1（quick_score）此前用“无梯度、按段
     重复累加”的独立算法，同一条目阶段1 得 22 分而阶段2 得 0 分，使
     “<30 分提前丢弃”这个阈值与阶段2 的分值毫无可比性。现在两阶段共用同一套
     计算与阈值口径，阶段1 只是把可匹配文本缩小为“标题+前200字”。
  3. **并列判定不确定**：分类与内容类型在分数并列时按字典插入顺序取第一个，
     结果对词表顺序敏感。现在统一按 (分数降序, 名称升序) 取确定值。
  4. **死代码/失效配置**：site_demotion 读的是 `item["source"]`（数据结构里
     不存在此键，实际为 `source_name`），该功能从未生效；`_has_negative_filter`
     的 source 参数在函数体内从未使用。现已修正为读取 source_name。

用法:
    from pipeline.steps.scorer import SecurityScorer
    scorer = SecurityScorer()
    result = scorer.score(item)
"""

import json
import re
from pathlib import Path

from ..utils import SCORING_CONFIG_PATH

DEFAULT_ACCEPT_THRESHOLD = 80
DEFAULT_REVIEW_THRESHOLD = 50


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
        self.position_mult = c["position_multipliers"]
        self.thresholds = c["thresholds"]
        self.lead_max = c.get("lead_max_chars", 200)
        self.tail_min = c.get("tail_min_total_chars", 400)
        self.neg = c.get("negative_filters", {})
        self.ambiguity = c.get("ambiguity_rules", {})

        # 关键词索引：text.lower() -> {text, categories: [], content_types: []}
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
        self.medium_type_index = {}
        for entry in c["medium"]["keywords"]:
            text = entry["text"]
            kw_type = entry.get("type", "normal")
            self.medium_kw_list.append(text)
            self.medium_index[text.lower()] = {
                "text": text,
                "type": kw_type,
                "categories": entry.get("categories", []),
                "content_types": entry.get("content_types", []),
            }
            self.medium_type_index[text.lower()] = kw_type

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

        # 预编译词边界 regex：纯 ASCII 关键词用词边界匹配防误报
        self._ascii_patterns = {}
        for kw_text in self.strong_kw_list + self.medium_kw_list + self.weak_kw_list:
            if kw_text and self._is_ascii_only(kw_text):
                self._ascii_patterns[kw_text.lower()] = re.compile(
                    r'\b' + re.escape(kw_text) + r'\b', re.IGNORECASE
                )

    # ── 文本分段 ──

    @staticmethod
    def _is_ascii_only(text: str) -> bool:
        """判断关键词是否纯ASCII（纯ASCII关键词需用词边界匹配防误报）"""
        return bool(text) and all(ord(c) < 128 for c in text)

    def _segment_text(self, item: dict) -> dict[str, str]:
        """将条目文本分割为 title / lead / body / tail"""
        title = item.get("title") or ""

        body_text = item.get("summary") or ""
        # full_body（content:encoded）> original_summary（fulltext_extractor 备份）> summary
        for field in ("full_body", "original_summary"):
            val = item.get(field) or ""
            if len(val) > len(body_text):
                body_text = val

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

        for pattern in rule.get("exclude_patterns", []):
            if pattern.lower() in text_lower:
                return False

        prefixes = rule.get("requires_prefix", [])
        if prefixes:
            if not any(p.lower() in text_lower for p in prefixes):
                return False

        return True

    def _should_score(self, kw_text: str) -> bool:
        """检查关键词是否实际贡献分数（standalone_score=false 不计分，仅用于分类）"""
        rule = self.ambiguity_lookup.get(kw_text.lower())
        return not (rule and rule.get("standalone_score") is False)

    # ── 词边界感知匹配 ──

    def _kw_matches(self, kw_text: str, text: str) -> bool:
        """检查关键词是否匹配文本——纯ASCII用词边界regex，含CJK用子串匹配"""
        if not kw_text.strip() or not text:
            return False
        pattern = self._ascii_patterns.get(kw_text.lower())
        if pattern:
            return bool(pattern.search(text))
        return kw_text.lower() in text.lower()

    # ── 负向过滤 ──

    def _has_negative_filter(self, text: str) -> bool:
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

        # 并列时按分类名升序，保证结果不随词表顺序变化
        ranked = sorted(cat_scores.items(), key=lambda x: (-x[1], x[0]))
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
        for ct, patterns in broad_map.items():
            score = 0
            for p in patterns:
                if self._is_ascii_only(p):
                    if re.search(r'\b' + re.escape(p) + r'\b', text, re.IGNORECASE):
                        score += 1
                elif p.lower() in text_lower:
                    score += 1
            if score > 0:
                ct_scores[ct] = ct_scores.get(ct, 0) + score

        if not ct_scores:
            return "综合"
        # 并列时按类型名升序，保证可复现
        return sorted(ct_scores.items(), key=lambda x: (-x[1], x[0]))[0][0]

    # ── 三级梯度计分（pairing_rules） ──

    # 各层级的默认系数（与配置缺省时保持一致）
    _TIER_DEFAULTS = {
        "tier_a": (1.0, 1.0),
        "tier_b": (0.6, 0.3),
        "tier_c": (0.5, 0.2),
        "tier_d": (0.0, 0.0),
    }

    def _tier_ratios(self, group: dict) -> tuple[str, float, float]:
        """按分类内部证据判定层级，返回 (层级名, 中词系数, 弱词系数)"""
        pairing = self.config.get("pairing_rules", {})
        has_strong = any(self._should_score(k["text"]) for k in group["strong"])
        core_medium_count = sum(
            1 for m in group["medium"]
            if m["type"] == "core" and self._should_score(m["text"]))
        normal_medium_count = sum(
            1 for m in group["medium"]
            if m["type"] == "normal" and self._should_score(m["text"]))

        if has_strong:
            tier = "tier_a"
        elif core_medium_count >= 1:
            tier = "tier_b"
        elif normal_medium_count >= 2:
            tier = "tier_c"
        else:
            tier = "tier_d"
        default_med, default_weak = self._TIER_DEFAULTS[tier]
        node = pairing.get(tier, {})
        return (tier,
                float(node.get("medium_score_ratio", default_med)),
                float(node.get("weak_score_ratio", default_weak)))

    def _score_with_pairing_rules(
        self,
        strong_matched: dict,
        medium_matched: dict,
        weak_matched: dict,
    ) -> tuple[float, dict]:
        """基于 pairing_rules 四级梯度计分，按分类独立判定层级。

        关键点：**每个关键词只在总分中计一次**。关键词若标注了多个分类，
        取其所属分类中最高的梯度系数；分类明细按分类归因，因此明细之和
        可能大于总分（仅作展示）。

        返回:
            (total_score, per_category_detail)
        """
        # -- 第1步：按分类聚合，同时记录关键词 -> 分类列表 --
        cat_groups: dict[str, dict] = {}
        global_medium: list[dict] = []
        global_weak: list[dict] = []

        def _add(cat, bucket, entry):
            cat_groups.setdefault(cat, {"strong": [], "medium": [], "weak": []})[bucket].append(entry)

        for kw_text, info in strong_matched.items():
            entry = {"text": kw_text, "mult": info["mult"]}
            for cat in info.get("categories", []):
                _add(cat, "strong", entry)

        for kw_text, info in medium_matched.items():
            kw_type = self.medium_type_index.get(kw_text.lower(), "normal")
            entry = {"text": kw_text, "mult": info["mult"], "type": kw_type}
            cats = info.get("categories", [])
            if cats:
                for cat in cats:
                    _add(cat, "medium", entry)
            else:
                global_medium.append(entry)

        for kw_text, info in weak_matched.items():
            entry = {"text": kw_text, "mult": info["mult"]}
            cats = info.get("categories", [])
            if cats:
                for cat in cats:
                    _add(cat, "weak", entry)
            else:
                global_weak.append(entry)

        # -- 第2步：各分类独立判定层级 --
        cat_tiers: dict[str, tuple[str, float, float]] = {}
        for cat, group in cat_groups.items():
            cat_tiers[cat] = self._tier_ratios(group)

        # -- 第3步：每个关键词只计一次，取所属分类中最高的梯度系数 --
        best_ratio: dict[tuple[str, str], float] = {}

        def _note(kind, kw_text, ratio):
            key = (kind, kw_text)
            if ratio > best_ratio.get(key, -1):
                best_ratio[key] = ratio

        for cat, group in cat_groups.items():
            _tier, med_ratio, weak_ratio = cat_tiers[cat]
            for k in group["strong"]:
                _note("strong", k["text"], k["mult"])
            for m in group["medium"]:
                _note("medium", m["text"], med_ratio * m["mult"])
            for w in group["weak"]:
                _note("weak", w["text"], weak_ratio * w["mult"])

        total = 0.0
        # 强词
        for (kind, kw_text), ratio in best_ratio.items():
            if not self._should_score(kw_text):
                continue
            if kind == "strong":
                total += self.strong_weight * ratio
            elif kind == "medium":
                total += self.medium_weight * ratio
            else:
                total += self.weak_weight * ratio

        # -- 第4步：无分类归属的中/弱词，按全额计分（每个词只计一次） --
        for m in global_medium:
            if self._should_score(m["text"]):
                total += self.medium_weight * m["mult"]
        for w in global_weak:
            if self._should_score(w["text"]):
                total += self.weak_weight * w["mult"]

        # -- 第5步：分类归因明细（仅供展示，重叠时之和大于总分） --
        cat_details: dict[str, dict] = {}
        for cat, group in cat_groups.items():
            tier, med_ratio, weak_ratio = cat_tiers[cat]
            cat_total = 0.0
            for kw in group["strong"]:
                if self._should_score(kw["text"]):
                    cat_total += self.strong_weight * kw["mult"]
            for m in group["medium"]:
                if self._should_score(m["text"]):
                    cat_total += self.medium_weight * m["mult"] * med_ratio
            for w in group["weak"]:
                if self._should_score(w["text"]):
                    cat_total += self.weak_weight * w["mult"] * weak_ratio
            cat_details[cat] = {"tier": tier, "score": round(cat_total, 1)}

        return total, cat_details

    # ── 关键词匹配 ──

    def _collect_matches(self, segments: dict[str, str]) -> tuple[dict, dict, dict]:
        """按段匹配关键词，同一关键词取最高位置加成"""
        strong_matched: dict = {}
        medium_matched: dict = {}
        weak_matched: dict = {}

        buckets = (
            (self.strong_kw_list, self.strong_index, strong_matched),
            (self.medium_kw_list, self.medium_index, medium_matched),
            (self.weak_kw_list, self.weak_index, weak_matched),
        )

        for seg_name, seg_text in segments.items():
            if not seg_text:
                continue
            mult = self.position_mult.get(seg_name, 1.0)
            for kw_list, index, matched in buckets:
                for kw_text in kw_list:
                    if not kw_text.strip():
                        continue
                    if not self._kw_matches(kw_text, seg_text):
                        continue
                    prev = matched.get(kw_text)
                    if prev is None or mult > prev["mult"]:
                        info = index.get(kw_text.lower(), {})
                        matched[kw_text] = {
                            "mult": mult,
                            "categories": info.get("categories", []),
                            "content_types": info.get("content_types", []),
                        }
        return strong_matched, medium_matched, weak_matched

    def _filter_ambiguity(self, kw_dict: dict, all_text: str) -> dict:
        return {kw: info for kw, info in kw_dict.items()
                if self._check_ambiguity(kw, all_text)}

    def _evaluate(self, item: dict, strong_matched: dict, medium_matched: dict,
                  weak_matched: dict, all_text: str):
        """统一的总分/决策计算，阶段1 与阶段2 共用，保证两阶段口径一致"""
        if "pairing_rules" in self.config:
            total, cat_details = self._score_with_pairing_rules(
                strong_matched, medium_matched, weak_matched)
        else:
            # 旧版兼容：简单累加（保留 requires_strong_or_medium 开关语义）
            medium_needs_context = self.config["medium"].get(
                "requires_strong_or_medium", False)
            has_strong_kw = any(self._should_score(k) for k in strong_matched)
            total = 0.0
            for kw_text, info in strong_matched.items():
                if self._should_score(kw_text):
                    total += self.strong_weight * info["mult"]
            if not medium_needs_context or has_strong_kw:
                for kw_text, info in medium_matched.items():
                    if self._should_score(kw_text):
                        total += self.medium_weight * info["mult"]
            for kw_text, info in weak_matched.items():
                if self._should_score(kw_text):
                    total += self.weak_weight * info["mult"]
            cat_details = {}

        # 负向过滤
        has_strong = any(self._should_score(k) for k in strong_matched)
        has_negative = self._has_negative_filter(all_text)

        site_demotion = self.neg.get("site_demotion", {})
        source_name = item.get("source_name") or ""
        if site_demotion.get("enabled") and source_name in site_demotion.get("demoted_sites", []):
            total += site_demotion.get("default_penalty", -20)

        if has_negative and not has_strong:
            total = min(total, self.neg.get("negative_score_cap", 29))
            total = max(total, 10)

        total = max(0, min(100, total))
        score_int = round(total)

        accept_threshold = self.thresholds.get(
            "accept_threshold", self.thresholds.get("direct_accept", DEFAULT_ACCEPT_THRESHOLD))
        review_threshold = self.thresholds.get("review_threshold", DEFAULT_REVIEW_THRESHOLD)

        if score_int >= accept_threshold:
            decision, level = "accepted", "high"
        elif score_int >= review_threshold:
            decision, level = "review", "medium"
        else:
            decision, level = "filtered", "non-security"

        return score_int, level, decision, cat_details, has_negative, has_strong

    # ── 综合评分 ──

    def score(self, item: dict) -> dict:
        """对单条资讯进行完整评分（使用全文可匹配文本）。

        返回:
            score: 0-100 分
            level: "high" / "medium" / "low" / "non-security"
            decision: "accepted" / "review" / "filtered"
            category: 安全领域分类字符串
            content_type: 内容类型
            matched: {strong: [...], medium: [...], weak: [...]}
            reason: 判定理由简述
        """
        segments = self._segment_text(item)
        all_text = " ".join(v for v in segments.values() if v)

        strong_matched, medium_matched, weak_matched = self._collect_matches(segments)
        strong_matched = self._filter_ambiguity(strong_matched, all_text)
        medium_matched = self._filter_ambiguity(medium_matched, all_text)
        weak_matched = self._filter_ambiguity(weak_matched, all_text)

        score_int, level, decision, cat_details, has_negative, _has_strong = self._evaluate(
            item, strong_matched, medium_matched, weak_matched, all_text)

        # 分类与元数据（合并所有匹配关键词）
        all_matched = {}
        all_matched.update(strong_matched)
        all_matched.update(medium_matched)
        all_matched.update(weak_matched)

        category = self._classify(all_matched)
        content_type = self._infer_content_type(all_matched, all_text)

        # 判定理由
        reason_parts = []
        if strong_matched:
            reason_parts.append(f"命中{len(strong_matched)}个强特征词")
        if medium_matched:
            reason_parts.append(f"命中{len(medium_matched)}个中特征词")
        if "pairing_rules" in self.config and cat_details:
            tiers_used = sorted({d["tier"] for d in cat_details.values()})
            reason_parts.append(f"梯度: {', '.join(tiers_used)}")
        if has_negative:
            reason_parts.append("负向过滤规则命中")
        reason_parts.append(f"最终得分{score_int}")

        return {
            "score": score_int,
            "level": level,
            "decision": decision,
            "category": category,
            "content_type": content_type,
            "matched": {
                "strong": sorted(strong_matched.keys()),
                "medium": sorted(medium_matched.keys()),
                "weak": sorted(weak_matched.keys()),
            },
            "cat_details": cat_details,
            "reason": "，".join(reason_parts),
        }

    # ── 阶段1 快速预筛 ──

    def quick_score(self, item: dict) -> dict:
        """阶段1 快速预评分：只匹配 title + lead，**其余口径与阶段2 完全一致**。

        与 score() 共用 _collect_matches / _evaluate，因此两阶段的分值可直接
        比较，"stage1_drop_below" 阈值才真正有意义。此前阶段1 用另一套算法
        （无梯度、跨段重复累加），同一篇内容可能出现阶段1 得 22 分、阶段2 得
        0 分的情况。
        """
        title = item.get("title") or ""
        summary = item.get("summary") or ""
        lead_text = summary[:self.lead_max] if summary else ""
        segments = {"title": title, "lead": lead_text}

        strong_matched, medium_matched, weak_matched = self._collect_matches(segments)
        all_text = f"{title} {lead_text}"
        strong_matched = self._filter_ambiguity(strong_matched, all_text)
        medium_matched = self._filter_ambiguity(medium_matched, all_text)
        weak_matched = self._filter_ambiguity(weak_matched, all_text)

        score_int, _level, _decision, _cat_details, has_negative, _hs = self._evaluate(
            item, strong_matched, medium_matched, weak_matched, all_text)

        drop_threshold = self.thresholds.get("stage1_drop_below", 30)
        drop = score_int < drop_threshold

        reason = ""
        if drop:
            reason = f"快速预筛得分{score_int}（<{drop_threshold}），提前丢弃"
        elif has_negative:
            reason = f"快速预筛得分{score_int}，命中负向规则但保留待完整评估"

        return {
            "score": score_int,
            "drop": drop,
            "reason": reason,
            "matched": {
                "strong": sorted(strong_matched.keys()),
                "medium": sorted(medium_matched.keys()),
                "weak": sorted(weak_matched.keys()),
            },
        }
