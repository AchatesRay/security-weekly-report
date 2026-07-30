#!/usr/bin/env python3
"""
网络安全周报系统 — 主入口

用法:
    python app.py --run          # 执行完整管道
    python app.py --run --skip-fetch  # 跳过抓取，使用已有数据重新生成
    python app.py server [port]  # 启动管理后台
"""

import argparse
import sys
import asyncio
from pathlib import Path
from datetime import datetime

# 确保 data/ 目录存在
DATA_DIR = Path("data")

def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

# 管道错误收集器
_pipeline_errors: list[str] = []

def run_step(step_num: int, total: int, name: str, fn, skip_ok: bool = False):
    """执行单个管道步骤，失败时记错但不阻断后续流程"""
    t0 = datetime.now()
    print(f"[{step_num}/{total}] {datetime.now().strftime('%H:%M:%S')} 正在{name}...")
    try:
        fn()
        elapsed = (datetime.now() - t0).total_seconds()
        print(f"[{step_num}/{total}] {name}完成 ({elapsed:.1f}s)")
        print()
        return True
    except Exception as e:
        elapsed = (datetime.now() - t0).total_seconds()
        msg = f"步骤{step_num}「{name}」失败: {e} ({elapsed:.1f}s)"
        print(f"  [ERROR] {msg}")
        _pipeline_errors.append(msg)
        if skip_ok:
            print(f"  [WARN] 跳过该步骤，继续后续流程")
            print()
            return False
        raise


def run_pipeline(skip_fetch: bool = False):
    """执行完整管道"""
    ensure_dirs()
    global _pipeline_errors
    _pipeline_errors = []
    start = datetime.now()
    print("=== 网络安全周报系统 ===")
    print(f"开始时间: {start.isoformat()}")
    print()

    # 清理上次运行的中间数据，确保不引用过期文件
    for f in ["translated_items.json", "enhanced_items.json"]:
        p = DATA_DIR / f
        if p.exists():
            p.unlink()
            print(f"[CLEAN] 清理过期中间文件: {f}")

    print()

    steps_total = 10

    # 初始化关键字过滤（如无配置则写入默认列表）
    from .steps.keyword_filter import init_default_keywords
    init_default_keywords()

    # Step 1: 抓取
    if skip_fetch:
        print(f"[SKIP] 跳过抓取阶段，使用已有数据\n")
    else:
        from .steps.fetcher import fetch_all
        run_step(1, steps_total, "抓取 RSS 信源", lambda: asyncio.run(fetch_all()))

    # Step 2: 解析
    from .steps.parser import parse_all
    run_step(2, steps_total, "解析 RSS 数据", parse_all, skip_ok=True)

    # Step 3: 去重
    from .steps.deduplicator import run as run_dedup
    run_step(3, steps_total, "去重", run_dedup, skip_ok=True)

    # Step 4: 评分过滤阶段1（快速预筛，<30 分提前丢弃）
    from .steps.keyword_filter import run_stage1 as kw_stage1
    run_step(4, steps_total, "评分过滤（阶段1：快速预筛）", kw_stage1, skip_ok=True)

    # Step 5: 全文提取
    from .steps.fulltext_extractor import run as run_fulltext
    run_step(5, steps_total, "提取全文（摘要过短的文章）", run_fulltext, skip_ok=True)

    # Step 6: 评分过滤阶段2（完整评分 + 分类 + 内容类型 + 地域推断）
    from .steps.keyword_filter import run_stage2 as kw_stage2
    run_step(6, steps_total, "评分过滤（阶段2：完整评分与分类）", kw_stage2, skip_ok=True)

    # Step 7: AI 摘要生成
    from .steps.llm_processor import run as run_llm
    run_step(7, steps_total, "生成 AI 摘要", run_llm, skip_ok=True)

    # Step 8: 翻译摘要
    from .steps.translator import run as run_translate
    run_step(8, steps_total, "翻译英文摘要为中文", run_translate, skip_ok=True)

    # Step 9: 生成报告
    from .steps.report_generator import generate_report
    report_path = None
    try:
        run_step(9, steps_total, "生成 HTML 周报", generate_report)
        report_path = "reports/Security_Reports.html"
    except Exception as e:
        print(f"  [ERROR] 生成报告失败: {e}")

    # Step 10: 移动版转换
    from .steps.mobile_converter import run as run_mobile
    run_step(10, steps_total, "生成移动版页面", run_mobile, skip_ok=True)

    # ── 评分质量周对比 ──
    _print_scoring_comparison()

    elapsed = (datetime.now() - start).total_seconds()
    print(f"=== 完成! 耗时 {elapsed:.1f} 秒 ===")
    if _pipeline_errors:
        print(f"\n⚠️  管道错误摘要 ({len(_pipeline_errors)} 个):")
        for err in _pipeline_errors:
            print(f"  • {err}")
        print()
    if report_path:
        print(f"报告: {report_path}")


def _print_scoring_comparison():
    """对比本轮与上轮的评分统计，标注异常偏离"""
    stats_path = DATA_DIR / "scoring_stats.json"
    if not stats_path.exists():
        return

    try:
        import json
        with open(stats_path, encoding="utf-8") as f:
            cur = json.load(f)
    except Exception:
        return

    # 从存档找上一轮的统计
    prev = None
    archive_dir = DATA_DIR / "scoring_history"
    if archive_dir.exists():
        archives = sorted(archive_dir.glob("stats_*.json"), reverse=True)
        if archives:
            try:
                with open(archives[0], encoding="utf-8") as f:
                    prev = json.load(f)
            except Exception:
                pass

    # 存档本轮数据
    from datetime import datetime
    archive_dir.mkdir(parents=True, exist_ok=True)
    week_str = datetime.now().strftime("%Y%m%d")
    archive_path = archive_dir / f"stats_{week_str}.json"
    from .utils import atomic_write
    atomic_write(archive_path, cur, indent=2)

    if not prev:
        print(f"[MAIN] 评分质量: accepted={cur['total_accepted']}, "
              f"discarded={cur['total_discarded']} "
              f"（首次运行，无历史对比）")
        return

    # 对比决策分布
    cur_total = cur["total_accepted"] + cur["total_discarded"]
    prev_total = prev["total_accepted"] + prev["total_discarded"]

    changes = []
    for key, label in [("total_accepted", "收录"), ("total_discarded", "丢弃")]:
        cur_pct = cur[key] / cur_total * 100 if cur_total else 0
        prev_pct = prev[key] / prev_total * 100 if prev_total else 0
        diff = cur_pct - prev_pct
        if abs(diff) >= 5:
            direction = "↑" if diff > 0 else "↓"
            changes.append(f"  {label}: {prev[key]}→{cur[key]} ({direction}{abs(diff):.0f}%)")

    if changes:
        print(f"[MAIN] ⚠️ 评分分布周变化（偏离≥5%）：")
        for c in changes:
            print(c)
    else:
        print(f"[MAIN] 评分分布稳定（最大偏离<5%）")

    # 分数段对比
    cur_buckets = cur.get("score_buckets", [])
    prev_buckets = prev.get("score_buckets", [])
    if cur_buckets and prev_buckets and len(cur_buckets) == len(prev_buckets):
        bucket_diffs = []
        for i in range(len(cur_buckets)):
            diff = cur_buckets[i] - prev_buckets[i]
            if abs(diff) >= 5:
                lo = i * 10
                hi = min(i * 10 + 9, 100)
                direction = "↑" if diff > 0 else "↓"
                bucket_diffs.append(f"    {lo:3d}-{hi:3d}: {prev_buckets[i]}→{cur_buckets[i]} ({direction}{abs(diff)})")
        if bucket_diffs:
            print(f"  [MAIN] 分数段周变化（差异≥5条）：")
            for d in bucket_diffs:
                print(d)


def main():
    parser = argparse.ArgumentParser(description="网络安全周报系统")
    parser.add_argument("--run", action="store_true", help="执行完整管道")
    parser.add_argument("--skip-fetch", action="store_true", help="跳过抓取阶段")
    args = parser.parse_args()

    if args.run:
        run_pipeline(skip_fetch=args.skip_fetch)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
