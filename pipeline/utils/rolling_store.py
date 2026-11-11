"""滚动窗口条目库 — 日更模式下的"近 N 天累加"。

设计要点（2026-11-11 日更改造）：
    1. 存的是**最终上屏的 JSON 快照**（report_generator.build_json_items 的产物），
       渲染时直接复用，不重跑评分/摘要/翻译；同一篇文章在窗口内保持稳定，
       不会因为次日重新评分或翻译结果波动而掉出报告或变样。
    2. 窗口裁剪用 ``first_seen``（首次进入窗口的本地日期），**不是**
       ``published_date``：部分信源不提供发布时间，按发布时间裁剪会把这类
       条目立刻判为过期删除。
    3. 只收「收录」条目（filter_decision != review）。待复核条目每天重新产生，
       不参与滚动累加，避免窗口里沉淀一堆没人处理的低分内容。
    4. 同一条目（item_id / 规范化 URL / 标题）重复出现时以**新快照为准**，
       保留最早的 ``first_seen``，保证"新条目累加、旧条目不重复"。

文件位置：``data/state/rolling_items.json``（在 STATE_DIR 下，开跑不会被清空）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from . import STATE_DIR, atomic_write
from .textnorm import item_id, normalize_url

STORE_PATH = STATE_DIR / "rolling_items.json"

STORE_VERSION = 1
DEFAULT_WINDOW_DAYS = 14
# 安全上限：窗口天数 × 每天上限之外再兜一层，防止异常数据把文件撑大
MAX_STORE_ITEMS = 3000


def _today(today: str | None = None) -> str:
    return today or datetime.now().strftime("%Y-%m-%d")


def _item_key(item: dict) -> str:
    """稳定去重键：item_id → 规范化 URL → 标题。"""
    key = (item.get("item_id") or "").strip()
    if key:
        return key
    url = normalize_url(item.get("url", "") or "")
    if url:
        return item_id(url) or url
    title = (item.get("title") or "").strip().lower()
    return f"title:{title}" if title else ""


def load_store() -> dict:
    """读取条目库；文件缺失/损坏时返回空库（不抛异常，报告要能继续生成）。"""
    if not STORE_PATH.exists():
        return {"version": STORE_VERSION, "updated_at": "", "items": []}
    try:
        with open(STORE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data
        if isinstance(data, list):  # 兼容裸列表
            return {"version": STORE_VERSION, "updated_at": "", "items": data}
    except Exception:
        pass
    return {"version": STORE_VERSION, "updated_at": "", "items": []}


def _parse_day(value: str) -> datetime | None:
    """只取日期部分比较；带时分秒与 ISO 'T' 的写法都能识别。"""
    try:
        return datetime.strptime((value or "")[:10], "%Y-%m-%d")
    except ValueError:
        return None


def prune_window(items: list[dict], window_days: int = DEFAULT_WINDOW_DAYS,
                 today: str | None = None) -> tuple[list[dict], int]:
    """丢掉 ``first_seen`` 早于窗口起点的条目；返回 (保留条目, 删除数)。

    窗口口径是"含今天在内的 N 个自然日"：window_days=14 时，
    今天与之前 13 天保留，第 15 天及更早删除。
    """
    today_dt = _parse_day(_today(today)) or datetime.now()
    cutoff = today_dt - timedelta(days=max(1, int(window_days)) - 1)
    kept: list[dict] = []
    dropped = 0
    for item in items:
        if not isinstance(item, dict):
            dropped += 1
            continue
        seen = _parse_day(str(item.get("first_seen") or ""))
        if seen is None:
            # 老数据没有 first_seen：视为今天进入，先保留，下次写入时补上
            item["first_seen"] = today_dt.strftime("%Y-%m-%d")
            kept.append(item)
            continue
        if seen < cutoff:
            dropped += 1
            continue
        kept.append(item)
    return kept, dropped


def merge_items(previous: list[dict], new_items: list[dict],
                window_days: int = DEFAULT_WINDOW_DAYS,
                today: str | None = None) -> tuple[list[dict], dict]:
    """把今天的新条目并入窗口，返回 (合并后条目, 统计)。

    统计字段：added / refreshed / expired / total。
    """
    day = _today(today)
    merged: dict[str, dict] = {}
    order: list[str] = []

    for item in previous or []:
        if not isinstance(item, dict):
            continue
        key = _item_key(item)
        if not key:
            continue
        if key not in merged:
            order.append(key)
        merged[key] = item

    added = 0
    refreshed = 0
    for item in new_items or []:
        if not isinstance(item, dict):
            continue
        key = _item_key(item)
        if not key:
            continue
        old = merged.get(key)
        snapshot = dict(item)
        if old:
            # 保留最早进入窗口的时间；其余字段以新快照为准
            snapshot["first_seen"] = old.get("first_seen") or day
            refreshed += 1
        else:
            snapshot["first_seen"] = snapshot.get("first_seen") or day
            added += 1
            order.append(key)
        snapshot["last_seen"] = day
        merged[key] = snapshot

    result = [merged[k] for k in order if k in merged]
    result, expired = prune_window(result, window_days=window_days, today=day)

    # 兜底：异常情况下条目数超上限时，按 first_seen 从旧到新丢弃
    if len(result) > MAX_STORE_ITEMS:
        overflow = len(result) - MAX_STORE_ITEMS
        result = result[overflow:]
        expired += overflow

    stats = {"added": added, "refreshed": refreshed,
             "expired": expired, "total": len(result)}
    return result, stats


def save_store(items: list[dict]) -> None:
    atomic_write(STORE_PATH, {
        "version": STORE_VERSION,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "items": items,
    }, indent=2)


def update_store(new_items: list[dict], window_days: int = DEFAULT_WINDOW_DAYS,
                 today: str | None = None) -> tuple[list[dict], dict]:
    """读取 → 合并 → 写回；返回窗口内全部条目与统计。"""
    store = load_store()
    merged, stats = merge_items(store.get("items") or [], new_items,
                                window_days=window_days, today=today)
    save_store(merged)
    return merged, stats
