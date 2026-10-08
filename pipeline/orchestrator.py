#!/usr/bin/env python3
"""网络安全周报系统 — 管道编排器

用法:
    python app.py --run               # 执行完整管道
    python app.py --run --skip-fetch  # 跳过抓取，使用已有数据重新生成

2026-09-29 修复（“静默用旧数据重发周报”）：
  1. **开跑先清空中间产物**：此前只清理 translated/enhanced 两个文件，
     某一步失败时下游会读到上一轮遗留的 parsed_items.json，于是用旧内容
     重新生成一份周报，而信源告警已是本轮新数据。
  2. **任一步失败即中止**：此前除第 1 步外所有步骤都是 skip_ok=True，
     失败只打印一行并继续。现在步骤失败会中止整个运行，并且**不生成报告**
     （保留上一版周报），同时以非零退出码结束，便于管理后台如实显示。
  3. **步骤产物各自独立**：每个步骤只读写自己声明的文件，输入缺失时直接
     报错，不再“有就凑合用”。
  4. 每次运行写入 data/pipeline_run.json 记录各步骤状态，便于事后定位。
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from .utils import DATA_DIR, atomic_write

# 每次运行前需要清除的中间产物（保证不会读到上一轮残留）
INTERMEDIATE_FILES = [
    "parsed_items.json",
    "deduped_items.json",
    "classified_items.json",
    "enhanced_items.json",
    "translated_items.json",
    "translation_status.json",
]
# 抓取产物只在真正执行抓取时清除
FETCH_OUTPUTS = ["raw_items.json"]

STEPS_TOTAL = 10


class PipelineAbort(RuntimeError):
    """管道中止（某一步失败）"""


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR.parent / "reports").mkdir(parents=True, exist_ok=True)


def clean_intermediates(skip_fetch: bool) -> list[str]:
    """清理上次运行的中间数据，确保不引用过期文件"""
    removed = []
    names = list(INTERMEDIATE_FILES)
    if not skip_fetch:
        # 真正执行抓取时才清掉抓取产物；--skip-fetch 要复用它
        names += FETCH_OUTPUTS
    for name in names:
        path = DATA_DIR / name
        if path.exists():
            try:
                path.unlink()
                removed.append(name)
            except OSError as e:
                print(f"[CLEAN] 无法删除 {name}: {e}")
    return removed


def run_step(step_num: int, name: str, fn, record: dict) -> None:
    """执行单个管道步骤。失败会抛出 PipelineAbort 中止整个运行。"""
    t0 = datetime.now()
    print(f"[{step_num}/{STEPS_TOTAL}] {datetime.now().strftime('%H:%M:%S')} 正在{name}...")
    try:
        fn()
    except Exception as e:
        elapsed = (datetime.now() - t0).total_seconds()
        msg = f"步骤{step_num}「{name}」失败: {type(e).__name__}: {e}"
        record["steps"].append({"step": step_num, "name": name,
                                "status": "failed", "error": msg,
                                "elapsed": round(elapsed, 1)})
        print(f"  [ERROR] {msg}")
        import traceback
        traceback.print_exc()
        raise PipelineAbort(msg) from e
    elapsed = (datetime.now() - t0).total_seconds()
    record["steps"].append({"step": step_num, "name": name,
                            "status": "ok", "elapsed": round(elapsed, 1)})
    print(f"[{step_num}/{STEPS_TOTAL}] {name}完成 ({elapsed:.1f}s)")
    print()


def run_pipeline(skip_fetch: bool = False) -> bool:
    """执行完整管道。成功返回 True，任一步失败返回 False（不生成报告）。"""
    ensure_dirs()
    start = datetime.now()
    record = {
        "started_at": start.isoformat(),
        "skip_fetch": skip_fetch,
        "steps": [],
        "status": "running",
    }

    print("=== 网络安全周报系统 ===")
    print(f"开始时间: {start.isoformat()}")
    print()

    removed = clean_intermediates(skip_fetch)
    if removed:
        print(f"[CLEAN] 已清理上一轮中间产物: {', '.join(removed)}")
    print()

    # 初始化关键字过滤（确认评分配置可用）
    from .steps.keyword_filter import init_default_keywords
    if not init_default_keywords():
        print("  [ERROR] 评分关键词配置不可用，管道无法继续")
        record["status"] = "failed"
        record["error"] = "评分配置缺失"
        atomic_write(DATA_DIR / "pipeline_run.json", record, indent=2)
        return False

    try:
        # Step 1: 抓取
        if skip_fetch:
            print("[SKIP] 跳过抓取阶段，使用已有 raw_items.json\n")
            if not (DATA_DIR / "raw_items.json").exists():
                raise PipelineAbort(
                    "指定了 --skip-fetch，但 data/raw_items.json 不存在，无数据可用")
        else:
            from .steps.fetcher import fetch_all
            run_step(1, "抓取 RSS 信源", lambda: asyncio.run(fetch_all()), record)

        # Step 2: 解析
        from .steps.parser import parse_all
        run_step(2, "解析 RSS 数据", parse_all, record)

        # Step 3: 去重（URL 规范化 + 标题相似度 + 过期过滤）
        from .steps.deduplicator import run as run_dedup
        run_step(3, "去重与过期过滤", run_dedup, record)

        # Step 4: 评分过滤阶段1（快速预筛，<30 分提前丢弃）
        from .steps.keyword_filter import run_stage1
        run_step(4, "评分过滤（阶段1：快速预筛）", run_stage1, record)

        # Step 5: 全文提取
        from .steps.fulltext_extractor import run as run_fulltext
        run_step(5, "提取全文（摘要过短的文章）", run_fulltext, record)

        # Step 6: 评分过滤阶段2（完整评分 + 分类 → classified_items.json）
        from .steps.keyword_filter import run_stage2
        run_step(6, "评分过滤（阶段2：完整评分与分类）", run_stage2, record)

        # Step 7: 摘要生成（TextRank 抽取式）
        from .steps.llm_processor import run as run_llm
        run_step(7, "生成摘要", run_llm, record)

        # Step 8: 翻译（非中文 → 中文）
        from .steps.translator import run as run_translate
        run_step(8, "翻译非中文内容", run_translate, record)

        # Step 9: 生成报告
        from .steps.report_generator import generate_report
        run_step(9, "生成 HTML 周报", generate_report, record)

        # Step 10: 移动版
        from .steps.mobile_converter import run as run_mobile
        run_step(10, "生成移动版页面", run_mobile, record)

    except PipelineAbort as e:
        print()
        print("=" * 60)
        print("⚠️  管道中止：后续步骤未执行，**报告未被更新**（保留上一版）")
        print(f"    原因: {e}")
        print("=" * 60)
        record["status"] = "failed"
        record["error"] = str(e)
        record["finished_at"] = datetime.now().isoformat()
        record["elapsed"] = round((datetime.now() - start).total_seconds(), 1)
        atomic_write(DATA_DIR / "pipeline_run.json", record, indent=2)
        return False

    elapsed = (datetime.now() - start).total_seconds()
    print(f"=== 完成! 耗时 {elapsed:.1f} 秒 ===")

    # ── 评分质量周对比 ──
    _print_scoring_comparison()

    record["status"] = "ok"
    record["finished_at"] = datetime.now().isoformat()
    record["elapsed"] = round(elapsed, 1)
    atomic_write(DATA_DIR / "pipeline_run.json", record, indent=2)

    print("报告: reports/Security_Reports.html")
    return True


def _load_json(path: Path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _print_scoring_comparison():
    """对比本轮与上一周的评分统计，标注异常偏离"""
    stats_path = DATA_DIR / "scoring_stats.json"
    cur = _load_json(stats_path)
    if not cur:
        return

    week_str = datetime.now().strftime("%Y%m%d")
    archive_dir = DATA_DIR / "scoring_history"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"stats_{week_str}.json"

    # 上一周 = 除本周存档之外最新的一个（原先会把同日更早的一次运行当成“上周”）
    prev = None
    archives = sorted(
        (p for p in archive_dir.glob("stats_*.json") if p.name != archive_path.name),
        reverse=True)
    if archives:
        prev = _load_json(archives[0])

    atomic_write(archive_path, cur, indent=2)

    if not prev:
        print(f"[MAIN] 评分质量: accepted={cur.get('total_accepted', 0)}, "
              f"review={cur.get('total_review', 0)}, "
              f"discarded={cur.get('total_discarded', 0)} （无历史可对比）")
        return

    def total(d):
        return (d.get("total_accepted", 0) + d.get("total_review", 0)
                + d.get("total_discarded", 0))

    cur_total, prev_total = total(cur), total(prev)
    changes = []
    for key, label in (("total_accepted", "收录"), ("total_review", "待复核"),
                       ("total_discarded", "丢弃")):
        cur_pct = cur.get(key, 0) / cur_total * 100 if cur_total else 0
        prev_pct = prev.get(key, 0) / prev_total * 100 if prev_total else 0
        diff = cur_pct - prev_pct
        if abs(diff) >= 5:
            direction = "↑" if diff > 0 else "↓"
            changes.append(f"  {label}: {cur.get(key, 0)} (占比 {direction}{abs(diff):.0f}%)")

    if changes:
        print("[MAIN] ⚠️ 评分分布周变化（偏离≥5%）：")
        for c in changes:
            print(c)
    else:
        print("[MAIN] 评分分布稳定（最大偏离<5%）")

    # 阈值变化提示：阈值一改，历史对比口径就变了
    cur_thr = cur.get("thresholds")
    prev_thr = prev.get("thresholds")
    if cur_thr and prev_thr and cur_thr != prev_thr:
        print(f"[MAIN] 注意: 评分阈值已变化 {prev_thr} → {cur_thr}，对比仅供参考")

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
                bucket_diffs.append(
                    f"    {lo:3d}-{hi:3d}: {prev_buckets[i]}→{cur_buckets[i]} "
                    f"({direction}{abs(diff)})")
        if bucket_diffs:
            print("  [MAIN] 分数段周变化（差异≥5条）：")
            for d in bucket_diffs:
                print(d)


def main():
    parser = argparse.ArgumentParser(description="网络安全周报系统")
    parser.add_argument("--run", action="store_true", help="执行完整管道")
    parser.add_argument("--skip-fetch", action="store_true", help="跳过抓取阶段")
    args = parser.parse_args()

    if args.run:
        ok = run_pipeline(skip_fetch=args.skip_fetch)
        sys.exit(0 if ok else 1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
