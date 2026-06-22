#!/usr/bin/env python3
"""
网络安全周报系统 — 主入口

用法:
    python main.py --run          # 执行完整管道: 抓取->解析->去重->分类->生成周报
    python main.py --run --skip-fetch  # 跳过抓取，使用已有数据重新生成
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

def run_step(step_num: int, total: int, name: str, fn, skip_ok: bool = False):
    """执行单个管道步骤，失败时记错但不阻断后续流程"""
    print(f"[{step_num}/{total}] 正在{name}...")
    try:
        fn()
        print()
        return True
    except Exception as e:
        print(f"  [ERROR] {name}失败: {e}")
        if skip_ok:
            print(f"  [WARN] 跳过该步骤，继续后续流程")
            print()
            return False
        raise


def run_pipeline(skip_fetch: bool = False):
    """执行完整管道"""
    ensure_dirs()
    start = datetime.now()
    print("=== 网络安全周报系统 ===")
    print(f"开始时间: {start.isoformat()}")
    print()

    steps_total = 5

    # Step 1: 抓取
    if skip_fetch:
        print(f"[SKIP] 跳过抓取阶段，使用已有数据\n")
    else:
        from fetcher import fetch_all
        run_step(1, steps_total, "抓取 RSS 信源", lambda: asyncio.run(fetch_all()))

    # Step 2: 解析
    from parser import parse_all
    run_step(2, steps_total, "解析 RSS 数据", parse_all, skip_ok=True)

    # Step 3: 去重
    from deduplicator import run as run_dedup
    run_step(3, steps_total, "去重", run_dedup, skip_ok=True)

    # Step 4: 分类
    from classifier import classify_all
    run_step(4, steps_total, "分类与打标", classify_all, skip_ok=True)

    # Step 5: 生成报告
    from report_generator import generate_report
    report_path = None
    try:
        run_step(5, steps_total, "生成 HTML 周报", generate_report)
        report_path = "reports/Security_Reports.html"
    except Exception as e:
        print(f"  [ERROR] 生成报告失败: {e}")

    elapsed = (datetime.now() - start).total_seconds()
    print(f"=== 完成! 耗时 {elapsed:.1f} 秒 ===")
    if report_path:
        print(f"报告: {report_path}")


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
