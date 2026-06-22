import httpx
import yaml
import json
from pathlib import Path
from datetime import datetime
import asyncio

CONFIG_PATH = Path("source_config.yaml")
DATA_DIR = Path("data")
RAW_ITEMS_PATH = DATA_DIR / "raw_items.json"

async def fetch_feed(client: httpx.AsyncClient, source: dict) -> dict:
    """抓取单个 RSS 信源，返回 {source_name, url, xml_text, error}"""
    name = source["name"]
    feed_url = source["url"]
    print(f"  [FETCH] {name} <- {feed_url}")

    try:
        resp = await client.get(feed_url, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
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
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
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
