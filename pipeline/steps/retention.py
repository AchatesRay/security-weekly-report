"""保留期清理 — 日更模式下的磁盘卫生（两周滚动）。

设计要点（2026-11-11 日更改造）：
    1. **不备份**：用户明确要求超期数据直接删。因此这里只做 ``unlink`` /
       ``rmtree``，不生成 ``.bak``、不打 tar 包。
    2. **只删可再生的运行产物**：``reports/data_*.json``、预压缩 ``.gz``、
       正文图片目录、评分历史存档。``data/favorites/`` 是用户收藏的完整快照，
       属**永久保留**，这里用白名单显式跳过（哪怕它的 mtime 再老）。
    3. **保留集先算后删**：图片目录是否删除取决于“保留期内的数据文件是否还
       引用这个 item_id”，先收集引用再删除，避免把仍在展示的图片删掉。
    4. **永不删最新一版数据**：即使距今天数超过保留期（例如管道连续失败两周），
       也保留最新的一份 ``data_<edition>.json`` 及其图片，否则前端会白屏。
    5. 归档页 ``cybersec_daily_*.html`` 在发布目录（项目外），由
       ``scripts/daily_publish.sh`` 用 ``find -mtime`` 清理。

用法::

    python app.py retention --dry-run      # 只打印将删除什么
    python app.py retention                # 真删
    python app.py retention --days 21      # 临时改保留天数
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from ..utils import DATA_DIR, REPORTS_DIR, SETTINGS_PATH

DATA_FILE_RE = re.compile(r"^data_(\d{4}-\d{2}-\d{2})(?:_[A-Za-z0-9_]+)?\.json$")
STATS_FILE_RE = re.compile(r"^stats_(\d{4}-\d{2}-\d{2})\.json$")

DEFAULT_RETENTION_DAYS = 14
IMG_DIR = REPORTS_DIR / "assets" / "img"
SCORING_HISTORY_DIR = DATA_DIR / "scoring_history"
# 永久保留目录（相对 DATA_DIR），任何清理都不得触碰
PROTECTED_DIRS = ("favorites",)


def _retention_days_from_settings(default: int = DEFAULT_RETENTION_DAYS) -> int:
    """保留天数优先取 images.retention_days，其次 report.rolling.window_days。"""
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return default
    for section, key in (("images", "retention_days"),
                         ("report", "window_days")):
        node = cfg.get(section) or {}
        if section == "report":
            node = node.get("rolling") or {}
        try:
            value = int(node.get(key))
            if value > 0:
                return value
        except (TypeError, ValueError):
            continue
    return default


def _parse_day(value: str) -> datetime | None:
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _data_edition(path: Path) -> str | None:
    m = DATA_FILE_RE.match(path.name)
    return m.group(1) if m else None


def collect_plan(days: int = DEFAULT_RETENTION_DAYS,
                 now: datetime | None = None) -> dict:
    """计算清理计划（不落盘），返回各分类的待删/保留清单。"""
    now = now or datetime.now()
    cutoff_day = (now - timedelta(days=max(1, int(days)) - 1)).date()
    cutoff_dt = datetime.combine(cutoff_day, datetime.min.time())

    data_files = sorted(REPORTS_DIR.glob("data_*.json"))
    editions = [(p, _data_edition(p)) for p in data_files]
    valid = [(p, e) for p, e in editions if e]

    keep_data: list[Path] = []
    drop_data: list[Path] = []
    for path, edition in valid:
        day = _parse_day(edition)
        if day and day.date() >= cutoff_day:
            keep_data.append(path)
        else:
            drop_data.append(path)

    # 兜底：最新一版数据（含 _review/_cat_* 同版本文件）永不删除
    if valid:
        latest_edition = max(e for _, e in valid)
        for path, edition in valid:
            if edition == latest_edition and path in drop_data:
                drop_data.remove(path)
                keep_data.append(path)

    # 预压缩文件跟随同名数据文件：数据删了，.gz 一起删
    # 注意：判定用「数据文件是否被删除」，而不是「数据文件是否在 keep_data」——
    # 旧周版命名（如 data_2026W46.json）不匹配 DATA_FILE_RE，不会进 keep_data，
    # 但文件仍在磁盘上；若按 keep_data 判定，就会把它的 .gz 删掉而 json 留下。
    drop_data_set = set(drop_data)
    drop_gz: list[Path] = []
    for gz in REPORTS_DIR.glob("data_*.json.gz"):
        base = Path(str(gz)[: -len(".gz")])
        if base in drop_data_set:
            drop_gz.append(gz)
            continue
        # 同名数据文件还在磁盘上（含未匹配命名的历史文件），保留其 .gz
        if base.exists():
            continue
        # 数据文件已不存在 → .gz 是孤儿，清掉
        drop_gz.append(gz)

    # 图片：已被保留数据的 id 集合
    keep_ids: set[str] = set()
    for path in keep_data:
        try:
            with open(path, encoding="utf-8") as f:
                items = json.load(f)
        except Exception:
            continue
        if isinstance(items, list):
            keep_ids.update(str(i.get("item_id")) for i in items
                            if isinstance(i, dict) and i.get("item_id"))

    drop_img_dirs: list[Path] = []
    keep_img_dirs: list[Path] = []
    if IMG_DIR.exists():
        for d in sorted(p for p in IMG_DIR.iterdir() if p.is_dir()):
            if d.name in keep_ids:
                keep_img_dirs.append(d)
                continue
            # 未被引用：给 1 天宽限，避免误删“刚下载但本次未上屏”的目录
            mtime = datetime.fromtimestamp(d.stat().st_mtime)
            if mtime < now - timedelta(days=1):
                drop_img_dirs.append(d)
            else:
                keep_img_dirs.append(d)

    # 评分历史存档
    drop_stats: list[Path] = []
    keep_stats: list[Path] = []
    if SCORING_HISTORY_DIR.exists():
        stats_files = sorted(SCORING_HISTORY_DIR.glob("stats_*.json"))
        valid_stats = [(p, STATS_FILE_RE.match(p.name).group(1))
                       for p in stats_files if STATS_FILE_RE.match(p.name)]
        for path, day_str in valid_stats:
            day = _parse_day(day_str)
            if day and day >= cutoff_dt:
                keep_stats.append(path)
            else:
                drop_stats.append(path)
        if valid_stats:
            latest_stats = max(valid_stats, key=lambda ps: ps[1])[0]
            if latest_stats in drop_stats:
                drop_stats.remove(latest_stats)
                keep_stats.append(latest_stats)

    return {
        "days": days,
        "cutoff_day": cutoff_day.isoformat(),
        "keep_data": keep_data,
        "drop_data": drop_data,
        "drop_gz": drop_gz,
        "keep_ids": keep_ids,
        "keep_img_dirs": keep_img_dirs,
        "drop_img_dirs": drop_img_dirs,
        "keep_stats": keep_stats,
        "drop_stats": drop_stats,
    }


def run_retention(days: int = DEFAULT_RETENTION_DAYS,
                  dry_run: bool = False) -> dict:
    """执行清理；返回统计。``dry_run=True`` 时只打印不删除。"""
    # 收藏是永久快照：本模块从不构造指向 data/favorites/ 的删除路径，
    # PROTECTED_DIRS 仅作自检文档，改动清理范围时对照检查。
    plan = collect_plan(days=days)
    stats = {
        "days": plan["days"],
        "cutoff_day": plan["cutoff_day"],
        "data_removed": 0,
        "gz_removed": 0,
        "img_dirs_removed": 0,
        "stats_removed": 0,
        "bytes_freed": 0,
        "dry_run": dry_run,
    }

    def _size(path: Path) -> int:
        try:
            if path.is_dir():
                return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            return path.stat().st_size
        except OSError:
            return 0

    tag = "[DRY-RUN] " if dry_run else ""
    print(f"[RETENTION] {tag}保留 {plan['days']} 天，"
          f"截止日期 {plan['cutoff_day']}（不含当日之前）")

    for path in plan["drop_data"]:
        size = _size(path)
        print(f"  {tag}- 数据文件 {path.name} ({size:,} B)")
        stats["bytes_freed"] += size
        stats["data_removed"] += 1
        if not dry_run:
            try:
                path.unlink()
            except OSError as e:
                print(f"    [WARN] 删除失败: {e}")

    for path in plan["drop_gz"]:
        size = _size(path)
        print(f"  {tag}- 预压缩 {path.name} ({size:,} B)")
        stats["bytes_freed"] += size
        stats["gz_removed"] += 1
        if not dry_run:
            try:
                path.unlink()
            except OSError as e:
                print(f"    [WARN] 删除失败: {e}")

    for path in plan["drop_img_dirs"]:
        size = _size(path)
        print(f"  {tag}- 图片目录 {path.name}/ ({size:,} B)")
        stats["bytes_freed"] += size
        stats["img_dirs_removed"] += 1
        if not dry_run:
            try:
                shutil.rmtree(path)
            except OSError as e:
                print(f"    [WARN] 删除失败: {e}")

    for path in plan["drop_stats"]:
        size = _size(path)
        print(f"  {tag}- 评分存档 {path.name} ({size:,} B)")
        stats["bytes_freed"] += size
        stats["stats_removed"] += 1
        if not dry_run:
            try:
                path.unlink()
            except OSError as e:
                print(f"    [WARN] 删除失败: {e}")

    print(f"[RETENTION] {tag}完成：数据 {stats['data_removed']} 个、"
          f"gz {stats['gz_removed']} 个、图片目录 {stats['img_dirs_removed']} 个、"
          f"评分存档 {stats['stats_removed']} 个，"
          f"释放 {stats['bytes_freed'] / 1024 / 1024:.1f} MB")
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清理超过保留期的报告产物（不备份）")
    parser.add_argument("--days", type=int, default=None,
                        help="保留天数，默认读 settings.json（14）")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不删除")
    args = parser.parse_args(argv)
    days = args.days if args.days and args.days > 0 else _retention_days_from_settings()
    run_retention(days=days, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
