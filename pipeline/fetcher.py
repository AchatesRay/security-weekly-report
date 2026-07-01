import httpx
import yaml
import json
from pathlib import Path
from datetime import datetime
import asyncio
import random
import os

from . import atomic_write, load_secrets

CONFIG_PATH = Path("config/source_config.yaml")
DATA_DIR = Path("data")
RAW_ITEMS_PATH = DATA_DIR / "raw_items.json"
SOURCE_HEALTH_PATH = DATA_DIR / "source_health.json"
# 连续失败上限：达到后自动禁用信源
MAX_CONSECUTIVE_FAILURES = 5

# User-Agents to rotate between for bypassing RSS blocks
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


# RSS-reader UAs for retry (less likely to be blocked than browser UAs)
RSS_READER_UAS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "NetNewsWire/6.1 (Mac; Intel Mac OS X 14.4)",
]


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
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_health(health: dict):
    """写入信源健康记录"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(SOURCE_HEALTH_PATH, health, indent=2)


def _get_auto_disabled_sources(health: dict) -> set[str]:
    """返回连续 MAX_CONSECUTIVE_FAILURES 次失败的信源名称集合"""
    disabled = set()
    for name, h in health.items():
        if h.get("consecutive_failures", 0) >= MAX_CONSECUTIVE_FAILURES and not h.get("recovered", False):
            disabled.add(name)
    return disabled


def _update_health(health: dict, name: str, success: bool):
    """更新单个信源的健康状态"""
    now = datetime.now().isoformat()
    if name not in health:
        health[name] = {
            "consecutive_failures": 0,
            "total_fetches": 0,
            "total_errors": 0,
            "last_success": "",
            "last_error": "",
        }
    h = health[name]
    h["total_fetches"] = h.get("total_fetches", 0) + 1
    if success:
        h["consecutive_failures"] = 0
        h["last_success"] = now
        # 恢复标记：此前被自动禁用但现已恢复
        if h.get("consecutive_failures", 0) == 0:
            h["recovered"] = True
    else:
        h["consecutive_failures"] = h.get("consecutive_failures", 0) + 1
        h["total_errors"] = h.get("total_errors", 0) + 1
        h["last_error"] = now
        h["recovered"] = False


async def fetch_feed(client: httpx.AsyncClient, source: dict) -> dict:
    """抓取单个 RSS 信源，返回 {source_name, url, xml_text, error}"""
    name = source["name"]
    feed_url = source["url"]
    ssl_verify = source.get("ssl_verify", True)
    print(f"  [FETCH] {name} <- {feed_url}")

    # 对需要跳过 SSL 验证的信源使用独立客户端
    if not ssl_verify:
        try:
            async with httpx.AsyncClient(verify=False, headers=client.headers) as insecure_client:
                resp = await insecure_client.get(feed_url, timeout=30.0, follow_redirects=True)
                resp.raise_for_status()
                if resp.text.strip():
                    return {
                        "source_name": name,
                        "source_level": source["source_level"],
                        "region": source["region"],
                        "language": source["language"],
                        "url": feed_url,
                        "type": source.get("type", "rss"),
                        "xml_text": resp.text,
                        "fetch_time": datetime.now().isoformat(),
                        "error": None,
                    }
        except Exception as e:
            return {
                "source_name": name,
                "source_level": source["source_level"],
                "region": source["region"],
                "language": source["language"],
                "url": feed_url,
                "type": source.get("type", "rss"),
                "xml_text": "",
                "fetch_time": datetime.now().isoformat(),
                "error": str(e),
            }

    first_ua = client.headers.get("User-Agent", "")

    first_error = None
    try:
        resp = await client.get(feed_url, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        # 检查空响应 (某些信源对特定UA返回200但空body)
        if resp.text.strip():
            return {
                "source_name": name,
                "source_level": source["source_level"],
                "region": source["region"],
                "language": source["language"],
                "url": feed_url,
                "type": source.get("type", "rss"),
                "xml_text": resp.text,
                "fetch_time": datetime.now().isoformat(),
                "error": None,
            }
    except httpx.HTTPStatusError as e:
        first_error = f"HTTP {e.response.status_code}"
    except httpx.RequestError as e:
        first_error = str(e)

    # 重试：换用RSS reader UA + Accept头
    try:
        retry_ua = RSS_READER_UAS[0] if first_ua != RSS_READER_UAS[0] else RSS_READER_UAS[1]
        alt_headers = {
            "User-Agent": retry_ua,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
        }
        resp2 = await client.get(feed_url, timeout=30.0, follow_redirects=True, headers=alt_headers)
        resp2.raise_for_status()
        return {
            "source_name": name,
            "source_level": source["source_level"],
            "region": source["region"],
            "language": source["language"],
            "url": feed_url,
            "type": source.get("type", "rss"),
            "xml_text": resp2.text,
            "fetch_time": datetime.now().isoformat(),
            "error": None,
        }
    except Exception as e:
        msg = str(e) or first_error or "请求失败"
        print(f"  [FETCH ERROR] {name}: {msg}")
        return {
            "source_name": name,
            "source_level": source["source_level"],
            "region": source["region"],
            "language": source["language"],
            "url": feed_url,
            "type": source.get("type", "rss"),
            "xml_text": "",
            "fetch_time": datetime.now().isoformat(),
            "error": msg,
        }


def _make_api_result(source: dict, url: str, text: str, platform: str, error: str | None) -> dict:
    """构造统一的 API 采集结果字典，减少五个 _fetch_* 函数中的样板代码"""
    return {
        "source_name": source["name"],
        "source_level": source["source_level"],
        "region": source["region"],
        "language": source["language"],
        "url": url,
        "type": "api",
        "api_platform": platform,
        "xml_text": text,
        "fetch_time": datetime.now().isoformat(),
        "error": error,
    }


async def fetch_api(client: httpx.AsyncClient, source: dict) -> dict:
    """抓取单个 API 信源，返回 {source_name, url, xml_text, error}"""
    name = source["name"]
    platform = source.get("api_platform", "")
    feed_url = source["url"]
    print(f"  [FETCH API] {name} <- {feed_url}")

    # 按 api_platform 分发
    platform_handlers = {
        "arxiv": _fetch_arxiv,
        "semantic_scholar": _fetch_semantic_scholar,
        "ietf": _fetch_ietf,
        "mitre_attack": _fetch_mitre_attack,
        "github": _fetch_github,
        "secrss": _fetch_secrss,
    }

    handler = platform_handlers.get(platform)
    if not handler:
        # 无 api_platform 时回退：对部分旧 API 源（如 安全内参）尝试基本 GET
        if platform == "" and "<" not in source.get("url", ""):
            print(f"  [FETCH API] {name} 无 api_platform，回退至基本 GET")
            try:
                resp = await client.get(feed_url, timeout=30.0, follow_redirects=True)
                resp.raise_for_status()
                return _make_api_result(source, feed_url, resp.text, "", None)
            except Exception as e2:
                return _make_api_result(source, feed_url, "", "", str(e2))
        return _make_api_result(source, feed_url, "", platform,
                                f"未知 api_platform: {platform}")

    try:
        result = await handler(client, source)
        return result
    except Exception as e:
        print(f"  [FETCH API ERROR] {name}: {e}")
        return _make_api_result(source, feed_url, "", platform, str(e))


async def _fetch_arxiv(client: httpx.AsyncClient, source: dict) -> dict:
    """arXiv API: 按分类查询最新论文"""
    url = source["url"]
    resp = await client.get(url, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    return _make_api_result(source, url, resp.text, "arxiv", None)


async def _fetch_semantic_scholar(client: httpx.AsyncClient, source: dict) -> dict:
    """Semantic Scholar API: 搜索安全相关论文"""
    url = source["url"]
    headers = {}
    if SCHOLAR_API_KEY:
        headers["x-api-key"] = SCHOLAR_API_KEY
    resp = await client.get(url, timeout=30.0, follow_redirects=True, headers=headers)
    resp.raise_for_status()
    return _make_api_result(source, url, resp.text, "semantic_scholar", None)


async def _fetch_ietf(client: httpx.AsyncClient, source: dict) -> dict:
    """IETF Datatracker API: 获取最新 drafts"""
    url = source["url"]
    resp = await client.get(url, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    return _make_api_result(source, url, resp.text, "ietf", None)


async def _fetch_mitre_attack(client: httpx.AsyncClient, source: dict) -> dict:
    """MITRE ATT&CK STIX: 从 GitHub 获取完整 ATT&CK 数据"""
    url = source["url"]
    resp = await client.get(url, timeout=60.0, follow_redirects=True)
    resp.raise_for_status()
    return _make_api_result(source, url, resp.text, "mitre_attack", None)


async def _fetch_github(client: httpx.AsyncClient, source: dict) -> dict:
    """GitHub API: 搜索安全相关热门仓库"""
    url = source["url"]
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    resp = await client.get(url, timeout=30.0, follow_redirects=True, headers=headers)
    resp.raise_for_status()
    return _make_api_result(source, url, resp.text, "github", None)


async def _fetch_secrss(client: httpx.AsyncClient, source: dict) -> dict:
    """安全内参 API: 获取安全资讯（简单 GET 请求）"""
    url = source["url"]
    resp = await client.get(url, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    return _make_api_result(source, url, resp.text, "secrss", None)


async def fetch_all() -> list[dict]:
    """抓取所有启用的信源（自动跳过健康状态异常的信源）"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    all_sources = [s for s in config["sources"] if s.get("enabled", True)]

    # 加载健康记录，自动过滤异常信源
    health = _load_health()
    auto_disabled = _get_auto_disabled_sources(health)
    sources = [s for s in all_sources if s["name"] not in auto_disabled]
    skipped = [s for s in all_sources if s["name"] in auto_disabled]

    if skipped:
        print(f"[FETCHER] 自动跳过 {len(skipped)} 个异常信源（连续{MAX_CONSECUTIVE_FAILURES}+次失败）:")
        for s in skipped:
            h = health.get(s["name"], {})
            fails = h.get("consecutive_failures", 0)
            last_err = h.get("last_error", "")[:19] if h.get("last_error") else ""
            print(f"  ⛔ {s['name']} ({fails}次连续失败, 最后失败: {last_err})")

    print(f"[FETCHER] 开始抓取 {len(sources)} 个信源（跳过 {len(skipped)} 个异常信源）...")

    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
    }
    async with httpx.AsyncClient(headers=headers) as client:
        tasks = []
        for src in sources:
            if src.get("type") == "api":
                tasks.append(fetch_api(client, src))
            else:
                tasks.append(fetch_feed(client, src))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        results = [r if isinstance(r, dict) else {
            "source_name": "",
            "source_level": "",
            "region": "",
            "language": "",
            "url": "",
            "type": "",
            "xml_text": "",
            "fetch_time": datetime.now().isoformat(),
            "error": str(r),
        } for r in results]

    # 追加自动跳过信源的记录（保留在结果内供下游查看）
    for s in skipped:
        h = health.get(s["name"], {})
        fail_count = h.get("consecutive_failures", 0)
        results.append({
            "source_name": s["name"],
            "source_level": s["source_level"],
            "region": s["region"],
            "language": s["language"],
            "url": s["url"],
            "type": s.get("type", "rss"),
            "xml_text": "",
            "fetch_time": datetime.now().isoformat(),
            "error": f"auto_disabled ({fail_count}次连续失败)",
        })

    # 更新健康记录
    for r in results:
        name = r.get("source_name", "")
        if name:
            _update_health(health, name, r["error"] is None)
    _save_health(health)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(RAW_ITEMS_PATH, results, indent=2)

    # Write per-source fetch status for config UI
    fetch_status = {}
    for r in results:
        name = r.get("source_name", "")
        if not name:
            continue
        error = r["error"]
        is_auto_disabled = isinstance(error, str) and error.startswith("auto_disabled")
        fetch_status[name] = {
            "status": "success" if error is None else ("auto_disabled" if is_auto_disabled else "error"),
            "error": r["error"],
            "fetch_time": r.get("fetch_time", ""),
        }
    atomic_write(DATA_DIR / "fetch_status.json", fetch_status, indent=2)

    success = sum(1 for r in results if r["error"] is None)
    auto_skipped = sum(1 for r in results if isinstance(r.get("error"), str) and r["error"].startswith("auto_disabled"))
    msg = f"[FETCHER] 完成: {success}/{len(results)} 成功"
    if auto_skipped:
        msg += f" ({auto_skipped} 个自动跳过)"
    print(msg)
    return results


if __name__ == "__main__":
    asyncio.run(fetch_all())
