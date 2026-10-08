"""管道工具函数 — 路径解析、原子写入、预压缩、密钥加载、安全 URL 校验

路径约定（2026-09-29 修复）：
    本模块内所有目录常量均由 PROJECT_ROOT 推导为**绝对路径**，不再依赖
    当前工作目录。此前各步骤模块大量使用 Path("data") 之类的相对路径，
    一旦从其他目录启动进程，数据会写到错误位置甚至在其他盘符根目录下
    创建 data/、config/ 目录。
"""

import gzip
import ipaddress
import json
import os
import socket
import tempfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

# pipeline/utils/__init__.py -> pipeline/utils -> pipeline -> 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = PROJECT_ROOT / "data"
CONFIG_DIR = PROJECT_ROOT / "config"
REPORTS_DIR = PROJECT_ROOT / "reports"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
ASSETS_DIR = PROJECT_ROOT / "pipeline" / "assets"

SECRETS_PATH = CONFIG_DIR / "secrets.json"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
SOURCES_PATH = CONFIG_DIR / "source_config.yaml"
SCORING_CONFIG_PATH = CONFIG_DIR / "scoring_keywords.json"
LLM_CONFIG_PATH = CONFIG_DIR / "llm_config.yaml"


def load_secrets() -> dict:
    """从 config/secrets.json 加载 API 密钥，文件不存在则返回空字典"""
    if SECRETS_PATH.exists():
        try:
            with open(SECRETS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def atomic_write(path: str | Path, data: Any, **json_kwargs):
    """原子写入 JSON 文件：先写临时文件，再 rename 覆盖目标路径。

    管道各阶段共享中间数据文件，普通写入若在写入中途崩溃会导致
    JSON 截断/损坏。本函数确保写入要么完全成功，要么完全不改变目标文件。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, **json_kwargs)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: str | Path, text: str, encoding: str = "utf-8"):
    """原子写入文本文件（配置项保存走这条路径，避免半截文件）"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_bytes(path: str | Path, data: bytes):
    """原子写入二进制文件"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def precompress(path: Path, level: int = 6, remove_original: bool = False) -> Path | None:
    """为文件生成预压缩 .gz 版本，返回压缩文件路径（压缩后更大则跳过）"""
    if not path.exists():
        return None
    gz_path = path.with_name(path.name + ".gz")
    raw = path.read_bytes()
    compressed = gzip.compress(raw, compresslevel=level)
    if len(compressed) >= len(raw):
        return None
    atomic_write_bytes(gz_path, compressed)
    if remove_original:
        path.unlink()
    return gz_path


# ── 时间解析（统一时区） ──────────────────────────────────────────

def parse_datetime_utc(value) -> datetime | None:
    """把各种来源的时间值解析为 **UTC 的无时区 datetime**，失败返回 None。

    存在的意义：修复“带时区的时间戳与无时区截止时间比较会抛异常、
    被 except 吞掉后导致过期过滤整体失效”的缺陷。所有时间在进入比较
    之前都先归一到 UTC 无时区表示，任何来源的格式都不会再绕过过滤。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        dt = None
        # 支持 2026-06-22T10:00:00Z / +08:00 / 无时区 等 ISO 形式
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00"))
        except ValueError:
            dt = None
        if dt is None:
            # 回退 RFC 822（RSS 常见：Mon, 22 Jun 2026 10:00:00 +0800）
            try:
                dt = parsedate_to_datetime(s)
            except (TypeError, ValueError):
                dt = None
        if dt is None:
            # 回退纯日期 2026-06-22
            try:
                dt = datetime.strptime(s[:10], "%Y-%m-%d")
            except ValueError:
                return None
    else:
        return None

    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def utc_now_naive() -> datetime:
    """当前 UTC 时间（无时区），与 parse_datetime_utc 的返回值可直接比较"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── 对外抓取的安全校验（防 SSRF） ──────────────────────────────────

ALLOWED_SCHEMES = frozenset({"http", "https"})
DEFAULT_MAX_BYTES = 5 * 1024 * 1024          # 单次响应体积上限 5MB
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_TIMEOUT = 15.0


class UnsafeURLError(ValueError):
    """URL 未通过安全校验（协议不允许、指向内网/本机、无法解析等）"""


def _ip_is_internal(ip) -> bool:
    """判断 IP 是否属于不允许访问的范围（内网/回环/链路本地/保留段等）"""
    # is_global 对私有网段、回环、链路本地、保留、组播、未指定地址均返回 False；
    # 同时覆盖 IPv4 与 IPv6（含 ::ffff: 形式的 IPv4 映射地址）。
    return not ip.is_global


def validate_public_url(url: str) -> str:
    """校验 URL 可以安全地对外抓取，返回规范化后的 URL。

    拒绝：
      - 非 http/https 协议（file://、gopher://、data: 等）
      - 缺少主机名
      - 主机是内网/回环/链路本地/保留地址（含 IPv4 字面量与域名解析结果）
      - URL 中携带用户名密码

    说明：此处对 DNS 解析结果做一次性校验，仍存在“解析后重绑定”的理论
    时间窗。对周报抓取场景，该风险可接受；如需彻底消除，需在连接层固定 IP。
    """
    if not url or not isinstance(url, str):
        raise UnsafeURLError("空 URL")

    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"协议不允许: {parsed.scheme or '(空)'}")
    if parsed.username or parsed.password:
        raise UnsafeURLError("URL 不允许携带凭据")

    host = parsed.hostname
    if not host:
        raise UnsafeURLError("缺少主机名")

    # 主机是 IP 字面量
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        if _ip_is_internal(literal):
            raise UnsafeURLError(f"目标是内网/保留地址: {host}")
        return url.strip()

    # 域名：解析后逐个校验
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise UnsafeURLError(f"域名解析失败 {host}: {e}") from e

    if not infos:
        raise UnsafeURLError(f"域名无解析结果: {host}")

    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr.split("%")[0])
        except ValueError:
            continue
        if _ip_is_internal(ip):
            raise UnsafeURLError(f"域名 {host} 解析到内网/保留地址 {addr}")

    return url.strip()


class SafeResponse:
    """safe_get 的结果对象（响应流已关闭，只保留必要字段）"""

    __slots__ = ("status_code", "headers", "text", "url", "truncated")

    def __init__(self, status_code: int, headers: dict, text: str, url: str, truncated: bool):
        self.status_code = status_code
        self.headers = headers
        self.text = text
        self.url = url
        self.truncated = truncated

    def header(self, name: str, default: str = "") -> str:
        for k, v in self.headers.items():
            if k.lower() == name.lower():
                return v
        return default


def _decode_body(body: bytes, content_type: str) -> str:
    """按响应声明的字符集解码，失败则回退 utf-8 替换"""
    charset = ""
    for part in content_type.split(";")[1:]:
        part = part.strip()
        if part.lower().startswith("charset="):
            charset = part.split("=", 1)[1].strip().strip('"\'')
            break
    for enc in ([charset] if charset else []) + ["utf-8", "gb18030", "latin-1"]:
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def safe_get(url: str, *, headers: dict | None = None, timeout: float = DEFAULT_TIMEOUT,
             max_bytes: int = DEFAULT_MAX_BYTES, max_redirects: int = DEFAULT_MAX_REDIRECTS) -> SafeResponse:
    """带 SSRF 防护的 GET 请求。

    - 起始 URL 与**每一次重定向目标**都经 validate_public_url() 校验，
      防止通过跳转绕过内网限制
    - 响应体按 max_bytes 边读边截断，不会把超大响应整体读入内存
    """
    import httpx

    current = validate_public_url(url)
    headers = headers or {}

    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        for _ in range(max_redirects + 1):
            with client.stream("GET", current, headers=headers) as resp:
                if resp.is_redirect and resp.headers.get("location"):
                    current = validate_public_url(urljoin(current, resp.headers["location"]))
                    continue

                resp.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                truncated = False
                for chunk in resp.iter_bytes():
                    room = max_bytes - total
                    if len(chunk) >= room:
                        chunks.append(chunk[:room])
                        truncated = len(chunk) > room
                        break
                    chunks.append(chunk)
                    total += len(chunk)

                body = b"".join(chunks)
                content_type = resp.headers.get("content-type", "")
                return SafeResponse(
                    status_code=resp.status_code,
                    headers=dict(resp.headers),
                    text=_decode_body(body, content_type),
                    url=current,
                    truncated=truncated,
                )

        raise UnsafeURLError(f"重定向次数超过上限 {max_redirects}: {url}")
