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

def run_pipeline(skip_fetch: bool = False):
    """执行完整管道"""
    ensure_dirs()
    start = datetime.now()
    print("=== 网络安全周报系统 ===")
    print(f"开始时间: {start.isoformat()}")
    print()

    # Step 1: 抓取
    if skip_fetch:
        print("[SKIP] 跳过抓取阶段，使用已有数据")
    else:
        print("[1/5] 正在抓取 RSS 信源...")
        from fetcher import fetch_all
        asyncio.run(fetch_all())
        print()

    # Step 2: 解析
    print("[2/5] 正在解析 RSS 数据...")
    from parser import parse_all
    parse_all()
    print()

    # Step 3: 去重
    print("[3/5] 正在去重...")
    from deduplicator import run as run_dedup
    run_dedup()
    print()

    # Step 4: 分类
    print("[4/5] 正在分类与打标...")
    from classifier import classify_all
    classify_all()
    print()

    # Step 5: 生成报告
    print("[5/5] 正在生成 HTML 周报...")
    from report_generator import generate_report
    report_path = generate_report()
    print()

    elapsed = (datetime.now() - start).total_seconds()
    print(f"=== 完成! 耗时 {elapsed:.1f} 秒 ===")
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
