"""正文图片本地化 — 把 content_html 里的站外图片下载到 reports/assets/

为什么必须本地化：
  1. 报告可能被离线打开或转发，热链图片会 404 / 被防盗链拦截；
  2. 后续导出到思源/飞书/WeKnora 时，目标系统无法接收站外热链；
  3. 第三方图片还意味着读者 IP 被外部统计，本地化后不再外泄。

安全约束（与全站一致）：
  - 只允许 http/https，统一下载走 safe_get()（SSRF 防护 + 每次重定向校验）
  - 单图 ≤ max_bytes，仅接受 image/* 且文件头与扩展名匹配
  - 拒绝 SVG（可内嵌脚本，且报告与后台同源）
  - 单篇最多 max_per_article 张，避免个别页面拖垮整轮

失败处理：保留原始 URL（已带 referrerpolicy=no-referrer），只记数不阻断报告。
"""

import hashlib
import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..utils import REPORTS_DIR, SETTINGS_PATH, UnsafeURLError, atomic_write_bytes, safe_get
from ..utils.textnorm import item_id

ASSETS_ROOT = REPORTS_DIR / "assets" / "img"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_DEFAULT_CFG = {
    "enabled": True,
    "max_bytes": 5 * 1024 * 1024,
    "max_per_article": 12,
    "timeout": 10,
    "concurrency": 4,
    "retention_days": 56,
}

_EXT_BY_TYPE = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
    "image/bmp": ".bmp",
}

_SAFE_EXT = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp"})


def load_config() -> dict:
    cfg = dict(_DEFAULT_CFG)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            raw = (json.load(f).get("images") or {})
        for key in cfg:
            if key in raw:
                cfg[key] = raw[key]
    except Exception:
        pass
    return cfg


def _sniff_ext(data: bytes) -> str:
    """按文件头判断图片格式；不认识的返回空串（宁可不存也不存错）"""
    if len(data) < 12:
        return ""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return ".avif"
    if data[:2] == b"BM":
        return ".bmp"
    return ""


def _rel_path(item_key: str, filename: str) -> str:
    """图片对外路径必须是**站内绝对路径**。

    content_html 是从 data_*.json 取回后注入页面的，而页面本身可能位于
    `/zhuanjia/cybersec_report.html`；相对路径会按页面位置解析成
    `/zhuanjia/assets/...`，在线上就是 404。用 `/reports/assets/...` 则同时
    兼容后台（`:8090/`）与 hub 静态服务（`/reports/` → cybersec/reports/）。
    """
    return f"/reports/assets/img/{item_key}/{filename}"


def _download(src: str, referer: str, cfg: dict) -> tuple[bytes, str]:
    """下载单张图片，返回 (bytes, 扩展名)；失败抛异常"""
    resp = safe_get(
        src,
        headers={"User-Agent": USER_AGENT, "Referer": referer, "Accept": "image/*,*/*;q=0.8"},
        timeout=float(cfg.get("timeout", 10)),
        max_bytes=int(cfg.get("max_bytes", 5 * 1024 * 1024)),
    )
    ctype = (resp.header("content-type") or "").split(";")[0].strip().lower()
    if ctype and not ctype.startswith("image/"):
        raise ValueError(f"not-image:{ctype}")
    if ctype == "image/svg+xml":
        raise ValueError("svg-rejected")

    data = resp.content or b""
    if not data:
        raise ValueError("empty-body")
    if len(data) > int(cfg.get("max_bytes", 5 * 1024 * 1024)):
        raise ValueError("too-large")

    ext = _sniff_ext(data)
    if not ext:
        raise ValueError("unknown-format")
    if ctype in _EXT_BY_TYPE and _EXT_BY_TYPE[ctype] != ext:
        # 声明与实际不一致时以文件头为准（常见于 CDN 错误页）
        pass
    return data, ext


def _is_remote(src: str) -> bool:
    return src.lower().startswith(("http://", "https://"))


def _collect_tasks(items: list[dict], cfg: dict) -> tuple[dict[int, BeautifulSoup], list[dict]]:
    """解析每条的 content_html，收集待下载图片。返回 (soup 缓存, 任务列表)"""
    soups: dict[int, BeautifulSoup] = {}
    tasks: list[dict] = []
    limit = max(0, int(cfg.get("max_per_article", 12)))

    for idx, item in enumerate(items):
        html = item.get("content_html") or ""
        if not html or "<img" not in html.lower():
            continue
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            continue
        imgs = soup.find_all("img")
        if not imgs:
            continue

        key = item.get("item_id") or item_id(item.get("url", "")) or f"item{idx}"
        item["item_id"] = key
        selected = 0
        for img in imgs:
            src = (img.get("src") or "").strip()
            if not src:
                continue
            if not _is_remote(src):
                selected += 1
                continue
            if selected >= limit:
                img.decompose()
                continue
            selected += 1
            tasks.append({
                "item_idx": idx,
                "item_key": key,
                "src": src,
                "referer": item.get("url", ""),
                "img": img,
            })
        soups[idx] = soup
    return soups, tasks


def localize_items(items: list[dict]) -> dict:
    """就地改写 items[*]["content_html"] 的图片地址，返回统计信息。"""
    cfg = load_config()
    stats: dict[str, Any] = {
        "enabled": bool(cfg.get("enabled", True)),
        "items_with_html": 0,
        "images_seen": 0,
        "images_localized": 0,
        "images_reused": 0,
        "images_failed": 0,
        "bytes": 0,
    }
    if not stats["enabled"] or not items:
        return stats

    stats["items_with_html"] = sum(1 for i in items if i.get("content_html"))
    if not stats["items_with_html"]:
        return stats

    soups, tasks = _collect_tasks(items, cfg)
    stats["images_seen"] = len(tasks)
    if not tasks:
        return stats

    ASSETS_ROOT.mkdir(parents=True, exist_ok=True)
    workers = max(1, int(cfg.get("concurrency", 4)))

    # 先算目标路径，已存在的直接复用，避免重复下载
    pending: list[dict] = []
    for task in tasks:
        digest = hashlib.sha1(task["src"].encode("utf-8")).hexdigest()[:12]
        task["digest"] = digest
        task["target_dir"] = ASSETS_ROOT / task["item_key"]
        existing = None
        if task["target_dir"].is_dir():
            for ext in _SAFE_EXT:
                candidate = task["target_dir"] / f"{digest}{ext}"
                if candidate.exists() and candidate.stat().st_size > 0:
                    existing = candidate
                    break
        if existing is not None:
            task["local"] = _rel_path(task["item_key"], existing.name)
            task["reused"] = True
        else:
            pending.append(task)

    downloads: dict[int, tuple[bytes, str]] = {}
    failed_reasons: dict[int, str] = {}

    if pending:
        def _work(task: dict) -> tuple[bytes, str]:
            return _download(task["src"], task["referer"], cfg)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_map = {pool.submit(_work, t): t for t in pending}
            for fut in as_completed(future_map):
                task = future_map[fut]
                try:
                    downloads[id(task)] = fut.result()
                except UnsafeURLError as e:
                    failed_reasons[id(task)] = f"blocked:{e}"
                except Exception as e:  # noqa: BLE001 — 单图失败不影响整轮
                    failed_reasons[id(task)] = f"{type(e).__name__}:{e}"

    for task in pending:
        result = downloads.get(id(task))
        if not result:
            stats["images_failed"] += 1
            if failed_reasons.get(id(task)) and stats["images_failed"] <= 3:
                print(f"  [IMAGES] 跳过 {task['src'][:90]} ({failed_reasons[id(task)][:60]})")
            continue
        data, ext = result
        filename = f"{task['digest']}{ext}"
        try:
            task["target_dir"].mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(task["target_dir"] / filename, data)
        except Exception:
            stats["images_failed"] += 1
            continue
        task["local"] = _rel_path(task["item_key"], filename)
        stats["images_localized"] += 1
        stats["bytes"] += len(data)

    # 原样复用已存在的图片（同一篇文章重跑不重复下载）
    stats["images_reused"] = sum(1 for t in tasks if t.get("reused"))

    # 串行改写 soup 并写回 content_html（bs4 Tag 不做跨线程修改）
    by_item: dict[int, list[dict]] = {}
    for task in tasks:
        by_item.setdefault(task["item_idx"], []).append(task)

    for idx, soup in soups.items():
        for task in by_item.get(idx, []):
            local = task.get("local")
            if not local:
                continue  # 保留原外链，前端 onerror 会隐藏坏图
            task["img"]["src"] = local
            task["img"].attrs.pop("referrerpolicy", None)
        items[idx]["content_html"] = str(soup)
    return stats


def prune_assets(retention_days: int | None = None, keep_ids: set[str] | None = None) -> int:
    """清理超过保留期的图片目录，返回删除的目录数。"""
    cfg = load_config()
    days = int(retention_days if retention_days is not None else cfg.get("retention_days", 56))
    if days <= 0 or not ASSETS_ROOT.is_dir():
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for child in ASSETS_ROOT.iterdir():
        if not child.is_dir():
            continue
        if keep_ids and child.name in keep_ids:
            continue
        try:
            if child.stat().st_mtime >= cutoff:
                continue
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
        except OSError:
            continue
    return removed
