#!/usr/bin/env python3
"""正文近重复合并清单（P0-2 验收/人工抽检用）。

读取 data/parsed_items.json（第 5 步指纹去重后的产物），列出被合并的簇：
代表条目、来源数、重复条数、其他来源 URL。人工逐条确认“同事件合并、
不同事件不合并”，抽检误合并率应 ≤5%。

用法：
    python scripts/dedup_report.py
    python scripts/dedup_report.py --json   # 输出 JSON，便于二次处理
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.utils import DATA_DIR  # noqa: E402

PARSED_ITEMS_PATH = DATA_DIR / "parsed_items.json"


def collect(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"缺少 {path}，请先运行 app.py --run 或 --skip-fetch")
    with open(path, encoding="utf-8") as f:
        items = json.load(f)
    clusters = []
    for item in items:
        count = int(item.get("duplicate_count", 0) or 0)
        if count <= 0:
            continue
        clusters.append({
            "title": item.get("title", ""),
            "source_name": item.get("source_name", ""),
            "sources": item.get("cluster_sources") or item.get("merged_sources") or [],
            "duplicate_count": count,
            "urls": item.get("duplicate_urls") or [],
        })
    clusters.sort(key=lambda c: -c["duplicate_count"])
    return clusters


def main() -> int:
    parser = argparse.ArgumentParser(description="正文近重复合并清单")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--path", default=str(PARSED_ITEMS_PATH), help="parsed_items.json 路径")
    args = parser.parse_args()

    clusters = collect(Path(args.path))
    if args.json:
        print(json.dumps(clusters, ensure_ascii=False, indent=2))
        return 0

    if not clusters:
        print("本轮没有检测到正文近重复合并。")
        return 0

    total_dup = sum(c["duplicate_count"] for c in clusters)
    print(f"共 {len(clusters)} 个合并簇，合并掉 {total_dup} 条重复：\n")
    for idx, c in enumerate(clusters, 1):
        print(f"{idx}. {c['title']}")
        print(f"   代表来源: {c['source_name']}；重复 {c['duplicate_count']} 条")
        print(f"   簇内来源: {', '.join(c['sources']) or '(未知)'}")
        for url in c["urls"][:8]:
            print(f"   - {url}")
        print()
    print("请人工抽检：同一事件合并是否正确；误合并率应 ≤5%。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
