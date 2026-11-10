#!/usr/bin/env python3
"""评分 golden test：用固定样本锁定“收录/待复核/丢弃”的决策与相对顺序。

用途（改评分/词表前必跑）：
    python scripts/scoring_golden_test.py            # 对比固定期望
    python scripts/scoring_golden_test.py --update   # 重新生成期望快照（需人工确认）

设计原则：
  - 断言的是**决策**与**相对顺序**，不要求精确分数（分数会随合理调参变化）。
  - 快照里的 raw_score 允许 ±12 的漂移，超过则提示人工检查。
  - 样本覆盖：KEV 漏洞、厂商发布稿、奖项稿、融资稿、政策、AI 安全、系列周报。
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.steps.scorer import SecurityScorer  # noqa: E402

CASES_PATH = Path(__file__).with_name("scoring_golden_cases.json")
EXPECTED_PATH = Path(__file__).with_name("scoring_golden_expected.json")

# 与业务语义绑定的硬断言：这几条不能靠快照“跟着代码改”
HARD_EXPECTATIONS = {
    "cisa_kev_fortinet": "accepted",
    "project_zero_exploit": "accepted",
    "vendor_launch_platform": "not_accepted",
    "gartner_award": "not_accepted",
    "news_roundup": "not_accepted",
    "siem_buyers_guide": "not_accepted",
}

# 相对顺序：前面的 raw_score 必须高于后面
ORDER_PAIRS = [
    ("cisa_kev_fortinet", "news_roundup"),
    ("project_zero_exploit", "news_roundup"),
    ("unit42_ransomware", "funding_round"),
    ("openssh_rce", "siem_buyers_guide"),
]


def _load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _decision_ok(actual: str, expected: str) -> bool:
    if expected == "not_accepted":
        return actual != "accepted"
    return actual == expected


def main() -> int:
    parser = argparse.ArgumentParser(description="评分 golden test")
    parser.add_argument("--update", action="store_true",
                        help="用当前评分结果重写期望快照")
    args = parser.parse_args()

    cases = _load_json(CASES_PATH)
    scorer = SecurityScorer()
    results = []
    for case in cases:
        item = {
            "title": case["title"],
            "summary": case["summary"],
            "source_name": case["source_name"],
            "language": case.get("language", "en"),
            "url": case.get("url", ""),
        }
        r = scorer.score(item)
        results.append({
            "id": case["id"],
            "decision": r["decision"],
            "score": r["score"],
            "raw_score": r["raw_score"],
            "category": r["category"],
        })

    print(f"{'id':32s} {'decision':10s} {'score':>5s} {'raw':>7s}  category")
    for r in results:
        print(f"{r['id']:32s} {r['decision']:10s} {r['score']:5d} "
              f"{r['raw_score']:7.1f}  {r['category']}")

    if args.update:
        snapshot = {r["id"]: {"decision": r["decision"],
                              "raw_score": r["raw_score"]}
                    for r in results}
        with open(EXPECTED_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"\n已写入期望快照: {EXPECTED_PATH}")
        return 0

    failures = []

    # 1) 硬断言
    by_id = {r["id"]: r for r in results}
    for case_id, expected in HARD_EXPECTATIONS.items():
        actual = by_id[case_id]["decision"]
        if not _decision_ok(actual, expected):
            failures.append(f"{case_id}: 期望 {expected}，实际 {actual}")

    # 2) 快照对比（决策必须一致；raw 允许 ±12）
    if EXPECTED_PATH.exists():
        expected = _load_json(EXPECTED_PATH)
        for r in results:
            exp = expected.get(r["id"])
            if not exp:
                failures.append(f"{r['id']}: 快照缺少该样本（请 --update）")
                continue
            if r["decision"] != exp.get("decision"):
                failures.append(
                    f"{r['id']}: 决策变化 {exp.get('decision')} → {r['decision']}")
            old_raw = exp.get("raw_score")
            if isinstance(old_raw, (int, float)) and abs(r["raw_score"] - old_raw) > 12:
                failures.append(
                    f"{r['id']}: raw_score 漂移 {old_raw} → {r['raw_score']}"
                    "（超过 ±12，请人工确认）")
    else:
        print(f"\n[WARN] 未找到快照 {EXPECTED_PATH.name}，仅执行硬断言。"
              "首次使用请运行 --update 并人工确认。")

    # 3) 相对顺序
    for before, after in ORDER_PAIRS:
        if by_id[before]["raw_score"] <= by_id[after]["raw_score"]:
            failures.append(
                f"顺序异常: {before}({by_id[before]['raw_score']}) "
                f"应高于 {after}({by_id[after]['raw_score']})")

    if failures:
        print("\n[FAIL] golden test 未通过：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\n[OK] golden test 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
