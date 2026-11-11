#!/usr/bin/env python3
"""
安全管理后台 — 配置服务器

提供 REST API + 静态文件服务，用于管理周报系统的各项配置。

用法:
    python config_server.py [--project-dir DIR] [port]
    python config_server.py 8090
    python config_server.py --project-dir /path/to/project 8090

默认端口 8090，--project-dir 默认为项目根目录（server/ 的父目录）。
访问 http://localhost:8090/config.html

安全说明（2026-09-29 加固）：
  - 静态文件服务改为**白名单**：仅放行管理页面与周报产物，其余一律 403。
    此前直接复用 SimpleHTTPRequestHandler 会对外暴露 .env、config/ 密钥、
    docs/ 部署文档（含 SSH 凭据）、data/ 中间数据与全部源码。
  - 认证失败按来源 IP 计数并锁定；用户名/密码使用固定耗时比较。
  - 请求体大小设上限；配置写入前做结构与必填字段校验；写入均为原子替换。
  - 不再发送 Access-Control-Allow-Origin: *（管理页面与接口同源，无需 CORS）。
"""

import json
import os
import re
import sys
import gzip
import hmac
import time
import base64
import subprocess
import http.server
import urllib.parse
from pathlib import Path
from datetime import datetime
from io import BytesIO

# 从 .env 文件加载环境变量（如果存在）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── 路径初始化（在 main() 中调用 _init_paths 完成）──
SERVER_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = None
_CONFIG_DIR = None
_DATA_DIR = None
_REPORT_DIR = None
_SETTINGS_PATH = None
_SOURCES_PATH = None
_LLM_PATH = None
_SCORING_KEYWORDS_PATH = None
_PIPELINE_LOG_PATH = None

_pipeline_proc: "subprocess.Popen | None" = None
_FAVORITES = None          # FavoritesStore，在 _init_paths 中创建
_ANNOTATIONS = None        # AnnotationStore，在 _init_paths 中创建

# ── 常量 ──
MAX_BODY_BYTES = 4 * 1024 * 1024          # 单个请求体上限 4MB
MAX_AUTH_FAILURES = 10                    # 同一 IP 连续认证失败上限
AUTH_LOCKOUT_SECONDS = 300                # 触发上限后的锁定时长

# 静态文件白名单：仅这些前缀/文件可被直接下载
_STATIC_ALLOW_EXACT = frozenset({"server/config.html", "server/bookmarks.html"})
_STATIC_ALLOW_PREFIXES = ("reports/", "pipeline/assets/")


def _init_paths(project_dir: str | None = None):
    global _PROJECT_DIR, _CONFIG_DIR, _DATA_DIR, _REPORT_DIR
    global _SETTINGS_PATH, _SOURCES_PATH, _LLM_PATH
    global _PIPELINE_LOG_PATH, _SCORING_KEYWORDS_PATH, _FAVORITES
    global _ANNOTATIONS

    _PROJECT_DIR = Path(project_dir).resolve() if project_dir else SERVER_DIR.parent
    # 确保 pipeline 模块可导入
    if str(_PROJECT_DIR) not in sys.path:
        sys.path.insert(0, str(_PROJECT_DIR))
    _CONFIG_DIR = _PROJECT_DIR / "config"
    _DATA_DIR = _PROJECT_DIR / "data"
    _REPORT_DIR = _PROJECT_DIR / "reports"
    _SETTINGS_PATH = _CONFIG_DIR / "settings.json"
    _SOURCES_PATH = _CONFIG_DIR / "source_config.yaml"
    _LLM_PATH = _CONFIG_DIR / "llm_config.yaml"
    _SCORING_KEYWORDS_PATH = _CONFIG_DIR / "scoring_keywords.json"
    _PIPELINE_LOG_PATH = _DATA_DIR / "pipeline_run.log"

    # 收藏模块：独立目录 + 规则文件；图片上限沿用 settings.images.max_bytes
    from favorites import FavoritesStore
    max_image_bytes = 5 * 1024 * 1024
    try:
        images_cfg = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8")).get("images") or {}
        max_image_bytes = int(images_cfg.get("max_bytes", max_image_bytes))
    except (OSError, ValueError, TypeError):
        pass
    _FAVORITES = FavoritesStore(
        favorites_dir=_DATA_DIR / "favorites",
        rules_path=_CONFIG_DIR / "favorite_tags.json",
        reports_dir=_REPORT_DIR,
        max_image_bytes=max_image_bytes,
    )

    # 选文标注：追加式 JSONL，存放规则调优的"人判"证据。
    # 不参与管道，写接口沿用后台 Basic Auth + 同源校验。
    from pipeline.annotations import AnnotationStore
    _ANNOTATIONS = AnnotationStore(_DATA_DIR / "state" / "annotations")


# ── Read/Write helpers ──

class ConfigValidationError(ValueError):
    """配置内容未通过校验"""


def _atomic_text(path: Path, content: str):
    """原子写入文本，避免写入中途崩溃留下半截配置"""
    from pipeline.utils import atomic_write_text
    atomic_write_text(path, content)


def read_yaml(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def write_yaml(path: Path, content: str) -> bool:
    try:
        _atomic_text(path, content)
        return True
    except Exception as e:
        print(f"[CONFIG] 写入失败 {path.name}: {e}")
        return False


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_json(path: Path, data: dict) -> bool:
    try:
        from pipeline.utils import atomic_write
        atomic_write(path, data, indent=2)
        return True
    except Exception as e:
        print(f"[CONFIG] 写入失败 {path.name}: {e}")
        return False


def send_json(handler, data, status=200):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.end_headers()
        handler.wfile.write(body)
    except OSError:
        pass  # 客户端断开连接，忽略


_FAVORITE_PATH_RE = re.compile(r"^/api/favorites/([0-9a-f]{16})$")


def _favorite_id_from_path(path: str) -> str | None:
    """从 /api/favorites/<16 位编号> 中取出编号；不匹配返回 None"""
    match = _FAVORITE_PATH_RE.match(path or "")
    return match.group(1) if match else None


def read_body(handler) -> str:
    """读取请求体，带大小上限（防超大请求打爆内存）"""
    raw_len = handler.headers.get("Content-Length")
    if raw_len is None:
        return ""
    try:
        length = int(raw_len)
    except (TypeError, ValueError):
        raise ConfigValidationError(f"非法的 Content-Length: {raw_len!r}")
    if length <= 0:
        return ""
    if length > MAX_BODY_BYTES:
        raise ConfigValidationError(
            f"请求体过大（{length} 字节，上限 {MAX_BODY_BYTES} 字节）")
    return handler.rfile.read(length).decode("utf-8")


# ── 配置校验 ──

_ALLOWED_TYPES = frozenset({"rss", "api", "scraper"})


def validate_sources_yaml(text: str) -> list[dict]:
    """校验信源配置：必须是合法 YAML 且含非空 sources 列表，每条含 name/url。

    此前只判断“写成功没有”，一次误保存即可让后续每次运行都在第 1 步崩溃。
    """
    import yaml

    if not text or not text.strip():
        raise ConfigValidationError("内容为空，拒绝写入（这会导致管道第 1 步崩溃）")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigValidationError(f"YAML 语法错误: {e}") from e
    if not isinstance(data, dict):
        raise ConfigValidationError("根节点必须是映射，且包含 sources 字段")
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ConfigValidationError("sources 必须是列表且不能为空")

    seen = set()
    for i, s in enumerate(sources):
        pos = f"第 {i + 1} 条信源"
        if not isinstance(s, dict):
            raise ConfigValidationError(f"{pos}不是映射结构")
        name = s.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ConfigValidationError(f"{pos}缺少 name")
        if name in seen:
            raise ConfigValidationError(f"信源名称重复: {name}")
        seen.add(name)
        url = s.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ConfigValidationError(f"信源「{name}」缺少 url")
        if not url.startswith(("http://", "https://")):
            raise ConfigValidationError(f"信源「{name}」的 url 必须以 http:// 或 https:// 开头")
        stype = s.get("type", "rss")
        if stype not in _ALLOWED_TYPES:
            raise ConfigValidationError(f"信源「{name}」的 type 非法: {stype}")
        lang = s.get("language")
        if lang is not None and (not isinstance(lang, str) or not lang.strip()):
            raise ConfigValidationError(f"信源「{name}」的 language 非法: {lang!r}")
    return sources


# settings.json 允许的字段与其类型（白名单，防止把密钥等任意键写进配置文件）
_ALLOWED_SETTINGS_SPEC = {
    "dedup": {"similarity_threshold": int, "max_days": int,
              "near_dup_hamming": int, "window_hours": int,
              "ledger_ttl_days": int, "near_dup_enabled": bool,
              "ledger_enabled": bool},
    # 抓取限速：同一主机并发上限与请求间隔（改动需重启进程生效）
    "fetch": {"min_host_interval_seconds": (int, float),
              "max_per_host_concurrency": int},
    # 正文抽取兜底开关：关闭后完全走现有自研五层清洗
    "fulltext": {"fallback_enabled": bool},
    # 正文图片本地化：开关、体积/数量/并发限制与本地保留天数
    "images": {"enabled": bool, "max_bytes": int, "max_per_article": int,
               "timeout": (int, float), "concurrency": int,
               "retention_days": int},
    # translate.only_displayed 只翻最终上屏的条目（省额度）；cache_enabled/retry_times/
    # cache_max_entries 控制译文落盘缓存与失败重试（2026-11-10）。
    "translate": {"timeout": (int, float), "fields": list,
                  "provider": str,
                  "only_displayed": bool, "cache_enabled": bool,
                  "retry_times": int, "cache_max_entries": int},
    # 摘要长度（报告「摘要」栏）。short_categories 里的分类用 short_max_chars，
    # 其余用 max_chars；min_chars 低于则退化为"原文节选"。
    "summary": {"max_chars": int, "short_max_chars": int, "min_chars": int,
                "short_categories": list},
    "category_order": list,
    # 日报滚动窗口（2026-11-11 日更改造）：是否把近 N 天已收录条目合并进来、
    # 窗口天数与窗口级单源上限。report.rolling 是二级嵌套，单独用
    # _sanitize_rolling() 校验（见下方调用处），否则会被当作未声明键丢弃。
    "report": {"rolling": dict},
    # 读者画像：展示总量上限（滚动窗口下的首页上限）
    "reader_profile": {
        "max_total_items": int,
    },
    # 质量信号：信源权威、深度、商业噪声、单源配额
    "quality": {
        "authority_multipliers": dict,
        "depth_bonus_max": (int, float),
        "commercial_penalty_max": (int, float),
        "series_penalty": (int, float),
        "source_tiers": dict,
        "quotas": dict,
    },
}

# translate.fields 允许的取值（与 translator.SUPPORTED_FIELDS 保持一致）
_TRANSLATE_FIELDS = ("title", "summary", "ai_summary")


def _sanitize_rolling(rolling: dict) -> dict:
    """校验 report.rolling（二级嵌套，无法用 _ALLOWED_SETTINGS_SPEC 表达）。"""
    if not isinstance(rolling, dict):
        raise ConfigValidationError("report.rolling 必须是对象")
    clean: dict = {}
    for rkey, rtype in (("enabled", bool), ("window_days", int),
                        ("max_per_source", int)):
        if rkey not in rolling:
            continue
        rval = rolling[rkey]
        if rtype is bool:
            if not isinstance(rval, bool):
                raise ConfigValidationError(f"report.rolling.{rkey} 类型不正确")
        elif isinstance(rval, bool) or not isinstance(rval, int):
            raise ConfigValidationError(f"report.rolling.{rkey} 类型不正确")
        clean[rkey] = rval
    for rkey, lo, hi in (("window_days", 1, 90),
                         ("max_per_source", 1, 50)):
        rval = clean.get(rkey)
        if rval is not None and not (lo <= rval <= hi):
            raise ConfigValidationError(
                f"report.rolling.{rkey} 必须在 {lo}-{hi} 之间")
    return clean


def sanitize_settings(raw: dict) -> dict:
    """按白名单裁剪并校验设置项，丢弃未声明的键（含历史遗留的密钥字段）"""
    if not isinstance(raw, dict):
        raise ConfigValidationError("settings 必须是对象")
    out: dict = {}
    for key, spec in _ALLOWED_SETTINGS_SPEC.items():
        if key not in raw:
            continue
        val = raw[key]
        if isinstance(spec, dict):
            if not isinstance(val, dict):
                raise ConfigValidationError(f"{key} 必须是对象")
            sub = {}
            for sk, stype in spec.items():
                if sk not in val:
                    continue
                sv = val[sk]
                # bool 是 int 的子类：声明为 bool 的字段按 bool 校验，
                # 声明为数值的字段则排除 bool。
                if stype is bool:
                    if not isinstance(sv, bool):
                        raise ConfigValidationError(f"{key}.{sk} 类型不正确")
                elif isinstance(sv, bool) or not isinstance(sv, stype):
                    raise ConfigValidationError(f"{key}.{sk} 类型不正确")
                sub[sk] = sv
            out[key] = sub
        else:
            if not isinstance(val, spec) or isinstance(val, bool):
                raise ConfigValidationError(f"{key} 类型不正确")
            if key == "category_order":
                if not all(isinstance(x, str) and x.strip() for x in val):
                    raise ConfigValidationError("category_order 必须是字符串数组")
            out[key] = val

    dedup = out.get("dedup", {})
    thr = dedup.get("similarity_threshold")
    if thr is not None and not (0 <= thr <= 100):
        raise ConfigValidationError("dedup.similarity_threshold 必须在 0-100 之间")
    days = dedup.get("max_days")
    if days is not None and not (1 <= days <= 365):
        raise ConfigValidationError("dedup.max_days 必须在 1-365 之间")
    for dkey, lo, hi in (("near_dup_hamming", 0, 64),
                         ("window_hours", 1, 720),
                         ("ledger_ttl_days", 1, 365)):
        dval = dedup.get(dkey)
        if dval is not None and not (lo <= dval <= hi):
            raise ConfigValidationError(f"dedup.{dkey} 必须在 {lo}-{hi} 之间")

    images_cfg = out.get("images", {})
    for ikey, lo, hi in (("max_bytes", 64 * 1024, 20 * 1024 * 1024),
                         ("max_per_article", 0, 50),
                         ("concurrency", 1, 16),
                         ("retention_days", 1, 365)):
        ival = images_cfg.get(ikey)
        if ival is not None and not (lo <= ival <= hi):
            raise ConfigValidationError(f"images.{ikey} 必须在 {lo}-{hi} 之间")
    itimeout = images_cfg.get("timeout")
    if itimeout is not None and not (1 <= itimeout <= 60):
        raise ConfigValidationError("images.timeout 必须在 1-60 之间")

    fetch_cfg = out.get("fetch", {})
    interval = fetch_cfg.get("min_host_interval_seconds")
    if interval is not None and not (0 <= interval <= 60):
        raise ConfigValidationError("fetch.min_host_interval_seconds 必须在 0-60 之间")
    concurrency = fetch_cfg.get("max_per_host_concurrency")
    if concurrency is not None and not (1 <= concurrency <= 16):
        raise ConfigValidationError("fetch.max_per_host_concurrency 必须在 1-16 之间")

    translate = out.get("translate", {})
    fields = translate.get("fields")
    if fields is not None:
        if not fields:
            raise ConfigValidationError("translate.fields 不能为空数组")
        bad = [f for f in fields if f not in _TRANSLATE_FIELDS]
        if bad:
            raise ConfigValidationError(
                f"translate.fields 含不支持的字段 {bad}，"
                f"可选: {list(_TRANSLATE_FIELDS)}")
    tprovider = translate.get("provider")
    if tprovider is not None and tprovider not in ("tencent", "baidu"):
        raise ConfigValidationError("translate.provider 只能是 tencent 或 baidu")
    tretry = translate.get("retry_times")
    if tretry is not None and not (0 <= tretry <= 5):
        raise ConfigValidationError("translate.retry_times 必须在 0-5 之间")
    tmax = translate.get("cache_max_entries")
    if tmax is not None and not (100 <= tmax <= 200000):
        raise ConfigValidationError("translate.cache_max_entries 必须在 100-200000 之间")

    summary = out.get("summary", {})
    for skey, lo, hi in (("max_chars", 100, 2000),
                         ("short_max_chars", 100, 2000),
                         ("min_chars", 20, 500)):
        sval = summary.get(skey)
        if sval is not None and not (lo <= sval <= hi):
            raise ConfigValidationError(f"summary.{skey} 必须在 {lo}-{hi} 之间")
    short_cats = summary.get("short_categories")
    if short_cats is not None and not all(
            isinstance(x, str) and x.strip() for x in short_cats):
        raise ConfigValidationError("summary.short_categories 必须是字符串数组")

    profile = out.get("reader_profile", {})
    for pkey, lo, hi in (("max_total_items", 1, 1000),):
        pval = profile.get(pkey)
        if pval is not None and not (lo <= pval <= hi):
            raise ConfigValidationError(f"reader_profile.{pkey} 必须在 {lo}-{hi} 之间")

    if "report" in out:
        out["report"]["rolling"] = _sanitize_rolling(out["report"].get("rolling") or {})

    quality = out.get("quality", {})
    for qkey, lo, hi in (("depth_bonus_max", 0, 300),
                         ("commercial_penalty_max", 0, 500),
                         ("series_penalty", 0, 200)):
        qval = quality.get(qkey)
        if qval is not None and not (lo <= qval <= hi):
            raise ConfigValidationError(f"quality.{qkey} 必须在 {lo}-{hi} 之间")
    return out


def validate_scoring_config(cfg: dict) -> None:
    """校验评分关键词配置结构（该文件直接决定报告收录质量）"""
    if not isinstance(cfg, dict):
        raise ConfigValidationError("评分配置必须是对象")
    for tier in ("strong", "medium", "weak"):
        node = cfg.get(tier)
        if not isinstance(node, dict):
            raise ConfigValidationError(f"缺少 {tier} 配置段")
        weight = node.get("weight")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise ConfigValidationError(f"{tier}.weight 必须是数字")
        kws = node.get("keywords")
        if not isinstance(kws, list):
            raise ConfigValidationError(f"{tier}.keywords 必须是数组")
        for i, kw in enumerate(kws):
            if not isinstance(kw, dict):
                raise ConfigValidationError(f"{tier}.keywords[{i}] 必须是对象")
            text = kw.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ConfigValidationError(f"{tier}.keywords[{i}] 缺少 text")
    thresholds = cfg.get("thresholds")
    if not isinstance(thresholds, dict):
        raise ConfigValidationError("缺少 thresholds 配置段")
    accept = thresholds.get("accept_threshold")
    review = thresholds.get("review_threshold")
    for label, val in (("accept_threshold", accept), ("review_threshold", review)):
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ConfigValidationError(f"thresholds.{label} 必须是数字")
        if not (0 <= val <= 100):
            raise ConfigValidationError(f"thresholds.{label} 必须在 0-100 之间")
    if review > accept:
        raise ConfigValidationError(
            f"review_threshold({review}) 不能大于 accept_threshold({accept})")
    min_strong = thresholds.get("min_strong_for_accept")
    if min_strong is not None:
        if isinstance(min_strong, bool) or not isinstance(min_strong, int):
            raise ConfigValidationError("thresholds.min_strong_for_accept 必须是整数")
        if not (0 <= min_strong <= 20):
            raise ConfigValidationError(
                "thresholds.min_strong_for_accept 必须在 0-20 之间（0 表示不启用该门槛）")


def validate_llm_yaml(text: str) -> None:
    import yaml

    if not text or not text.strip():
        raise ConfigValidationError("内容为空，拒绝写入")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigValidationError(f"YAML 语法错误: {e}") from e
    if not isinstance(data, dict):
        raise ConfigValidationError("根节点必须是映射")
    if "enabled" not in data:
        raise ConfigValidationError("缺少 enabled 字段")
    provider = data.get("provider")
    if provider is not None and provider not in ("extractive", "openai", "anthropic", "ollama"):
        raise ConfigValidationError(f"provider 非法: {provider}")
    if data.get("enabled") and provider and provider != "extractive" and not data.get("api_key"):
        raise ConfigValidationError("启用 LLM 时必须填写 api_key，否则管道会静默回退")


# ── 认证配置（从环境变量读取，留空则不启用）──
_CONFIG_USER = os.environ.get("CONFIG_USERNAME", "")
_CONFIG_PASS = os.environ.get("CONFIG_PASSWORD", "")
_AUTH_ENABLED = bool(_CONFIG_USER and _CONFIG_PASS)

# 认证失败记录：ip -> {"fails": int, "locked_until": float}
_auth_failures: dict[str, dict] = {}


def _client_ip(handler) -> str:
    try:
        return handler.client_address[0]
    except Exception:
        return "unknown"


def _auth_locked(ip: str) -> float:
    """返回剩余锁定秒数，0 表示未锁定"""
    rec = _auth_failures.get(ip)
    if not rec:
        return 0.0
    remain = rec.get("locked_until", 0) - time.time()
    return remain if remain > 0 else 0.0


def _record_auth_failure(ip: str):
    rec = _auth_failures.setdefault(ip, {"fails": 0, "locked_until": 0.0})
    rec["fails"] += 1
    if rec["fails"] >= MAX_AUTH_FAILURES:
        rec["locked_until"] = time.time() + AUTH_LOCKOUT_SECONDS
        rec["fails"] = 0
        print(f"[CONFIG] 认证失败次数过多，已锁定来源 {ip} {AUTH_LOCKOUT_SECONDS} 秒")


def _record_auth_success(ip: str):
    _auth_failures.pop(ip, None)


def _send_401(handler, message: str = "认证失败"):
    body = json.dumps({"ok": False, "error": message}, ensure_ascii=False).encode("utf-8")
    handler.send_response(401)
    handler.send_header("WWW-Authenticate", 'Basic realm="Config Server"')
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _send_429(handler, remain: float):
    body = json.dumps(
        {"ok": False, "error": f"认证失败次数过多，请 {int(remain) + 1} 秒后重试"},
        ensure_ascii=False).encode("utf-8")
    handler.send_response(429)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _check_auth(handler) -> bool:
    """验证 HTTP Basic Auth，未通过时返回 401"""
    if not _AUTH_ENABLED:
        return True

    ip = _client_ip(handler)
    remain = _auth_locked(ip)
    if remain > 0:
        _send_429(handler, remain)
        return False

    auth = handler.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        _record_auth_failure(ip)
        _send_401(handler)
        return False
    try:
        decoded = base64.b64decode(auth[len("Basic "):], validate=True).decode("utf-8")
        user, _, pwd = decoded.partition(":")
        # 固定耗时比较，避免通过响应时间推断用户名/密码
        user_ok = hmac.compare_digest(user, _CONFIG_USER)
        pass_ok = hmac.compare_digest(pwd, _CONFIG_PASS)
        if user_ok and pass_ok:
            _record_auth_success(ip)
            return True
    except Exception:
        pass
    _record_auth_failure(ip)
    _send_401(handler)
    return False


# ── Request Handler ──

class ConfigHandler(http.server.SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(_PROJECT_DIR), **kwargs)

    def log_message(self, fmt, *args):
        """收敛访问日志，避免把含凭据的内容打进日志"""
        sys.stderr.write("[CONFIG] %s - %s\n" % (self.address_string(), fmt % args))

    def do_OPTIONS(self):
        # 管理页面与接口同源，无需跨域许可；不再回 Access-Control-Allow-Origin: *
        self.send_response(204)
        self.send_header("Allow", "GET, PUT, POST, DELETE, OPTIONS")
        self.end_headers()

    def _safe_call(self, fn):
        """执行 API 方法，捕获所有异常避免服务器崩溃"""
        try:
            if not _check_auth(self):
                return
            fn()
        except ConfigValidationError as e:
            send_json(self, {"ok": False, "error": str(e)}, 400)
        except OSError:
            pass  # 客户端断开连接
        except Exception as e:
            print(f"[CONFIG ERROR] {self.path}: {e}")
            import traceback
            traceback.print_exc()
            try:
                send_json(self, {"ok": False, "error": f"服务器内部错误: {e}"}, 500)
            except OSError:
                pass  # 客户端已断开，忽略

    # ── 静态文件白名单 ──

    @staticmethod
    def _static_allowed(rel_posix: str) -> bool:
        if rel_posix in _STATIC_ALLOW_EXACT:
            return True
        return any(rel_posix.startswith(p) for p in _STATIC_ALLOW_PREFIXES)

    def _resolve_request_path(self) -> str | None:
        """把请求路径解析为项目内的相对 posix 路径；不在项目内返回 None"""
        parsed = urllib.parse.urlparse(self.path)
        try:
            abs_path = Path(self.translate_path(parsed.path)).resolve()
        except Exception:
            return None
        try:
            rel = abs_path.relative_to(Path(_PROJECT_DIR).resolve())
        except ValueError:
            return None
        return rel.as_posix()

    def _guard_static(self) -> bool:
        """白名单校验 + 禁止目录列举；通过返回 True"""
        rel = self._resolve_request_path()
        if rel is None or not self._static_allowed(rel):
            self.send_error(403, "Forbidden")
            return False
        abs_path = (Path(_PROJECT_DIR) / rel)
        if abs_path.is_dir():
            # 不提供目录列举（会暴露周报文件名与目录结构）
            self.send_error(403, "Forbidden")
            return False
        return True

    # ── Gzip 压缩 ──
    _COMPRESS_TYPES = frozenset([
        "text/html", "text/css", "text/javascript", "application/javascript",
        "application/json", "text/plain", "application/xml",
    ])

    @staticmethod
    def _is_report_path(path: Path) -> bool:
        """判断是否为周报文件，用于添加缓存头"""
        name = path.name
        return name.startswith("Security_Reports") or name.startswith("data_")

    def _gz_path(self, path: Path) -> Path | None:
        """如果存在预压缩文件且客户端接受 gzip，返回 .gz 路径"""
        gz = path.with_name(path.name + ".gz")
        if gz.exists() and "gzip" in self.headers.get("Accept-Encoding", ""):
            # 只认比原文件新的 .gz：旧副本会让浏览器永远拿到上一版报告
            try:
                if gz.stat().st_mtime >= path.stat().st_mtime:
                    return gz
            except OSError:
                return None
        return None

    def _serve_file(self, file_path: Path, ctype: str = None):
        """直接返回文件内容（优先预压缩，回退实时压缩或原始）"""
        if not file_path.exists():
            return False
        if ctype is None:
            ext = file_path.suffix.lower()
            ctype = {
                ".html": "text/html; charset=utf-8",
                ".json": "application/json; charset=utf-8",
            }.get(ext, "text/html; charset=utf-8")

        # 优先返回预压缩文件
        pre_gz = self._gz_path(file_path)
        if pre_gz is not None:
            data = pre_gz.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Vary", "Accept-Encoding")
            if self._is_report_path(file_path):
                self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(data)
            return True

        # 回退：读取原始文件
        accept_gzip = "gzip" in self.headers.get("Accept-Encoding", "")
        try:
            raw = file_path.read_bytes()
        except OSError:
            return False

        if accept_gzip and len(raw) > 512:
            buf = BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=1) as gz:
                gz.write(raw)
            compressed = buf.getvalue()
            if len(compressed) < len(raw):
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(compressed)))
                self.send_header("Vary", "Accept-Encoding")
                if self._is_report_path(file_path):
                    self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(compressed)
                return True

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        if self._is_report_path(file_path):
            self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(raw)
        return True

    # ── UA 检测 ──
    @staticmethod
    def _is_mobile_ua(ua: str) -> bool:
        """检查 User-Agent 是否为移动端"""
        keywords = ("mobile", "android", "iphone", "ipad", "phone",
                    "ipod", "opera mini", "blackberry", "webos", "iemobile")
        return any(k in ua.lower() for k in keywords)

    def send_head(self):
        if not self._guard_static():
            return None
        path = self.translate_path(self.path)
        # 只对实际存在的文件启用压缩
        if os.path.isfile(path):
            ctype = self.guess_type(path)
            accept_encoding = self.headers.get("Accept-Encoding", "")
            if ctype in self._COMPRESS_TYPES and "gzip" in accept_encoding:
                return self._send_compressed(path, ctype)
        return super().send_head()

    def do_HEAD(self):
        """HEAD 与 GET 走同一套认证与白名单校验"""
        self._safe_call(lambda: self.send_head())

    def _send_compressed(self, path: str, ctype: str):
        file_path = Path(path)
        cache_header = "public, max-age=3600" if self._is_report_path(file_path) else "no-cache"
        # 优先返回预压缩文件
        pre_gz = self._gz_path(file_path)
        if pre_gz is not None:
            data = pre_gz.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype + "; charset=utf-8" if "text/html" in ctype else ctype)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache_header)
            self.send_header("Vary", "Accept-Encoding")
            self.end_headers()
            return BytesIO(data)

        try:
            with open(path, "rb") as f:
                raw = f.read()
            buf = BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=1) as gz:
                gz.write(raw)
            compressed = buf.getvalue()
        except OSError:
            return super().send_head()

        # 如果压缩后反而更大，不压缩
        if len(compressed) >= len(raw):
            return super().send_head()

        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8" if ctype == "text/html" else ctype)
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(compressed)))
        self.send_header("Cache-Control", cache_header)
        self.send_header("Vary", "Accept-Encoding")
        self.end_headers()
        return BytesIO(compressed)

    def do_GET(self):
        self._safe_call(self._do_get_impl)

    def do_PUT(self):
        self._safe_call(self._do_put_impl)

    def do_POST(self):
        self._safe_call(self._do_post_impl)

    def do_DELETE(self):
        self._safe_call(self._do_delete_impl)

    def _do_get_impl(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        # 根路径：根据 UA 直接返回对应版本
        if path == "" or path == "/":
            ua = self.headers.get("User-Agent", "")
            is_mobile = self._is_mobile_ua(ua)
            report_path = (_PROJECT_DIR / "reports" / "Security_Reports_mobile.html"
                           if is_mobile else _PROJECT_DIR / "reports" / "Security_Reports.html")
            if report_path.exists():
                self._serve_file(report_path)
                return
            # 回退到桌面版
            desktop = _PROJECT_DIR / "reports" / "Security_Reports.html"
            if desktop.exists():
                self._serve_file(desktop)
                return
            self.send_response(302)
            self.send_header("Location", "/server/config.html")
            self.end_headers()
            return

        # /bookmarks 快捷路径（收藏页）
        if path == "/bookmarks":
            bookmarks = _PROJECT_DIR / "server" / "bookmarks.html"
            if not self._serve_file(bookmarks):
                self.send_error(404, "Not Found")
            return

        # /config.html 快捷路径
        if path == "/config.html":
            self.send_response(302)
            self.send_header("Location", "/server/config.html")
            self.end_headers()
            return

        # UA 检测：直接返回对应版本内容（不重定向）
        if path in ("/reports/Security_Reports.html", "/reports/Security_Reports_mobile.html"):
            ua = self.headers.get("User-Agent", "")
            want_mobile = self._is_mobile_ua(ua)
            is_on_mobile = path.endswith("_mobile.html")
            if want_mobile != is_on_mobile:
                correct = (_PROJECT_DIR / "reports" / "Security_Reports_mobile.html"
                           if want_mobile else _PROJECT_DIR / "reports" / "Security_Reports.html")
                if correct.exists():
                    self._serve_file(correct)
                    return
            # UA 匹配 → 正常走静态文件服务

        # ── API routes ──
        if path == "/api/config/sources":
            return self._get_sources()
        elif path == "/api/config/settings":
            return self._get_settings()
        elif path == "/api/config/llm":
            return self._get_llm()
        elif path == "/api/pipeline/status":
            return self._pipeline_status()
        elif path == "/api/config/category_order":
            return self._get_category_order()
        elif path == "/api/config/source_status":
            return self._get_source_status()
        elif path == "/api/config/security_keywords":
            return self._get_security_keywords()
        elif path == "/api/config/scoring_keywords":
            return self._get_scoring_keywords()
        elif path == "/api/config/scoring_keywords/save":
            return self._put_scoring_keywords()

        # ── 选文标注 API（只读列表 + 导出；写接口在 POST）──
        if path == "/api/annotations":
            return self._get_annotations()
        if path == "/api/annotations/export":
            return self._export_annotations(parsed)

        # ── 收藏 API ──
        if path == "/api/favorites":
            return self._get_favorites()
        if path == "/api/favorites/export":
            return self._export_favorites(parsed)
        if path.startswith("/api/favorites/assets/"):
            return self._get_favorite_asset(path)
        favorite_id = _favorite_id_from_path(path)
        if favorite_id:
            return self._get_favorite(favorite_id)

        # ── favicon.ico ──
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        # ── Static files（白名单校验在 send_head 中执行）──
        return super().do_GET()

    def _do_put_impl(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/config/sources":
            return self._put_sources()
        elif path == "/api/config/settings":
            return self._put_settings()
        elif path == "/api/config/llm":
            return self._put_llm()
        elif path == "/api/config/category_order":
            return self._put_category_order()
        elif path == "/api/config/security_keywords":
            return self._put_security_keywords()
        elif path == "/api/config/scoring_keywords":
            return self._put_scoring_keywords()

        favorite_id = _favorite_id_from_path(path)
        if favorite_id:
            return self._put_favorite(favorite_id)

        send_json(self, {"error": "Not found"}, 404)

    def _do_post_impl(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/pipeline/run":
            return self._run_pipeline()
        if path == "/api/favorites":
            return self._post_favorite()
        if path == "/api/annotations":
            return self._post_annotation()

        send_json(self, {"error": "Not found"}, 404)

    def _do_delete_impl(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        favorite_id = _favorite_id_from_path(path)
        if favorite_id:
            return self._delete_favorite(favorite_id)

        send_json(self, {"error": "Not found"}, 404)

    # ── API Implementations ──

    def _get_sources(self):
        text = read_yaml(_SOURCES_PATH)
        send_json(self, {"ok": True, "yaml": text})

    def _put_sources(self):
        data = json.loads(read_body(self))
        text = data.get("yaml", "")
        sources = validate_sources_yaml(text)   # 校验不通过会抛 ConfigValidationError
        ok = write_yaml(_SOURCES_PATH, text)
        send_json(self, {"ok": ok, "count": len(sources)})

    def _get_settings(self):
        cfg = read_json(_SETTINGS_PATH)
        send_json(self, {"ok": True, "settings": cfg})

    def _put_settings(self):
        data = json.loads(read_body(self))
        settings = sanitize_settings(data.get("settings", {}))
        ok = write_json(_SETTINGS_PATH, settings)
        send_json(self, {"ok": ok, "settings": settings})

    def _get_llm(self):
        text = read_yaml(_LLM_PATH)
        send_json(self, {"ok": True, "yaml": text})

    def _put_llm(self):
        data = json.loads(read_body(self))
        text = data.get("yaml", "")
        validate_llm_yaml(text)
        ok = write_yaml(_LLM_PATH, text)
        send_json(self, {"ok": ok})

    def _get_category_order(self):
        cfg = read_json(_SETTINGS_PATH)
        order = cfg.get("category_order", [])
        send_json(self, {"ok": True, "order": order})

    def _put_category_order(self):
        data = json.loads(read_body(self))
        order = data.get("order", [])
        if not isinstance(order, list) or not all(
                isinstance(x, str) and x.strip() for x in order):
            raise ConfigValidationError("order 必须是非空字符串数组")
        cfg = read_json(_SETTINGS_PATH)
        cfg["category_order"] = order
        ok = write_json(_SETTINGS_PATH, sanitize_settings(cfg))
        send_json(self, {"ok": ok})

    def _get_source_status(self):
        status_path = _DATA_DIR / "fetch_status.json"
        status = read_json(status_path)
        send_json(self, {"ok": True, "status": status})

    def _get_security_keywords(self):
        """读取关键字列表"""
        from pipeline.steps.keyword_filter import load_keywords, init_default_keywords
        init_default_keywords()
        keywords = load_keywords()
        send_json(self, {"ok": True, "keywords": keywords})

    def _put_security_keywords(self):
        """替换关键字列表"""
        data = json.loads(read_body(self))
        from pipeline.steps.keyword_filter import save_keywords, DEFAULT_KEYWORDS, load_keywords
        kws = data.get("keywords", [])
        if not isinstance(kws, list):
            raise ConfigValidationError("keywords 必须是数组")
        if not kws:
            # 空列表 = 恢复默认
            ok = save_keywords(DEFAULT_KEYWORDS)
        else:
            ok = save_keywords(kws)
        if not ok:
            raise ConfigValidationError("保存失败：评分关键词配置结构不完整，已放弃写入")
        send_json(self, {"ok": ok, "keywords": load_keywords()})

    def _get_scoring_keywords(self):
        """读取评分关键词完整配置"""
        cfg = read_json(_SCORING_KEYWORDS_PATH)
        if not cfg:
            send_json(self, {"ok": False, "error": "评分配置文件不存在"})
            return
        send_json(self, {"ok": True, "config": cfg})

    def _put_scoring_keywords(self):
        """保存评分关键词完整配置（写入前校验结构，避免改坏收录质量）"""
        data = json.loads(read_body(self))
        cfg = data.get("config", {})
        validate_scoring_config(cfg)
        ok = write_json(_SCORING_KEYWORDS_PATH, cfg)
        send_json(self, {"ok": ok})

    def _pipeline_status(self):
        global _pipeline_proc
        running = _pipeline_proc is not None and _pipeline_proc.poll() is None
        log_text = ""
        try:
            if _PIPELINE_LOG_PATH.exists():
                log_text = _PIPELINE_LOG_PATH.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
        # 只回传尾部，避免长时间运行后日志无限增长
        lines = log_text.strip().split("\n")[-500:]
        send_json(self, {
            "ok": True,
            "running": running,
            "log": lines,
        })

    def _run_pipeline(self):
        global _pipeline_proc
        if _pipeline_proc is not None and _pipeline_proc.poll() is None:
            send_json(self, {"ok": False, "error": "管道正在运行中"}, 409)
            return

        # 清空并初始化日志文件
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            _atomic_text(_PIPELINE_LOG_PATH,
                         f"[{datetime.now().isoformat()}] 管道启动...\n")
        except Exception as e:
            send_json(self, {"ok": False, "error": f"日志初始化失败: {e}"}, 500)
            return

        proc = subprocess.Popen(
            [sys.executable, "-u", "app.py", "--run"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd=str(_PROJECT_DIR),
        )
        _pipeline_proc = proc

        def drain():
            """将子进程 stdout 实时写入日志文件"""
            try:
                with open(_PIPELINE_LOG_PATH, "a", encoding="utf-8") as log_f:
                    for line in iter(proc.stdout.readline, ""):
                        log_f.write(line)
                        log_f.flush()
                    proc.wait()
                    log_f.write(f"[{datetime.now().isoformat()}] 退出码: {proc.returncode}\n")
            except Exception as e:
                try:
                    with open(_PIPELINE_LOG_PATH, "a", encoding="utf-8") as log_f:
                        log_f.write(f"[{datetime.now().isoformat()}] 管道错误: {e}\n")
                except OSError:
                    pass
            finally:
                try:
                    proc.stdout.close()
                except Exception:
                    pass

        import threading
        t = threading.Thread(target=drain, daemon=True)
        t.start()

        send_json(self, {"ok": True, "message": "管道已启动"})

    # ── 收藏 API ──

    def _require_same_origin_write(self) -> bool:
        """收藏的写接口要求同源 + 自定义头，阻断跨站伪造请求"""
        if self.headers.get("X-Requested-With") != "XMLHttpRequest":
            send_json(self, {"ok": False, "error": "缺少同源请求标识"}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin:
            try:
                origin_host = urllib.parse.urlsplit(origin).netloc
            except ValueError:
                origin_host = ""
            if origin_host != self.headers.get("Host", ""):
                send_json(self, {"ok": False, "error": "拒绝跨站请求"}, 403)
                return False
        return True

    @staticmethod
    def _read_json_body(handler) -> dict:
        raw = read_body(handler)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError as e:
            raise ConfigValidationError(f"JSON 格式错误: {e}") from e
        if not isinstance(data, dict):
            raise ConfigValidationError("请求体必须是 JSON 对象")
        return data

    def _get_favorites(self):
        items, tags = _FAVORITES.list_meta()
        send_json(self, {"ok": True, "total": len(items), "items": items, "tags": tags})

    def _get_favorite(self, favorite_id: str):
        record = _FAVORITES.get(favorite_id)
        if record is None:
            send_json(self, {"ok": False, "error": "收藏不存在"}, 404)
            return
        send_json(self, {"ok": True, "item": record})

    def _post_favorite(self):
        if not self._require_same_origin_write():
            return
        payload = self._read_json_body(self)
        item = payload.get("item")
        if not isinstance(item, dict):
            raise ConfigValidationError("缺少 item 字段")
        try:
            record, created = _FAVORITES.save(item)
        except ValueError as e:
            raise ConfigValidationError(str(e)) from e
        send_json(self, {"ok": True, "created": created,
                         "item": _FAVORITES.meta(record)}, 201 if created else 200)

    def _put_favorite(self, favorite_id: str):
        if not self._require_same_origin_write():
            return
        payload = self._read_json_body(self)
        tags = payload.get("tags") if "tags" in payload else None
        note = payload.get("note") if "note" in payload else None
        if tags is not None and not isinstance(tags, list):
            raise ConfigValidationError("tags 必须是数组")
        if note is not None and not isinstance(note, str):
            raise ConfigValidationError("note 必须是字符串")
        try:
            record = _FAVORITES.update(favorite_id, tags=tags, note=note)
        except KeyError:
            send_json(self, {"ok": False, "error": "收藏不存在"}, 404)
            return
        send_json(self, {"ok": True, "item": _FAVORITES.meta(record)})

    def _delete_favorite(self, favorite_id: str):
        if not self._require_same_origin_write():
            return
        if not _FAVORITES.delete(favorite_id):
            send_json(self, {"ok": False, "error": "收藏不存在"}, 404)
            return
        send_json(self, {"ok": True, "removed": True})

    # ── 选文标注 API ──

    @staticmethod
    def _annotator_name() -> str:
        """标注人：有认证时用登录名，本地未启用认证时记为 local。"""
        return _CONFIG_USER if (_AUTH_ENABLED and _CONFIG_USER) else "local"

    def _get_annotations(self):
        items = _ANNOTATIONS.latest()
        send_json(self, {
            "ok": True,
            "annotator": self._annotator_name(),
            "reasons": _ANNOTATIONS.reasons(),
            "today": _ANNOTATIONS.today_counts(),
            "total": len(items),
            "items": items,
        })

    def _post_annotation(self):
        if not self._require_same_origin_write():
            return
        payload = self._read_json_body(self)
        try:
            record = _ANNOTATIONS.append(payload, annotator=self._annotator_name())
        except ValueError as e:
            raise ConfigValidationError(str(e)) from e
        send_json(self, {"ok": True, "record": record,
                         "today": _ANNOTATIONS.today_counts()})

    def _export_annotations(self, parsed):
        query = urllib.parse.parse_qs(parsed.query)
        month = (query.get("month") or [""])[0].strip()
        try:
            body = _ANNOTATIONS.export_jsonl(month or None).encode("utf-8")
        except ValueError as e:
            raise ConfigValidationError(str(e)) from e
        stamp = month or datetime.now().strftime("%Y-%m")
        filename = f"annotations_{stamp}.jsonl"
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def _get_favorite_asset(self, path: str):
        raw = path[len("/api/favorites/assets/"):]
        parts = raw.split("/")
        if len(parts) != 2:
            self.send_error(404, "Not Found")
            return
        favorite_id, filename = urllib.parse.unquote(parts[0]), urllib.parse.unquote(parts[1])
        try:
            asset = _FAVORITES.asset_path(favorite_id, filename)
        except ValueError:
            self.send_error(404, "Not Found")
            return
        if not asset.is_file():
            self.send_error(404, "Not Found")
            return
        try:
            data = asset.read_bytes()
        except OSError:
            self.send_error(404, "Not Found")
            return
        self.send_response(200)
        self.send_header("Content-Type", _FAVORITES.asset_content_type(filename))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=31536000")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(data)
        except OSError:
            pass

    def _export_favorites(self, parsed):
        query = urllib.parse.parse_qs(parsed.query)
        fmt = (query.get("format") or ["md"])[0].lower()
        stamp = datetime.now().strftime("%Y%m%d")
        if fmt == "json":
            body = _FAVORITES.export_json().encode("utf-8")
            ctype = "application/json; charset=utf-8"
            filename = f"favorites_{stamp}.json"
        elif fmt in ("md", "markdown"):
            body = _FAVORITES.export_markdown().encode("utf-8")
            ctype = "text/markdown; charset=utf-8"
            filename = f"favorites_{stamp}.md"
        else:
            send_json(self, {"ok": False, "error": "不支持的导出格式"}, 400)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass


class ConfigHTTPServer(http.server.ThreadingHTTPServer):
    # 客户端异常断开时不让处理线程堆积（原代码把 socket accept 超时误当作防线程残留）
    daemon_threads = True
    allow_reuse_address = True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="安全管理后台配置服务器")
    parser.add_argument("--project-dir", default=None,
                        help="项目根目录路径（默认自动检测）")
    parser.add_argument("--host", default="0.0.0.0",
                        help="监听地址（默认 0.0.0.0；置于反向代理后建议 127.0.0.1）")
    parser.add_argument("port", nargs="?", default=8090, type=int,
                        help="监听端口（默认 8090）")
    args = parser.parse_args()

    _init_paths(args.project_dir)
    port = args.port

    # 显式从项目目录加载 .env（进程启动时的 cwd 可能不是项目根）
    try:
        from dotenv import load_dotenv
        load_dotenv(_PROJECT_DIR / ".env", override=False)
    except ImportError:
        pass
    global _CONFIG_USER, _CONFIG_PASS, _AUTH_ENABLED
    _CONFIG_USER = os.environ.get("CONFIG_USERNAME", "")
    _CONFIG_PASS = os.environ.get("CONFIG_PASSWORD", "")
    _AUTH_ENABLED = bool(_CONFIG_USER and _CONFIG_PASS)

    if _AUTH_ENABLED:
        print(f"[CONFIG] 认证已启用（用户: {_CONFIG_USER}）")
    else:
        print("[CONFIG] 警告: 认证未启用，请设置 CONFIG_USERNAME/CONFIG_PASSWORD 环境变量")
        print("[CONFIG] 当前配置: 任何可访问此端口的人均可完全控制配置")
        print("[CONFIG] 静态文件已限制为白名单（管理页面 + 周报产物），密钥与源码不可直接下载")

    if _PROJECT_DIR != SERVER_DIR.parent:
        print(f"[CONFIG] 注意: 项目目录被指定为 {_PROJECT_DIR}")

    server = ConfigHTTPServer((args.host, port), ConfigHandler)
    print(f"[CONFIG] 监听地址: {args.host}:{port}")
    print(f"[CONFIG] 管理后台: http://localhost:{port}/config.html")
    print(f"[CONFIG] 项目目录: {_PROJECT_DIR}")
    print(f"[CONFIG] API:       http://localhost:{port}/api/config/sources")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[CONFIG] 已停止")
        server.server_close()


if __name__ == "__main__":
    main()
