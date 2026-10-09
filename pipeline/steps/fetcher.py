"""信源抓取 — 并发抓取 RSS / API 信源

2026-09-29 修复：
  1. **自动禁用变成永久禁用**：信源连续失败达到上限后被移出待抓取列表，
     而“失败计数归零”只发生在抓取成功时——于是它永远不再被尝试，失败计数
     还每轮 +1（报出的“连续 N 次失败”是虚高的）。现在改为**指数退避重试**：
     连续失败后按 1→2→4→8→16 轮（上限）的间隔重试，成功即恢复；
     退避跳过不再计入失败次数。
  2. **API 密钥从未生效**：SCHOLAR_API_KEY / GITHUB_TOKEN 已加载，但
     `api_platform` 只在 9 个信源上配置过，语义学者、GitHub Trending 等
     信源走的是“无平台→基本 GET”分支，密钥根本没带上。现在按信源名回补
     平台映射，使密钥与专用请求头真正生效。
  3. **信源地址未校验协议**：配置可经管理后台修改，现在只接受 http/https。
  4. 相对路径全部改为项目绝对路径；单信源响应体积设上限。
"""

import asyncio
import json
import os
import random
from datetime import datetime

import httpx
import yaml

from ..utils import DATA_DIR, SOURCES_PATH, atomic_write, load_secrets

RAW_ITEMS_PATH = DATA_DIR / "raw_items.json"
SOURCE_HEALTH_PATH = DATA_DIR / "source_health.json"

# 连续失败达到此值后进入退避重试（不再永久禁用）
MAX_CONSECUTIVE_FAILURES = 5
# 退避间隔上限（轮）
MAX_COOLDOWN_ROUNDS = 16
# 健康记录里存放运行轮次的保留键
HEALTH_META_KEY = "__meta__"
# 单信源响应体积上限（防止单个超大信源把内存与 raw_items.json 撑爆）
MAX_SOURCE_BYTES = 8 * 1024 * 1024

# User-Agents to rotate between for bypassing RSS blocks
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]


# 失败重试用的 UA：**只放真正的 RSS 阅读器**。
# 踩过的坑（2026-10-09 实测）：这里原先第 0 个写的是浏览器 UA（与 USER_AGENTS[0] 重复），
# 而部分站点恰恰"封浏览器 UA、放行阅读器 UA"（Aqua / Cynet / Jack Clark /
# Microsoft AI Blog / OWASP LLM Top 10 / Qualys 实测：浏览器 UA 403、阅读器 UA 200）。
# 旧代码只有一个浏览器 UA，恰好等于列表第 0 个，重试时能落到真正的阅读器；
# 一旦给浏览器 UA 加了第二个（随机轮换），约一半概率重试又会取回浏览器 UA → 信源丢失。
# 因此这里只允许出现阅读器 UA，重试按顺序逐个尝试。
RSS_READER_UAS = [
    "NetNewsWire/6.1 (Mac; Intel Mac OS X 14.4)",
    "FeedFetcher-Google; (+http://www.google.com/feedfetcher.html)",
]
# 重试时统一带上的 Accept（有些站点按 Accept 判断是不是订阅客户端）
_RSS_ACCEPT = "application/rss+xml, application/atom+xml, application/xml, text/xml"

# 信源名 → API 平台（配置里未写 api_platform 时回补，使专用请求头/密钥生效）
SOURCE_NAME_PLATFORMS = {
    "安全内参": "secrss",
    "arXiv cs.CR": "arxiv",
    "Semantic Scholar": "semantic_scholar",
    "IETF Datatracker": "ietf",
    "MITRE ATT&CK": "mitre_attack",
    "GitHub Security Trending": "github",
}

# API Keys（从 secrets.json 读取，回退到环境变量）
_secrets = load_secrets()
SCHOLAR_API_KEY = _secrets.get("scholar_api_key") or os.environ.get("SCHOLAR_API_KEY", "")
GITHUB_TOKEN = _secrets.get("github_token") or os.environ.get("GITHUB_TOKEN", "")


# ── 信源健康追踪 ──

def _load_health() -> dict:
    """读取信源健康记录"""
    if SOURCE_HEALTH_PATH.exists():
        try:
            with open(SOURCE_HEALTH_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def _save_health(health: dict):
    """写入信源健康记录"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(SOURCE_HEALTH_PATH, health, indent=2)


def _current_round(health: dict) -> int:
    meta = health.get(HEALTH_META_KEY) or {}
    try:
        return int(meta.get("round", 0))
    except (TypeError, ValueError):
        return 0


def _next_round(health: dict) -> int:
    round_no = _current_round(health) + 1
    health.setdefault(HEALTH_META_KEY, {})["round"] = round_no
    health[HEALTH_META_KEY]["updated_at"] = datetime.now().isoformat()
    return round_no


def _backoff_rounds(fails: int) -> int:
    """连续失败次数 → 退避轮数（1,2,4,8,...,MAX_COOLDOWN_ROUNDS）"""
    over = max(0, fails - MAX_CONSECUTIVE_FAILURES)
    return min(2 ** over, MAX_COOLDOWN_ROUNDS)


def _should_backoff(health: dict, name: str, round_no: int) -> tuple[bool, str]:
    """判断信源本轮是否应跳过（退避中）"""
    h = health.get(name) or {}
    fails = h.get("consecutive_failures", 0)
    if fails < MAX_CONSECUTIVE_FAILURES:
        return False, ""
    until = h.get("skip_until_round", 0)
    try:
        until = int(until)
    except (TypeError, ValueError):
        until = 0
    if round_no < until:
        wait = _backoff_rounds(fails)
        return True, (f"退避中（连续 {fails} 次失败，每 {wait} 轮重试一次，"
                      f"本轮为第 {round_no} 轮，下次尝试第 {until} 轮）")
    return False, ""


def _update_health(health: dict, name: str, success: bool, round_no: int) -> None:
    """更新单个信源的健康状态"""
    now = datetime.now().isoformat()
    if name not in health:
        health[name] = {
            "consecutive_failures": 0,
            "total_fetches": 0,
            "total_errors": 0,
            "last_success": "",
            "last_error": "",
            "skip_until_round": 0,
        }
    h = health[name]
    h["total_fetches"] = h.get("total_fetches", 0) + 1
    if success:
        had_failures = h.get("consecutive_failures", 0) > 0
        h["consecutive_failures"] = 0
        h["last_success"] = now
        h["skip_until_round"] = 0
        if had_failures:
            h["recovered_at"] = now
    else:
        fails = h.get("consecutive_failures", 0) + 1
        h["consecutive_failures"] = fails
        h["total_errors"] = h.get("total_errors", 0) + 1
        h["last_error"] = now
        if fails >= MAX_CONSECUTIVE_FAILURES:
            h["skip_until_round"] = round_no + _backoff_rounds(fails)


def _check_url_scheme(name: str, url: str):
    """信源地址必须是 http/https（配置可经管理后台修改）"""
    if not url or not url.startswith(("http://", "https://")):
        raise ValueError(f"信源「{name}」的 url 必须为 http/https: {url!r}")


def _truncate(text: str, name: str) -> str:
    """按体积上限截断单个信源的响应体"""
    if len(text) <= MAX_SOURCE_BYTES:
        return text
    print(f"  [FETCH] {name}: 响应过大（{len(text):,} 字符），已截断到 "
          f"{MAX_SOURCE_BYTES:,} 字符")
    return text[:MAX_SOURCE_BYTES]


def _result(source: dict, url: str, text: str, error, rtype: str = None,
            platform: str = None) -> dict:
    item = {
        "source_name": source["name"],
        "language": source.get("language", "en"),
        "url": url,
        "type": rtype or source.get("type", "rss"),
        "xml_text": text,
        "fetch_time": datetime.now().isoformat(),
        "error": error,
    }
    if platform:
        item["api_platform"] = platform
    cfg = source.get("scraper_config")
    if cfg:
        item["scraper_config"] = cfg
    return item


async def fetch_feed(client: httpx.AsyncClient, source: dict) -> dict:
    """抓取单个 RSS 信源，返回 {source_name, url, xml_text, error}"""
    name = source["name"]
    feed_url = source["url"]
    ssl_verify = source.get("ssl_verify", True)

    try:
        _check_url_scheme(name, feed_url)
    except ValueError as e:
        print(f"  [FETCH ERROR] {name}: {e}")
        return _result(source, feed_url, "", str(e))

    print(f"  [FETCH] {name} <- {feed_url}")

    # 对需要跳过 SSL 验证的信源使用独立客户端（存在中间人风险，配置需谨慎）
    if not ssl_verify:
        print(f"  [FETCH] 注意: {name} 已按配置跳过 TLS 证书校验，"
              "该信源内容存在被篡改的可能")
        try:
            async with httpx.AsyncClient(verify=False, headers=client.headers) as insecure_client:
                resp = await insecure_client.get(feed_url, timeout=30.0, follow_redirects=True)
                resp.raise_for_status()
                if resp.text.strip():
                    return _result(source, feed_url, _truncate(resp.text, name), None)
        except Exception as e:
            return _result(source, feed_url, "", str(e))

    first_ua = client.headers.get("User-Agent", "")
    first_error = None
    try:
        resp = await client.get(feed_url, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        # 检查空响应 (某些信源对特定UA返回200但空body)
        if resp.text.strip():
            return _result(source, feed_url, _truncate(resp.text, name), None)
    except httpx.HTTPStatusError as e:
        first_error = f"HTTP {e.response.status_code}"
    except httpx.RequestError as e:
        first_error = str(e)

    # 重试：依次换用 RSS 阅读器 UA（跳过与首次相同的 UA）
    last_error = None
    for retry_ua in RSS_READER_UAS:
        if retry_ua == first_ua:
            continue
        try:
            resp2 = await client.get(
                feed_url, timeout=30.0, follow_redirects=True,
                headers={"User-Agent": retry_ua, "Accept": _RSS_ACCEPT})
            resp2.raise_for_status()
            if resp2.text.strip():
                return _result(source, feed_url, _truncate(resp2.text, name), None)
            last_error = "空响应"
        except Exception as e:
            last_error = str(e)

    msg = last_error or first_error or "请求失败"
    print(f"  [FETCH ERROR] {name}: {msg}")
    return _result(source, feed_url, "", msg)


async def fetch_api(client: httpx.AsyncClient, source: dict) -> dict:
    """抓取单个 API 信源，返回 {source_name, url, xml_text, error}"""
    name = source["name"]
    feed_url = source["url"]
    # 未显式配置 api_platform 时，按信源名回补，使密钥/专用请求头生效
    platform = source.get("api_platform") or SOURCE_NAME_PLATFORMS.get(name, "")
    print(f"  [FETCH API] {name} <- {feed_url}")

    try:
        _check_url_scheme(name, feed_url)
    except ValueError as e:
        print(f"  [FETCH API ERROR] {name}: {e}")
        return _result(source, feed_url, "", str(e), rtype="api", platform=platform)

    platform_handlers = {
        "arxiv": _fetch_arxiv,
        "semantic_scholar": _fetch_semantic_scholar,
        "ietf": _fetch_ietf,
        "mitre_attack": _fetch_mitre_attack,
        "github": _fetch_github,
        "github_repo": _fetch_github_repo,
        "secrss": _fetch_secrss,
    }

    handler = platform_handlers.get(platform)
    if not handler:
        # 无平台信息时回退：基本 GET
        print(f"  [FETCH API] {name} 无可用 api_platform，回退至基本 GET")
        try:
            resp = await client.get(feed_url, timeout=30.0, follow_redirects=True)
            resp.raise_for_status()
            return _result(source, feed_url, _truncate(resp.text, name), None,
                           rtype="api", platform=platform)
        except Exception as e2:
            return _result(source, feed_url, "", str(e2), rtype="api", platform=platform)

    try:
        return await handler(client, source, platform)
    except Exception as e:
        print(f"  [FETCH API ERROR] {name}: {e}")
        return _result(source, feed_url, "", str(e), rtype="api", platform=platform)


async def _fetch_arxiv(client, source, platform):
    url = source["url"]
    resp = await client.get(url, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    return _result(source, url, _truncate(resp.text, source["name"]), None,
                   rtype="api", platform=platform)


async def _fetch_semantic_scholar(client, source, platform):
    """Semantic Scholar API：带 x-api-key 可显著提高频率上限"""
    url = source["url"]
    headers = {}
    if SCHOLAR_API_KEY:
        headers["x-api-key"] = SCHOLAR_API_KEY
    resp = await client.get(url, timeout=30.0, follow_redirects=True, headers=headers)
    resp.raise_for_status()
    return _result(source, url, _truncate(resp.text, source["name"]), None,
                   rtype="api", platform=platform)


async def _fetch_ietf(client, source, platform):
    url = source["url"]
    resp = await client.get(url, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    return _result(source, url, _truncate(resp.text, source["name"]), None,
                   rtype="api", platform=platform)


async def _fetch_mitre_attack(client, source, platform):
    """MITRE ATT&CK STIX：数据量大，单独放宽超时"""
    url = source["url"]
    resp = await client.get(url, timeout=120.0, follow_redirects=True)
    resp.raise_for_status()
    return _result(source, url, _truncate(resp.text, source["name"]), None,
                   rtype="api", platform=platform)


async def _fetch_github(client, source, platform):
    """GitHub API：带 token 可显著提高频率上限"""
    url = source["url"]
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    resp = await client.get(url, timeout=30.0, follow_redirects=True, headers=headers)
    resp.raise_for_status()
    return _result(source, url, _truncate(resp.text, source["name"]), None,
                   rtype="api", platform=platform)


async def _fetch_github_repo(client, source, platform):
    """GitHub API：获取特定仓库的 Releases 或 repo 信息"""
    url = source["url"]
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    resp = await client.get(url, timeout=30.0, follow_redirects=True, headers=headers)
    resp.raise_for_status()
    return _result(source, url, _truncate(resp.text, source["name"]), None,
                   rtype="api", platform=platform)


async def _fetch_secrss(client, source, platform):
    """安全内参 API：基本 GET"""
    url = source["url"]
    resp = await client.get(url, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    return _result(source, url, _truncate(resp.text, source["name"]), None,
                   rtype="api", platform=platform)


async def fetch_all() -> list[dict]:
    """抓取所有启用的信源（处于退避期的信源本轮跳过）"""
    if not SOURCES_PATH.exists():
        raise FileNotFoundError(f"信源配置不存在: {SOURCES_PATH}")
    with open(SOURCES_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if not isinstance(config, dict) or not isinstance(config.get("sources"), list):
        raise ValueError(f"信源配置结构不正确（缺少 sources 列表）: {SOURCES_PATH}")

    all_sources = [s for s in config["sources"] if s.get("enabled", True)]

    health = _load_health()
    round_no = _next_round(health)

    sources, backoff = [], []
    for s in all_sources:
        skip, reason = _should_backoff(health, s["name"], round_no)
        (backoff if skip else sources).append((s, reason))

    if backoff:
        print(f"[FETCHER] 退避跳过 {len(backoff)} 个信源（连续失败后按指数间隔重试）:")
        for s, reason in backoff:
            print(f"  ⏳ {s['name']}: {reason}")

    print(f"[FETCHER] 开始抓取 {len(sources)} 个信源（退避跳过 {len(backoff)} 个，"
          f"第 {round_no} 轮）...")

    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
    }
    async with httpx.AsyncClient(headers=headers) as client:
        tasks = []
        for src, _ in sources:
            if src.get("type") == "api":
                tasks.append(fetch_api(client, src))
            else:
                tasks.append(fetch_feed(client, src))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        results = [r if isinstance(r, dict) else {
            "source_name": "",
            "language": "",
            "url": "",
            "type": "",
            "xml_text": "",
            "fetch_time": datetime.now().isoformat(),
            "error": str(r),
        } for r in results]

    # 退避跳过的信源：只记录状态，**不计入失败次数**（旧实现每轮都 +1，
    # 导致“连续 N 次失败”虚高且永远无法恢复）
    for s, reason in backoff:
        results.append({
            "source_name": s["name"],
            "language": s.get("language", "en"),
            "url": s["url"],
            "type": s.get("type", "rss"),
            "xml_text": "",
            "fetch_time": datetime.now().isoformat(),
            "error": f"backoff ({reason})",
        })

    # 更新健康记录（仅真实抓取的信源）
    for r in results:
        name = r.get("source_name", "")
        if not name or (isinstance(r.get("error"), str)
                        and r["error"].startswith("backoff")):
            continue
        _update_health(health, name, r["error"] is None, round_no)

    recovered = [n for n, h in health.items()
                 if n != HEALTH_META_KEY and isinstance(h, dict)
                 and h.get("recovered_at", "").startswith(datetime.now().strftime("%Y-%m-%d"))]
    if recovered:
        print(f"[FETCHER] 已恢复正常抓取的信源: {', '.join(recovered)}")

    _save_health(health)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(RAW_ITEMS_PATH, results, indent=2)

    # 每个信源的抓取状态（供管理后台展示）
    fetch_status = {}
    for r in results:
        name = r.get("source_name", "")
        if not name:
            continue
        error = r.get("error")
        if error is None:
            status = "success"
        elif isinstance(error, str) and error.startswith("backoff"):
            status = "backoff"
        else:
            status = "error"
        fetch_status[name] = {
            "status": status,
            "error": error,
            "fetch_time": r.get("fetch_time", ""),
        }
    atomic_write(DATA_DIR / "fetch_status.json", fetch_status, indent=2)

    success = sum(1 for r in results if r.get("error") is None)
    backoff_count = sum(1 for r in results
                        if isinstance(r.get("error"), str) and r["error"].startswith("backoff"))
    msg = f"[FETCHER] 完成: {success}/{len(results)} 成功"
    if backoff_count:
        msg += f"（{backoff_count} 个退避中）"
    print(msg)

    try:
        size = RAW_ITEMS_PATH.stat().st_size
        if size > 50 * 1024 * 1024:
            print(f"[FETCHER] 注意: raw_items.json 已达 {size / 1048576:.1f} MB，"
                  "其中含各信源原始响应全文，可考虑精简信源")
    except OSError:
        pass

    return results


if __name__ == "__main__":
    asyncio.run(fetch_all())
