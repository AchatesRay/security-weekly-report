import json
import yaml
from pathlib import Path

DATA_DIR = Path("data")
CONFIG_PATH = Path("llm_config.yaml")
CLASSIFIED_ITEMS_PATH = DATA_DIR / "classified_items.json"
ENHANCED_ITEMS_PATH = DATA_DIR / "enhanced_items.json"


def process(items: list[dict], config: dict) -> list[dict]:
    """
    LLM 摘要模块（预留空壳）。
    当 config.enabled = true 时，调用指定 LLM 为每条内容生成摘要。
    当前阶段：直接返回原数据，不做处理。
    """
    if config.get("enabled", False):
        print("[LLM] LLM 功能已启用但尚未实现 — 直接透传数据")
    else:
        print("[LLM] LLM 功能未启用（默认），透传数据")

    return items


def run():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    with open(CLASSIFIED_ITEMS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    result = process(items, config)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(ENHANCED_ITEMS_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return result


if __name__ == "__main__":
    run()
