"""关键字过滤模块 — 两阶段过滤

阶段 1（解析后）：匹配标题 + 摘要
阶段 2（全文提取后）：对阶段 1 未匹配的条目，用正文再次匹配

关键字存储在 config/settings.json 的 security_keywords 字段中。
匹配逻辑：命中任意关键字即保留（OR）。
"""

import json
import re
from pathlib import Path

DATA_DIR = Path("data")
PARSED_ITEMS_PATH = DATA_DIR / "parsed_items.json"
SETTINGS_PATH = Path("config/settings.json")
KEYWORDS_PATH = Path("config/keywords.json")

# 默认网络安全关键字列表（中英文）
DEFAULT_KEYWORDS = sorted([
    # 通用安全
    "security", "cybersecurity", "cyber security",
    "安全", "网络安全", "信息安全",
    # 漏洞
    "vulnerability", "cve", "cwe", "0day", "zero-day", "zero day",
    "漏洞", "零日", "弱點",
    # 攻击
    "attack", "exploit", "malware", "ransomware", "trojan",
    "backdoor", "worm", "rootkit", "spyware",
    "攻击", "恶意软件", "勒索", "木马", "后门", "蠕虫",
    # 黑客
    "hacker", "hacking", "hack",
    "黑客",
    # 数据泄露
    "data breach", "data leak", "leak",
    "泄露", "泄漏", "数据安全",
    # 入侵与防御
    "intrusion", "penetration", "pentest", "firewall", "ids", "ips",
    "入侵", "渗透", "防火墙", "检测",
    # 加密与认证
    "encryption", "cryptography", "authentication", "mfa", "oauth",
    "加密", "认证", "身份验证",
    # 钓鱼与社工
    "phishing", "social engineering", "socialengineer",
    "钓鱼", "社工", "社会工程",
    # DDoS / Botnet
    "ddos", "botnet", "dos attack",
    "僵尸网络",
    # APT / 威胁情报
    "apt ", "apt-", "threat intelligence", "threat intel",
    "apt攻击", "威胁情报", "apt组织",
    # 补丁
    "patch", "hotfix",
    "补丁", "修复",
    # 隐私与合规
    "privacy", "gdpr", "compliance",
    "隐私", "合规", "等保",
    # 供应链安全
    "supply chain", "software supply chain",
    "供应链",
    # 应急响应
    "incident response", "soc", "应急响应",
    # AI 安全
    "ai security", "llm security", "prompt injection",
    "ai安全", "大模型安全",
    # 移动安全
    "mobile security", "android security", "ios security",
    # Web 安全
    "xss", "sql injection", "sqli", "csrf", "ssrf", "rce",
    "跨站", "注入",
    # 其他
    "forensics", "取证",
    "bug bounty", "漏洞赏金",
    "red team", "blue team", "红队", "蓝队",
    "zero trust", "零信任",
    "sase", "sso",
    "端点安全", "终端安全",
    "云安全", "cloud security",
    "物联网安全", "iot security",
    "工控安全", "ics security", "scada",
    "恶意", "威胁", "风险",
])


def load_keywords() -> list[str]:
    """从 keywords.json 加载关键字列表（回退 settings.json 兼容旧数据）"""
    try:
        with open(KEYWORDS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("security_keywords", [])
    except Exception:
        pass
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("security_keywords", [])
    except Exception:
        return []


def save_keywords(keywords: list[str]) -> bool:
    """保存关键字列表到 keywords.json"""
    cleaned = sorted(set(kw.strip() for kw in keywords if kw.strip()))
    try:
        with open(KEYWORDS_PATH, "w", encoding="utf-8") as f:
            json.dump({"security_keywords": cleaned}, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def init_default_keywords():
    """如配置中无关键字，写入默认列表"""
    existing = load_keywords()
    if not existing:
        save_keywords(DEFAULT_KEYWORDS)
        print(f"[KEYWORD] 已初始化 {len(DEFAULT_KEYWORDS)} 个默认关键字")


def get_match_text(item: dict) -> str:
    """提取用于关键字匹配的文本（标题 + 摘要 + 正文）"""
    parts = [
        item.get("title") or "",
        item.get("summary") or "",
    ]
    # 如果有保留的原文摘要（全文提取前），也加入匹配
    orig = item.get("original_summary") or ""
    if orig and orig != item.get("summary", ""):
        parts.append(orig)
    return " ".join(parts)


def matches_keywords(item: dict, keywords: list[str]) -> bool:
    """检查条目是否匹配任一关键字（不区分大小写）"""
    text = get_match_text(item).lower()
    for kw in keywords:
        if not kw.strip():
            continue
        if kw.lower() in text:
            return True
    return False


def filter_items(items: list[dict], keywords: list[str],
                 stage: str = "stage1") -> tuple[list[dict], list[dict]]:
    """
    过滤条目。

    stage1: 过滤后 keep 的条目标记 keyword_matched=true
            unmatched 的条目设置 keyword_matched=false，继续保留（等待阶段2）
    stage2: unmatched 条目再次匹配，仍不匹配的丢弃

    返回 (kept, discarded)
    """
    if not keywords:
        return items, []

    kept = []
    discarded = []

    for item in items:
        matched = matches_keywords(item, keywords)
        item["keyword_matched"] = matched

        if matched:
            kept.append(item)
        elif stage == "stage1":
            # 阶段1：不匹配也保留，等阶段2用全文再试
            kept.append(item)
        else:
            # 阶段2：仍不匹配则丢弃
            discarded.append(item)

    return kept, discarded


def run_stage1():
    """阶段1过滤：解析之后，全文提取之前"""
    init_default_keywords()
    keywords = load_keywords()
    if not keywords:
        return

    with open(PARSED_ITEMS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    total_before = len(items)
    kept, discarded = filter_items(items, keywords, stage="stage1")

    with open(PARSED_ITEMS_PATH, "w", encoding="utf-8") as f:
        json.dump(kept, f, ensure_ascii=False, indent=2)

    print(f"[KEYWORD] 阶段1过滤: {total_before} → {len(kept)} 条保留"
          f" ({len([i for i in kept if i.get('keyword_matched')])} 条已匹配)")


def run_stage2():
    """阶段2过滤：全文提取之后"""
    keywords = load_keywords()
    if not keywords:
        return

    with open(PARSED_ITEMS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    # 只对阶段1未匹配的条目重新判断
    unmatched = [i for i in items if not i.get("keyword_matched")]
    matched = [i for i in items if i.get("keyword_matched")]

    if not unmatched:
        print(f"[KEYWORD] 阶段2过滤: 无不匹配条目，跳过")
        return

    kept_unmatched, discarded = filter_items(unmatched, keywords, stage="stage2")

    final = matched + kept_unmatched
    total_discarded = len(discarded)

    # 写回
    with open(PARSED_ITEMS_PATH, "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)

    print(f"[KEYWORD] 阶段2过滤: 未匹配 {len(unmatched)} 条中 "
          f"{len(kept_unmatched)} 条通过, {total_discarded} 条丢弃")


if __name__ == "__main__":
    # 测试
    init_default_keywords()
    print(f"当前关键字: {len(load_keywords())} 个")
    run_stage1()
