import json
import yaml
from pathlib import Path

DATA_DIR = Path("data")
RULES_PATH = Path("config/classifier_rules.yaml")
DEDUPED_ITEMS_PATH = DATA_DIR / "deduped_items.json"
CLASSIFIED_ITEMS_PATH = DATA_DIR / "classified_items.json"


def load_rules() -> dict:
    """加载分类规则"""
    with open(RULES_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def match_keywords(text: str, keywords: list[str]) -> int:
    """计算文本中命中的关键词数量"""
    text_lower = text.lower()
    count = 0
    for kw in keywords:
        kw_str = str(kw)
        if kw_str.lower() in text_lower:
            count += 1
    return count


def classify_item(item: dict, rules: list[dict]) -> dict:
    """
    基于关键词匹配对内容进行分类和打标。
    分类规则: 取命中关键词最多的规则对应分类。
    无匹配时归入"未分类"。
    """
    text = f"{item.get('title', '')} {item.get('summary', '')}"

    best_category = "未分类"
    best_tags = []
    best_count = 0

    for rule in rules:
        count = match_keywords(text, rule["keywords"])
        if count > best_count:
            best_count = count
            best_category = rule["category"]
            best_tags = rule.get("tags", [])

    item["category"] = best_category
    item["tags"] = best_tags

    text_lower = text.lower()

    # 内容类型推断
    content_type_map = {
        "研究报告/白皮书": ["白皮书", "研究报告", "whitepaper", "white paper",
                           "研究", "research paper", "技术报告"],
        "漏洞披露": ["CVE-", "漏洞披露", "vulnerability disclosure", "0-day",
                     "advisory", "安全公告", "漏洞预警"],
        "攻击活动报告": ["APT", "攻击活动", "threat actor", "threat group",
                        "入侵", "intrusion", "campaign", "攻击链"],
        "工具发布": ["工具", "tool", "发布", "release", "开源项目"],
        "行业分析": ["市场", "market", "报告", "analysis", "趋势",
                     "Gartner", "Forrester", "行业"],
        "法规/标准发布": ["法规", "regulation", "标准", "standard", "法律",
                         "法案", "合规", "compliance", "NIST", "ISO"],
    }

    for content_type, kws in content_type_map.items():
        if any(kw.lower() in text_lower for kw in kws):
            item["content_type"] = content_type
            break
    else:
        item["content_type"] = "综合"

    # 地域推断: 在文本中检测特定国家/地区关键词
    region_map = {
        "cn": ["中国", "国家网信办", "工信部", "cncert", "中国信通院",
               "全国信安标委", "公安三所", "等保", "网信办"],
        "us": ["美国", "CISA", "FBI", "NSA", "白宫", "Biden", "Trump",
               "美国政府", "US government"],
        "eu": ["欧盟", "ENISA", "GDPR", "欧洲", "EU", "European Union"],
    }
    for region, kws in region_map.items():
        if any(kw.lower() in text_lower for kw in kws):
            if item.get("region") != region:
                # 仅在检测到特定地域关键词时覆盖（源级默认为 global）
                item["region"] = region
            break

    return item


def classify_all() -> list[dict]:
    """对所有去重后的内容进行分类"""
    rules = load_rules()["rules"]
    with open(DEDUPED_ITEMS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    classified = [classify_item(item, rules) for item in items]

    # 统计
    categories = {}
    for item in classified:
        cat = item["category"]
        categories[cat] = categories.get(cat, 0) + 1
    print(f"[CLASSIFIER] 分类完成: {len(classified)} 条")
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CLASSIFIED_ITEMS_PATH, "w", encoding="utf-8") as f:
        json.dump(classified, f, ensure_ascii=False, indent=2)

    return classified


if __name__ == "__main__":
    classify_all()
