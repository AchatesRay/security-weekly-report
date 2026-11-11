"""选文标注存储：把"人觉得该不该收"沉淀成可复盘的证据。

设计约束（详见 docs/annotations.md）：

- **只追加、不改写**。每次点击都是一条独立事件；撤销也写成新记录
  （``verdict=clear``），既能回看自己判断的变化，也不会因并发写丢数据。
- **随行快照**。报告数据 14 天清理、滚动库 14 天滑出，只存 item_id 事后
  无法复盘，因此把标题/信源/当时的评分拆解一起存下来。
- **放 data/state/annotations/**。data/ 根目录会在每次开跑被清空。
- **不参与管道**。标注只是调规则的依据，任何时候都不影响当天报告。
"""

from __future__ import annotations

import json
import re
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

# 原因白名单：每个标签都必须能对应到打分/配置里的一个可调项，
# 否则标注只会变成"情绪记录"，没法指导改规则。
REASONS: list[dict] = [
    {
        "code": "commercial",
        "label": "商业/厂商宣传",
        "hint": "产品发布、融资、榜单、案例包装",
        "rule": "commercial_penalty / 发布稿封顶 / 商业负向词",
    },
    {
        "code": "off_topic",
        "label": "与安全无关",
        "hint": "只是沾了安全词，实际是其它领域",
        "rule": "负向过滤 / 强词权重",
    },
    {
        "code": "ai_hollow",
        "label": "AI 空泛",
        "hint": "谈 AI 不谈安全，无攻击面或防护内容",
        "rule": "伞形词只分类不计分 / ① 类权重",
    },
    {
        "code": "shallow",
        "label": "太浅/无新增",
        "hint": "通稿式复述，没有技术细节或新信息",
        "rule": "depth_bonus / 深度门槛",
    },
    {
        "code": "duplicate",
        "label": "重复/转载",
        "hint": "同文多篇，或与已收条目实质相同",
        "rule": "dedup 阈值 / 正文指纹",
    },
    {
        "code": "stale",
        "label": "过时/旧闻",
        "hint": "非近期事件，或已被后续进展覆盖",
        "rule": "max_days / 滚动窗口",
    },
    {
        "code": "source_noise",
        "label": "信源噪音",
        "hint": "该源整体不值得收，不是单篇问题",
        "rule": "source_tiers / 单源配额",
    },
    {
        "code": "cve_noise",
        "label": "漏洞详情，不关注",
        "hint": "逐条 CVE 通报，只有编号与影响版本",
        "rule": "③ 类权重 / 内容类型系数",
    },
]
REASON_CODES = {r["code"] for r in REASONS}
VERDICTS = ("accept", "reject", "clear")
BASELINES = ("accepted", "review")

SNAPSHOT_TEXT_LIMITS = {
    "title": 300,
    "url": 1000,
    "source_name": 80,
    "category": 60,
    "published_date": 24,
    "first_seen": 24,
    "content_type": 40,
    "edition": 24,
    "ai_summary": 400,
}
_MAX_TERMS = 40
_MAX_TERM_LEN = 40

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean(value, limit: int) -> str:
    if value is None:
        return ""
    text = _CTRL_RE.sub("", str(value)).strip()
    return text[:limit]


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class AnnotationStore:
    """按月分文件的追加式标注库。

    目录结构::

        data/state/annotations/annotations-2026-11.jsonl
    """

    def __init__(self, store_dir: Path):
        self.dir = Path(store_dir)
        self._lock = threading.Lock()

    # ── 写入 ──

    def append(self, payload: dict, annotator: str = "me") -> dict:
        """校验并追加一条标注，返回落盘记录。"""
        if not isinstance(payload, dict):
            raise ValueError("标注内容必须是对象")

        item_id = _clean(payload.get("item_id"), 64)
        if not item_id:
            raise ValueError("缺少 item_id")

        verdict = _clean(payload.get("verdict"), 16)
        if verdict not in VERDICTS:
            raise ValueError(f"verdict 必须是 {'/'.join(VERDICTS)}")

        reason = _clean(payload.get("reason"), 32)
        if reason and reason not in REASON_CODES:
            raise ValueError("未知的标注原因")
        if verdict == "reject" and not reason:
            reason = "unspecified"

        baseline = _clean(payload.get("baseline"), 16)
        if baseline and baseline not in BASELINES:
            raise ValueError("baseline 必须是 accepted / review")

        now = datetime.now(timezone.utc).astimezone()
        record = {
            "v": 1,
            "id": uuid.uuid4().hex[:12],
            "ts": now.isoformat(timespec="seconds"),
            "annotator": _clean(annotator, 40) or "me",
            "item_id": item_id,
            "verdict": verdict,
            "reason": reason,
            "baseline": baseline,
            "snapshot": self._snapshot(payload.get("snapshot")),
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        path = self._path(now.strftime("%Y-%m"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            # 单行单次 write，追加模式下不会与其它进程交错写坏半行
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
        return record

    @staticmethod
    def _snapshot(raw) -> dict:
        if not isinstance(raw, dict):
            return {}
        snap: dict = {}
        for key, limit in SNAPSHOT_TEXT_LIMITS.items():
            value = _clean(raw.get(key), limit)
            if value:
                snap[key] = value
        for key in ("confidence_score", "raw_score", "relevance_score"):
            num = _num(raw.get(key))
            if num is not None:
                snap[key] = round(num, 2)
        sb = raw.get("score_breakdown")
        if isinstance(sb, dict):
            snap["score_breakdown"] = {
                str(k)[:40]: (round(v, 2) if isinstance(v, (int, float)) else _clean(v, 40))
                for k, v in list(sb.items())[:24]
                if isinstance(v, (int, float, bool, str))
            }
        matched = raw.get("matched_terms")
        if isinstance(matched, dict):
            snap["matched_terms"] = {
                tier: [_clean(t, _MAX_TERM_LEN) for t in terms[:_MAX_TERMS]
                       if _clean(t, _MAX_TERM_LEN)]
                for tier, terms in matched.items()
                if isinstance(terms, list) and tier in ("strong", "medium", "weak")
            }
        return snap

    def _path(self, month: str | None = None) -> Path:
        month = month or datetime.now().strftime("%Y-%m")
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            raise ValueError("月份格式应为 YYYY-MM")
        return self.dir / f"annotations-{month}.jsonl"

    # ── 读取 ──

    def _months(self) -> list[str]:
        if not self.dir.exists():
            return []
        months = []
        for p in self.dir.glob("annotations-*.jsonl"):
            m = re.fullmatch(r"annotations-(\d{4}-\d{2})\.jsonl", p.name)
            if m:
                months.append(m.group(1))
        return sorted(months)

    def _iter_records(self, month: str | None = None):
        months = [month] if month else self._months()
        for m in months:
            path = self._path(m)
            if not path.exists():
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except ValueError:
                            continue  # 半行/损坏行跳过，不影响其余标注
                        if isinstance(rec, dict) and rec.get("item_id"):
                            yield rec
            except OSError:
                continue

    def latest(self, month: str | None = None) -> list[dict]:
        """按 (标注人, 条目) 折叠，返回当前生效的标注（不含撤销）。"""
        folded: dict[tuple, dict] = {}
        for rec in self._iter_records(month):
            key = (rec.get("annotator") or "", rec.get("item_id") or "")
            folded[key] = rec
        live = [r for r in folded.values() if r.get("verdict") in ("accept", "reject")]
        live.sort(key=lambda r: r.get("ts") or "", reverse=True)
        return live

    def today_counts(self) -> dict:
        """当天（本机时区）的最新标注计数，用于"今日已标 N 条"。"""
        today = datetime.now().strftime("%Y-%m-%d")
        accepted = rejected = 0
        for rec in self.latest():
            if str(rec.get("ts") or "")[:10] != today:
                continue
            if rec.get("verdict") == "accept":
                accepted += 1
            elif rec.get("verdict") == "reject":
                rejected += 1
        return {"date": today, "accept": accepted, "reject": rejected,
                "total": accepted + rejected}

    def export_jsonl(self, month: str | None = None) -> str:
        """原样导出（含撤销记录），供本机做规则调优分析。"""
        months = [month] if month else self._months()
        chunks: list[str] = []
        for m in months:
            path = self._path(m)
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if text and not text.endswith("\n"):
                text += "\n"
            chunks.append(text)
        return "".join(chunks)

    @staticmethod
    def reasons() -> list[dict]:
        return [dict(r) for r in REASONS]


def default_store_dir() -> Path:
    from .utils import STATE_DIR
    return STATE_DIR / "annotations"


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="选文标注导出")
    parser.add_argument("--dir", default=str(default_store_dir()))
    parser.add_argument("--month", default="", help="YYYY-MM，缺省导出全部")
    parser.add_argument("--out", default="", help="输出文件，缺省打印到标准输出")
    args = parser.parse_args(argv)

    store = AnnotationStore(Path(args.dir))
    text = store.export_jsonl(args.month or None)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已导出 {text.count(chr(10))} 条 → {args.out}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))
