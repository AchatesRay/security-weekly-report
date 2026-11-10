#!/usr/bin/env python3
"""文章收藏 — 存储、自动标签与导出

设计要点（与周报生产链路解耦）：
  - 每篇收藏是 data/favorites/<id>.json 的完整快照，不依赖每周被覆盖的
    reports/data_*.json；周报更新或原文失效后，收藏页仍能看到当时的内容。
  - 不维护总索引文件：列表直接扫描目录，避免“索引与文章不一致”。几百篇
    规模下扫描成本可以忽略。
  - 正文来自外部网站且收藏页与后台同源，入库前必须用白名单再净化一次。
  - 自动标签完全离线、可复现：分类短名 + CVE + 领域词表 + 具体命中关键词 +
    内容类型；规则在 config/favorite_tags.json，人工可调。
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from pipeline.utils import atomic_write
from pipeline.utils.html_sanitize import sanitize_fragment

ID_RE = re.compile(r"^[0-9a-f]{16}$")
CVE_RE = re.compile(r"CVE-\d{4}-\d{4,8}", re.IGNORECASE)
SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
_LOCAL_IMG_RE = re.compile(
    r"^/reports/assets/img/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)$")


def _favorite_asset_src_re(fid: str) -> re.Pattern:
    return re.compile(rf"^/api/favorites/assets/{re.escape(fid)}/([A-Za-z0-9._-]+)$")

MAX_TAGS = 20
MAX_TAG_CHARS = 20
MAX_NOTE_CHARS = 2000
MAX_TITLE_CHARS = 500
MAX_URL_CHARS = 2000
MAX_SUMMARY_CHARS = 20000
MAX_TEXT_CHARS = 200_000
MAX_TOTAL_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGES = 12

# 只允许保留的请求字段：客户端可以多传，但不会原样落盘
_ALLOWED_FIELDS = (
    "url", "title", "source_name", "published_date", "category",
    "content_type", "source_type", "summary", "ai_summary", "full_body",
    "content_html", "week", "confidence_score", "raw_score", "source_hue",
)


def _clean_text(value, limit: int) -> str:
    """去掉控制字符并截断，避免异常输入污染 JSON / 页面"""
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text.strip()[:limit]


def canonicalize_url(url: str) -> str:
    """规范化链接：去 fragment、去跟踪参数、统一大小写与结尾斜杠"""
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return raw
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
        and k.lower() not in ("spm", "from", "share_token", "scene", "srcid")
    ]
    return urlunsplit((
        parts.scheme.lower(),
        parts.netloc.lower(),
        parts.path.rstrip("/") or "/",
        urlencode(sorted(query)),
        "",
    ))


def favorite_id(url: str, title: str) -> str:
    """收藏编号：链接哈希；无链接时退回标题哈希（同一篇不会重复收藏）"""
    basis = canonicalize_url(url) or ("title:" + (title or "").strip())
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def normalize_tags(tags) -> list[str]:
    """清洗标签列表：去空、去重（忽略大小写）、限长限量"""
    if not isinstance(tags, (list, tuple)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = _clean_text(raw, MAX_TAG_CHARS)
        tag = re.sub(r"\s+", " ", tag).strip()
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(tag)
        if len(result) >= MAX_TAGS:
            break
    return result


class FavoritesStore:
    """data/favorites/ 的读写入口（线程内安全，写操作串行化）"""

    def __init__(self, favorites_dir: Path, rules_path: Path,
                 reports_dir: Path, max_image_bytes: int = 5 * 1024 * 1024):
        self.dir = Path(favorites_dir)
        self.assets_dir = self.dir / "assets"
        self.rules_path = Path(rules_path)
        self.reports_dir = Path(reports_dir)
        self.max_image_bytes = max_image_bytes
        self._lock = threading.RLock()
        self._rules_cache: tuple[float, dict] | None = None

    # ── 规则 ──

    def _rules(self) -> dict:
        try:
            mtime = self.rules_path.stat().st_mtime
        except OSError:
            return {}
        if self._rules_cache and self._rules_cache[0] == mtime:
            return self._rules_cache[1]
        try:
            rules = json.loads(self.rules_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(rules, dict):
            return {}
        self._rules_cache = (mtime, rules)
        return rules

    # ── 自动标签 ──

    def build_auto_tags(self, record: dict) -> list[str]:
        rules = self._rules()
        max_tags = int(rules.get("max_tags", 6) or 6)
        max_tag_chars = int(rules.get("max_tag_chars", MAX_TAG_CHARS) or 20)
        max_cve = int(rules.get("max_cve_tags", 3) or 3)
        max_topic = int(rules.get("max_topic_tags", 2) or 2)
        max_keyword = int(rules.get("max_keyword_tags", 2) or 2)

        title = record.get("title", "")
        summary = record.get("summary", "")
        ai_summary = record.get("ai_summary", "")
        text = "\n".join((title, ai_summary, summary))
        text_cf = text.casefold()

        tags: list[str] = []
        seen: set[str] = set()

        def add(tag: str):
            tag = _clean_text(tag, max_tag_chars)
            tag = re.sub(r"\s+", " ", tag).strip()
            if not tag or len(tag) > max_tag_chars:
                return False
            key = tag.casefold()
            if key in seen:
                return False
            seen.add(key)
            tags.append(tag)
            return True

        # 1) 主题分类（固定短名；没有映射时取分类名去掉序号）
        category = record.get("category", "")
        category_map = rules.get("category_tags") or {}
        category_tag = category_map.get(category)
        if not category_tag and category:
            category_tag = re.sub(r"^[①②③④⑤⑥⑦]\s*", "", category)
        if category_tag:
            add(category_tag)

        # 2) CVE 编号（最多 3 个）
        cve_added = 0
        for cve in CVE_RE.findall(text):
            if cve_added >= max_cve:
                break
            if add(cve.upper()):
                cve_added += 1

        # 3) 领域词表命中（更具体，优先于零散关键词）
        topic_added = 0
        for rule in rules.get("topic_rules") or []:
            if topic_added >= max_topic:
                break
            if not isinstance(rule, dict):
                continue
            needles = [str(x).casefold() for x in (rule.get("any") or []) if str(x).strip()]
            if any(n in text_cf for n in needles):
                if add(str(rule.get("tag", ""))):
                    topic_added += 1

        # 4) 具体命中关键词：优先出现在标题里的，其次较长的
        generic = {str(x).casefold() for x in (rules.get("generic_keywords") or [])}
        aliases = {str(k).casefold(): str(v)
                   for k, v in (rules.get("keyword_aliases") or {}).items()}
        matched = record.get("scoring_matched") or {}
        if not isinstance(matched, dict):
            matched = {}
        candidates: list[tuple[float, str]] = []
        for level in ("strong", "medium"):
            for kw in matched.get(level) or []:
                kw = _clean_text(kw, 40)
                if not kw or kw.casefold() in generic:
                    continue
                score = (3.0 if kw.casefold() in title.casefold() else 0.0)
                score += 2.0 if kw.casefold() in ai_summary.casefold() else 0.0
                score += min(len(kw), 12) / 12.0
                display = aliases.get(kw.casefold(), kw)
                candidates.append((score, display))
        candidates.sort(key=lambda item: item[0], reverse=True)
        keyword_added = 0
        for _, display in candidates:
            if keyword_added >= max_keyword:
                break
            if add(display):
                keyword_added += 1

        # 5) 内容类型
        content_type = record.get("content_type", "")
        skip_types = {str(x) for x in (rules.get("skip_content_types") or [])}
        if content_type and content_type not in skip_types:
            add(content_type)

        return tags[:max_tags]

    # ── 记录读写 ──

    def _path(self, fid: str) -> Path:
        if not ID_RE.match(fid or ""):
            raise ValueError("非法的收藏编号")
        return self.dir / f"{fid}.json"

    @staticmethod
    def meta(record: dict) -> dict:
        """列表用的轻量元信息（不含正文）"""
        summary = record.get("ai_summary") or record.get("summary") or ""
        return {
            "id": record.get("id", ""),
            "url": record.get("url", ""),
            "title": record.get("title", ""),
            "source_name": record.get("source_name", ""),
            "published_date": record.get("published_date", ""),
            "category": record.get("category", ""),
            "content_type": record.get("content_type", ""),
            "tags": record.get("tags") or [],
            "note": record.get("note", ""),
            "saved_at": record.get("saved_at", ""),
            "week": record.get("week", ""),
            "summary": summary[:300],
            "has_assets": bool(record.get("asset_files")),
        }

    def list_meta(self) -> tuple[list[dict], list[dict]]:
        items: list[dict] = []
        if not self.dir.exists():
            return items, []
        for path in self.dir.glob("*.json"):
            if not ID_RE.match(path.stem):
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(record, dict):
                record.setdefault("id", path.stem)
                items.append(self.meta(record))
        items.sort(key=lambda r: r.get("saved_at", ""), reverse=True)
        counter = Counter()
        for item in items:
            for tag in item.get("tags") or []:
                counter[tag] += 1
        tags = [{"name": name, "count": count}
                for name, count in counter.most_common()]
        return items, tags

    def get(self, fid: str) -> dict | None:
        path = self._path(fid)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return record if isinstance(record, dict) else None

    def _normalize_payload(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("收藏内容格式错误")
        url = _clean_text(payload.get("url"), MAX_URL_CHARS)
        if url and not re.match(r"^https?://", url, re.IGNORECASE):
            raise ValueError("只支持 http/https 链接")
        title = _clean_text(payload.get("title"), MAX_TITLE_CHARS)
        if not title:
            raise ValueError("标题不能为空")

        record = {
            "url": url,
            "title": title,
            "source_name": _clean_text(payload.get("source_name"), 200),
            "published_date": _clean_text(payload.get("published_date"), 40),
            "category": _clean_text(payload.get("category"), 100),
            "content_type": _clean_text(payload.get("content_type"), 50),
            "source_type": _clean_text(payload.get("source_type"), 50),
            "summary": _clean_text(payload.get("summary"), MAX_SUMMARY_CHARS),
            "ai_summary": _clean_text(payload.get("ai_summary"), MAX_SUMMARY_CHARS),
            "full_body": _clean_text(payload.get("full_body"), MAX_TEXT_CHARS),
            "week": _clean_text(payload.get("week"), 20),
        }
        # 评分字段只用于展示/排序，限制为数字
        for key in ("confidence_score", "raw_score", "source_hue"):
            try:
                record[key] = int(payload.get(key) or 0)
            except (TypeError, ValueError):
                record[key] = 0

        raw_html = _clean_text(payload.get("content_html") or "", 2_000_000)
        # 周报里的图片已本地化为 /reports/assets/...；直接按文章 URL 补全会把它们
        # 变成外站地址，因此先换成保留域名，净化后再换回本地路径。
        protected = re.sub(
            r"(src\s*=\s*[\"'])/reports/assets/img/",
            r"\1https://favorites.invalid/reports/assets/img/",
            raw_html, flags=re.IGNORECASE)
        cleaned = sanitize_fragment(protected, base_url=url)
        record["content_html"] = cleaned.replace(
            "https://favorites.invalid/reports/assets/img/",
            "/reports/assets/img/")
        matched = payload.get("scoring_matched") or {}
        if isinstance(matched, dict):
            record["scoring_matched"] = {
                level: [_clean_text(x, 60) for x in (matched.get(level) or [])[:30]
                        if _clean_text(x, 60)]
                for level in ("strong", "medium", "weak")
            }
        else:
            record["scoring_matched"] = {}
        return record

    def _localize_images(self, fid: str, html_text: str) -> tuple[str, list[str]]:
        """把周报里已本地化的图片复制进收藏目录，并改写为收藏资源地址"""
        if not html_text or "<img" not in html_text:
            return html_text, []
        try:
            soup = BeautifulSoup(html_text, "lxml")
        except Exception:
            return html_text, []
        files: list[str] = []
        total_bytes = 0
        dest_dir = self.assets_dir / fid
        already = _favorite_asset_src_re(fid)
        for img in soup.find_all("img"):
            src = str(img.get("src") or "")
            # 再次收藏同一篇时，图片已经被改写成收藏地址，直接保留
            existing_match = already.match(src)
            if existing_match:
                files.append(existing_match.group(1))
                continue
            match = _LOCAL_IMG_RE.match(src)
            if not match:
                continue  # 远程图片保持原链，不做热链下载
            item_key, filename = match.group(1), match.group(2)
            if not SAFE_FILENAME_RE.match(filename):
                img.decompose()
                continue
            source = self.reports_dir / "assets" / "img" / item_key / filename
            try:
                size = source.stat().st_size
            except OSError:
                img.decompose()
                continue
            if (len(files) >= MAX_IMAGES
                    or size > self.max_image_bytes
                    or total_bytes + size > MAX_TOTAL_IMAGE_BYTES):
                img.decompose()
                continue
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / filename
            if not dest.exists():
                try:
                    shutil.copyfile(source, dest)
                except OSError:
                    img.decompose()
                    continue
            img["src"] = f"/api/favorites/assets/{fid}/{filename}"
            files.append(filename)
            total_bytes += size
        if not files:
            return html_text, []
        body = soup.body or soup
        return body.decode_contents().strip(), files

    def save(self, payload: dict) -> tuple[dict, bool]:
        """新增或刷新收藏；已存在时保留用户标签与备注"""
        record = self._normalize_payload(payload)
        fid = favorite_id(record["url"], record["title"])
        path = self._path(fid)

        with self._lock:
            existing = None
            if path.exists():
                try:
                    existing = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    existing = None

            created = existing is None
            if created:
                record["auto_tags"] = self.build_auto_tags(record)
                record["tags"] = list(record["auto_tags"])
                record["note"] = ""
                record["saved_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            else:
                record["auto_tags"] = existing.get("auto_tags") or self.build_auto_tags(record)
                record["tags"] = normalize_tags(existing.get("tags")) or list(record["auto_tags"])
                record["note"] = _clean_text(existing.get("note"), MAX_NOTE_CHARS)
                record["saved_at"] = existing.get("saved_at") or \
                    datetime.now().astimezone().isoformat(timespec="seconds")

            record["id"] = fid
            record["content_html"], record["asset_files"] = \
                self._localize_images(fid, record["content_html"])
            atomic_write(path, record, indent=2)
            return record, created

    def update(self, fid: str, tags=None, note=None) -> dict:
        with self._lock:
            record = self.get(fid)
            if record is None:
                raise KeyError(fid)
            if tags is not None:
                record["tags"] = normalize_tags(tags)
            if note is not None:
                record["note"] = _clean_text(note, MAX_NOTE_CHARS)
            record["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            atomic_write(self._path(fid), record, indent=2)
            return record

    def delete(self, fid: str) -> bool:
        with self._lock:
            path = self._path(fid)
            if not path.exists():
                return False
            path.unlink()
            asset_dir = self.assets_dir / fid
            if asset_dir.exists():
                shutil.rmtree(asset_dir, ignore_errors=True)
            return True

    # ── 图片资源 ──

    def asset_path(self, fid: str, filename: str) -> Path:
        if not ID_RE.match(fid or "") or not SAFE_FILENAME_RE.match(filename or ""):
            raise ValueError("非法的资源路径")
        root = (self.assets_dir / fid).resolve()
        path = (root / filename).resolve()
        if root not in path.parents:
            raise ValueError("非法的资源路径")
        return path

    @staticmethod
    def asset_content_type(filename: str) -> str:
        guessed = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if guessed.startswith("image/"):
            return guessed
        return "application/octet-stream"

    # ── 导出 ──

    def export_markdown(self) -> str:
        items, _ = self.list_meta()
        lines = [
            "# 我的收藏",
            "",
            f"导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}　共 {len(items)} 篇",
            "",
        ]
        for item in items:
            lines.append(f"## {item['title']}")
            lines.append("")
            meta = []
            if item.get("source_name"):
                meta.append(f"来源：{item['source_name']}")
            if item.get("published_date"):
                meta.append(f"发布：{item['published_date']}")
            if item.get("saved_at"):
                meta.append(f"收藏：{item['saved_at'][:10]}")
            if meta:
                lines.append("　·　".join(meta))
                lines.append("")
            if item.get("tags"):
                lines.append("标签：" + "、".join(item["tags"]))
                lines.append("")
            if item.get("note"):
                lines.append(f"> 备注：{item['note']}")
                lines.append("")
            if item.get("summary"):
                lines.append(item["summary"])
                lines.append("")
            if item.get("url"):
                lines.append(f"[阅读原文]({item['url']})")
                lines.append("")
            lines.append("---")
            lines.append("")
        return "\n".join(lines)

    def export_json(self) -> str:
        items, _ = self.list_meta()
        records = []
        for item in items:
            record = self.get(item["id"])
            if record:
                records.append(record)
        return json.dumps(records, ensure_ascii=False, indent=2)
