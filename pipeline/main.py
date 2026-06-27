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

    # 清理上次运行的中间数据，确保不引用过期文件
    for f in ["enhanced_items.json", "translated_items.json"]:
        p = DATA_DIR / f
        if p.exists():
            p.unlink()
            print(f"[CLEAN] 清理过期中间文件: {f}")

    print()

    steps_total = 10

    # 初始化关键字过滤（如无配置则写入默认列表）
    from .keyword_filter import init_default_keywords
    init_default_keywords()

    # Step 1: 抓取
    if skip_fetch:
        print(f"[SKIP] 跳过抓取阶段，使用已有数据\n")
    else:
        from .fetcher import fetch_all
        run_step(1, steps_total, "抓取 RSS 信源", lambda: asyncio.run(fetch_all()))

    # Step 2: 解析
    from .parser import parse_all
    run_step(2, steps_total, "解析 RSS 数据", parse_all, skip_ok=True)

    # Step 3: 评分过滤阶段1（快速预筛，<30 分提前丢弃）
    from .keyword_filter import run_stage1 as kw_stage1
    run_step(3, steps_total, "评分过滤（阶段1：快速预筛）", kw_stage1, skip_ok=True)

    # Step 4: 全文提取
    from .fulltext_extractor import run as run_fulltext
    run_step(4, steps_total, "提取全文（摘要过短的文章）", run_fulltext, skip_ok=True)

    # Step 5: 评分过滤阶段2（完整评分 + 分类 + 阈值判定）
    from .keyword_filter import run_stage2 as kw_stage2
    run_step(5, steps_total, "评分过滤（阶段2：完整评分与分类）", kw_stage2, skip_ok=True)

    # Step 6: 去重
    from .deduplicator import run as run_dedup
    run_step(6, steps_total, "去重", run_dedup, skip_ok=True)

    # Step 7: 分类
    from .classifier import classify_all
    run_step(7, steps_total, "分类与打标", classify_all, skip_ok=True)

    # Step 8: 翻译
    from .translator import run as run_translate
    run_step(8, steps_total, "翻译英文内容为中文", run_translate, skip_ok=True)

    # Step 9: AI 摘要生成
    from .llm_processor import run as run_llm
    run_step(9, steps_total, "生成 AI 摘要", run_llm, skip_ok=True)

    # Step 10: 生成报告
    from .report_generator import generate_report
    report_path = None
    try:
        run_step(10, steps_total, "生成 HTML 周报", generate_report)
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
