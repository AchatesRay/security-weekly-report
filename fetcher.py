import httpx
import yaml
import json
from pathlib import Path
from datetime import datetime
import asyncio
import random

CONFIG_PATH = Path("source_config.yaml")
DATA_DIR = Path("data")
RAW_ITEMS_PATH = DATA_DIR / "raw_items.json"

# User-Agents to rotate between for bypassing RSS blocks
USER_AGENTS = [
    # Chrome browser (most compatible)
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    # Feedbin RSS reader
    "Feedbin/1.0 (+https://feedbin.com)",
    # Feedly RSS reader
    "Feedly/1.0 (+https://feedly.com)",
    # Apple RSS reader
    "Apple-PubSub/1.0 (+https://apple.com/rss)",
    # Inoreader RSS reader
    "Inoreader/1.0 (+https://inoreader.com)",
]


# RSS-reader UAs for retry (less likely to be blocked than browser UAs)
RSS_READER_UAS = [
    "Feedbin/1.0 (+https://feedbin.com)",
    "Feedly/1.0 (+https://feedly.com)",
]


async def fetch_feed(client: httpx.AsyncClient, source: dict) -> dict:
    """抓取单个 RSS 信源，返回 {source_name, url, xml_text, error}"""
    name = source["name"]
    feed_url = source["url"]
    print(f"  [FETCH] {name} <- {feed_url}")

    first_ua = client.headers.get("User-Agent", "")

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
                "xml_text": resp.text,
                "fetch_time": datetime.now().isoformat(),
                "error": None,
            }
    except (httpx.HTTPStatusError, httpx.RequestError):
        pass

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
            "xml_text": resp2.text,
            "fetch_time": datetime.now().isoformat(),
            "error": None,
        }
    except Exception as e:
        print(f"  [FETCH ERROR] {name}: {e}")
        return {
            "source_name": name,
            "source_level": source["source_level"],
            "region": source["region"],
            "language": source["language"],
            "url": feed_url,
            "xml_text": "",
            "fetch_time": datetime.now().isoformat(),
            "error": str(e),
        }


async def fetch_all() -> list[dict]:
    """抓取所有启用的信源"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    sources = [s for s in config["sources"] if s.get("enabled", True)]
    print(f"[FETCHER] 开始抓取 {len(sources)} 个信源...")

    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
    }
    async with httpx.AsyncClient(headers=headers) as client:
        tasks = [fetch_feed(client, src) for src in sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        results = [r if isinstance(r, dict) else {
            "source_name": "",
            "source_level": "",
            "region": "",
            "language": "",
            "url": "",
            "xml_text": "",
            "fetch_time": datetime.now().isoformat(),
            "error": str(r),
        } for r in results]

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(RAW_ITEMS_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    success = sum(1 for r in results if r["error"] is None)
    print(f"[FETCHER] 完成: {success}/{len(results)} 成功")
    return results


if __name__ == "__main__":
    asyncio.run(fetch_all())
