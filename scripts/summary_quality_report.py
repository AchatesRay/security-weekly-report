# -*- coding: utf-8 -*-
"""摘要质量体检 / 回归对比工具（只读，不改任何数据）

用法：
    python scripts/summary_quality_report.py                     # 体检 data/translated_items.json
    python scripts/summary_quality_report.py A.json              # 体检指定产物
    python scripts/summary_quality_report.py A.json B.json       # 对比改造前后
    python scripts/summary_quality_report.py A.json B.json --show 15

指标口径（与 pipeline/steps/llm_processor.py 的清洗规则保持一致）：
    截断率   摘要以省略号收尾 —— 句子被拦腰截断
    噪声率   摘要含导航/署名/分享/日期/裸网址等网页噪声
    粘连处   英文句末紧接大写字母（"...accessed.The"），应为 0
    重复率   报告「正文」栏开头就是摘要内容 —— 同一段话显示两遍
              （该指标请对 reports/data_<week>.json 跑，管道中间产物里不做剥离）
    长度     字数中位数/均值（命中上限比例过高说明长度由上限而非内容决定）

用于项目 §7 的强制回归流程：改动摘要逻辑后，用最近一次真实运行的产物
对比改动前后，并**逐条看被改变的摘要**，不要只看总数。
"""

import argparse
import json
import re
import statistics as st
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DEFAULT_DATA = PROJECT_ROOT / "data" / "translated_items.json"

# 优先复用摘要模块里的噪声规则，避免两处规则分叉；导入失败时用本地副本
_FALLBACK_NOISE = (
    r"^\s*(share|tweet|print|email|featured|home|about|contact|topics|resources|events|webinars|menu|search)\b",
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
    r"(发表于\s*\d{4}[-/]|阅读\s*[\(（]\s*\d+\s*[\)）]|立即登录|注册登录|分享到|"
    r"转载请注明|点击查看原文|责任编辑\s*[:：])",
)

try:  # pragma: no cover - 依赖项目环境，缺失时走本地副本
    from pipeline.steps.llm_processor import _NOISE_PATTERNS as NOISE_PATTERNS
except Exception:
    NOISE_PATTERNS = _FALLBACK_NOISE


def load_items(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"找不到产物文件: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"{path} 内容不是列表，文件可能损坏")
    return data


def display_summary(item: dict) -> str:
    return (item.get("ai_summary_zh") or item.get("ai_summary") or "").strip()


def body_text(item: dict) -> str:
    return re.sub(r"\s+", "", (item.get("full_body") or item.get("summary") or ""))


def body_repeats_summary(item: dict) -> bool:
    """正文栏是否从摘要那一段开始（= 同一段话在报告里显示两遍）。

    只看**开头**：抽取式摘要本身就是原文里的片段，出现在正文中段属正常，
    只有正文开头就是摘要内容时才算重复展示。
    """
    summary = re.sub(r"\s+", "", display_summary(item))
    body = body_text(item)
    if len(summary) < 60 or not body:
        return False
    key = summary[:100]
    return body.startswith(key)


def has_noise(text: str) -> bool:
    return any(re.search(p, text, re.I) for p in NOISE_PATTERNS)


def metrics(items: list[dict]) -> dict:
    n = len(items) or 1
    summaries = [display_summary(i) for i in items]
    filled = [s for s in summaries if s]
    lens = [len(s) for s in filled] or [0]

    truncated = sum(1 for s in filled if s.rstrip().endswith(("...", "…")))
    noisy = sum(1 for s in filled if has_noise(s))
    glued = sum(len(re.findall(r"[a-z][.?!][A-Z]", s)) for s in filled)
    duplicated = sum(1 for i in items if body_repeats_summary(i))

    kinds: dict[str, int] = {}
    for i in items:
        k = i.get("ai_summary_kind") or ("extractive" if display_summary(i) else "empty")
        kinds[k] = kinds.get(k, 0) + 1

    return {
        "total": len(items),
        "empty": sum(1 for i in items if not display_summary(i)),
        "filled": len(filled),
        "truncated": truncated,
        "noisy": noisy,
        "glued": glued,
        "duplicated": duplicated,
        "median_len": st.median(lens),
        "mean_len": st.mean(lens),
        "at_cap": sum(1 for s in filled if len(s) >= 495),
        "kinds": kinds,
        "summaries": [display_summary(i) for i in items],
    }


def report_one(label: str, path: Path) -> dict:
    items = load_items(path)
    m = metrics(items)
    print(f"\n{'=' * 72}\n{label}: {path}\n{'=' * 72}")
    print(f"条目总数            {m['total']}")
    print(f"无摘要              {m['empty']} ({m['empty'] / max(1, m['total']):.1%})")
    print(f"半句截断            {m['truncated']}/{m['filled']} ({m['truncated'] / max(1, m['filled']):.1%})")
    print(f"含网页噪声          {m['noisy']}/{m['filled']} ({m['noisy'] / max(1, m['filled']):.1%})")
    print(f"英文句子粘连        {m['glued']} 处")
    print(f"摘要与正文栏重复    {m['duplicated']}/{m['total']} ({m['duplicated'] / max(1, m['total']):.1%})")
    print(f"长度中位/均值       {m['median_len']:.0f} / {m['mean_len']:.0f}")
    print(f"顶到 500 字上限     {m['at_cap']}/{m['filled']} ({m['at_cap'] / max(1, m['filled']):.1%})")
    if any(k != "empty" for k in m["kinds"]):
        print("来源标记分布        " + ", ".join(f"{k}={v}" for k, v in sorted(m["kinds"].items())))
    return m


def compare(a_path: Path, b_path: Path, show: int) -> None:
    a_items = load_items(a_path)
    b_items = load_items(b_path)
    b_by_url = {i.get("url", ""): i for i in b_items}

    changed = []
    for it in a_items:
        old = display_summary(it)
        new_item = b_by_url.get(it.get("url", ""))
        new = display_summary(new_item) if new_item else ""
        if old != new:
            changed.append((it.get("source_name", ""), it.get("title", ""), old, new))

    print(f"\n{'=' * 72}\n逐条对比: 摘要发生变化的条目 {len(changed)}/{len(a_items)}\n{'=' * 72}")
    for src, title, old, new in changed[:show]:
        print("-" * 72)
        print(f"[{src}] {title[:70]}")
        print(f"  改前({len(old)}): {old[:200]}")
        print(f"  改后({len(new)}): {new[:200]}")
    if len(changed) > show:
        out = Path(__file__).resolve().parent.parent / "Output" / "摘要优化" / "Temp" / "summary_changes.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(
            [{"source": s, "title": t, "old": o, "new": n} for s, t, o, n in changed],
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n… 其余 {len(changed) - show} 条已写入 {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description="摘要质量体检 / 回归对比")
    ap.add_argument("files", nargs="*", help="产物 JSON 路径（0 个=默认产物，1 个=体检，2 个=对比）")
    ap.add_argument("--show", type=int, default=10, help="对比时打印多少条样例（默认 10）")
    args = ap.parse_args()

    paths = [Path(f) for f in args.files] or [DEFAULT_DATA]
    if len(paths) == 1:
        report_one("摘要质量体检", paths[0])
        return
    report_one("改前", paths[0])
    report_one("改后", paths[1])
    compare(paths[0], paths[1], args.show)


if __name__ == "__main__":
    main()
