import json
import os
import shutil
import hashlib
import yaml
from pathlib import Path
from datetime import datetime, timedelta
import re
from jinja2 import Environment, FileSystemLoader, select_autoescape

DATA_DIR = Path("data")
REPORTS_DIR = Path("reports")
TEMPLATES_DIR = Path("templates")
CONFIG_PATH = Path("config/settings.json")
CLASSIFIED_ITEMS_PATH = DATA_DIR / "classified_items.json"
TRANSLATED_ITEMS_PATH = DATA_DIR / "translated_items.json"
ENHANCED_ITEMS_PATH = DATA_DIR / "enhanced_items.json"
FETCH_STATUS_PATH = DATA_DIR / "fetch_status.json"
SOURCE_CONFIG_PATH = Path("config/source_config.yaml")
SOURCE_HEALTH_PATH = DATA_DIR / "source_health.json"
LATEST_REPORT = REPORTS_DIR / "Security_Reports.html"

# 信源组别 → 告警严重级别
SOURCE_ALERT_SEVERITY = {
    "政府与CERT": "high",
    "安全厂商": "medium",
    "安全媒体": "medium",
    "国内信源": "medium",
    "AI厂商": "low",
    "开发者社区": "low",
}


def _load_category_order() -> list[str]:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("category_order", [])
    except Exception:
        return []


CATEGORY_ORDER = _load_category_order() or [
    "① AI/LLM 安全",
    "② 威胁情报与攻防对抗",
    "③ 漏洞态势与供应链安全",
    "④ 政策法规与标准框架",
    "⑤ 产业动态与技术趋势",
    "⑥ 数据安全与隐私保护",
    "未分类",
]



def get_week_number(dt: datetime) -> str:
    """返回 ISO 周号字符串，如 2026W26"""
    iso = dt.isocalendar()
    return f"{iso[0]}W{iso[1]:02d}"


def group_by_category(items: list[dict]) -> dict:
    """按分类对内容分组"""
    groups = {cat: [] for cat in CATEGORY_ORDER}
    for item in items:
        cat = item.get("category", "未分类")
        if cat not in groups:
            cat = "未分类"
        groups[cat].append(item)
    # 移除空组
    return {k: v for k, v in groups.items() if v}


def archive_previous_report():
    """将现有 Security_Reports.html 重命名为带周号的归档文件"""
    if LATEST_REPORT.exists():
        # 读取旧文件 mtime 所在周
        mtime = datetime.fromtimestamp(LATEST_REPORT.stat().st_mtime)
        week_str = get_week_number(mtime)
        archive_name = REPORTS_DIR / f"Security_Reports_{week_str}.html"
        shutil.move(str(LATEST_REPORT), str(archive_name))
        print(f"[REPORT] 归档旧报告: {archive_name.name}")


def _source_color(source_name: str) -> int:
    """为信源名称生成确定性的色相值 (0-360)"""
    h = int(hashlib.md5(source_name.encode()).hexdigest()[:6], 16) % 360
    return h


def build_json_items(items: list[dict]) -> list[dict]:
    """预处理条目为前端 JSON 格式"""
    result = []
    for item in items:
        result.append({
            "title": item.get("title_zh") or item.get("title", ""),
            # 优先显示译文摘要；如果译文被截断（<原文长度的一半），则显示原文全文
            "summary": item.get("summary_zh") if (item.get("summary_zh") and
                       len(item.get("summary_zh", "")) >= len(item.get("summary", "")) * 0.5
                       ) else item.get("summary", ""),
            # AI 生成的中文摘要（优先使用翻译后的版本）
            "ai_summary": item.get("ai_summary_zh") or item.get("ai_summary") or "",
            "url": item.get("url", ""),
            "source_name": item.get("source_name", ""),
            "published_date": (item.get("published_date") or "")[:10],
            "content_type": item.get("content_type") or "",
            "source_type": item.get("source_type") or "",
            "category": item.get("category", "未分类"),
            "merged_sources": item.get("merged_sources") or [],
            "fulltext_fetched": item.get("fulltext_fetched"),
            "scoring_matched": item.get("scoring_matched") or {},
            "source_hue": _source_color(item.get("source_name", "")),
            "filter_decision": item.get("filter_decision", ""),
            "confidence_score": item.get("confidence_score", 0),
            "full_body": item.get("full_body") or "",
        })
    return result


def save_weekly_data(items: list[dict], week_str: str):
    """将本周数据保存为独立 JSON 文件"""
    path = REPORTS_DIR / f"data_{week_str}.json"
    from . import atomic_write
    atomic_write(path, items)


def update_manifest(week_str: str, date_range: str, total_count: int):
    """更新周报清单 manifest.json"""
    manifest_path = REPORTS_DIR / "manifest.json"
    manifest = []
    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

    # 移除旧条目（如果有）
    manifest = [e for e in manifest if e["week"] != week_str]
    manifest.append({
        "week": week_str,
        "date_range": date_range,
        "total_count": total_count,
    })
    manifest.sort(key=lambda x: x["week"], reverse=True)

    from . import atomic_write
    atomic_write(manifest_path, manifest, indent=2)


def rebuild_manifest_from_archives():
    """从归档的 JSON 数据文件重建 manifest（用于首次迁移）"""
    manifest_path = REPORTS_DIR / "manifest.json"
    if manifest_path.exists():
        return  # 已存在，不做覆盖

    manifest = []
    for fpath in sorted(REPORTS_DIR.glob("data_*.json")):
        week = fpath.stem.replace("data_", "")
        try:
            with open(fpath, encoding="utf-8") as f:
                items = json.load(f)
            manifest.append({
                "week": week,
                "date_range": "",
                "total_count": len(items),
            })
        except Exception:
            pass

    if manifest:
        manifest.sort(key=lambda x: x["week"], reverse=True)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)


def generate_source_alerts() -> list[dict]:
    """
    检查哪些启用的信源本周未获取到数据，返回告警列表。
    """
    alerts = []
    try:
        with open(SOURCE_CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except Exception:
        return alerts

    sources = cfg.get("sources", [])

    # 读取抓取状态
    fetch_status = {}
    if FETCH_STATUS_PATH.exists():
        try:
            with open(FETCH_STATUS_PATH, encoding="utf-8") as f:
                fetch_status = json.load(f)
        except Exception:
            pass

    # 读取信源健康记录
    source_health = {}
    if SOURCE_HEALTH_PATH.exists():
        try:
            with open(SOURCE_HEALTH_PATH, encoding="utf-8") as f:
                source_health = json.load(f)
        except Exception:
            pass

    # 读取条目的 source_name 计数
    parsed_items_path = DATA_DIR / "parsed_items.json"
    source_item_counts = {}
    if parsed_items_path.exists():
        try:
            with open(parsed_items_path, encoding="utf-8") as f:
                parsed = json.load(f)
            from collections import Counter
            source_item_counts = Counter(i.get("source_name", "") for i in parsed)
        except Exception:
            pass

    for src in sources:
        if not src.get("enabled", True):
            continue
        name = src["name"]
        group = src.get("group", "其他")
        status = fetch_status.get(name, {})
        fetch_ok = status.get("status") == "success"
        item_count = source_item_counts.get(name, 0)
        health = source_health.get(name, {})
        cons_fails = health.get("consecutive_failures", 0)

        if status.get("status") == "auto_disabled":
            alerts.append({
                "source_name": name,
                "group": group,
                "severity": "high",
                "reason": f"自动禁用（连续{cons_fails}次失败）",
                "fetch_error": status.get("error"),
            })
        elif cons_fails >= 3 and not fetch_ok and item_count == 0:
            alerts.append({
                "source_name": name,
                "group": group,
                "severity": "high" if cons_fails >= 5 else "medium",
                "reason": f"连续{cons_fails}次抓取失败",
                "fetch_error": status.get("error"),
            })
        elif not fetch_ok and item_count == 0:
            alerts.append({
                "source_name": name,
                "group": group,
                "severity": SOURCE_ALERT_SEVERITY.get(group, "low"),
                "reason": "抓取失败",
                "fetch_error": status.get("error"),
            })
        elif fetch_ok and item_count == 0:
            alerts.append({
                "source_name": name,
                "group": group,
                "severity": SOURCE_ALERT_SEVERITY.get(group, "low"),
                "reason": "无匹配条目",
                "fetch_error": None,
            })
        elif name not in fetch_status and item_count == 0:
            alerts.append({
                "source_name": name,
                "group": group,
                "severity": SOURCE_ALERT_SEVERITY.get(group, "low"),
                "reason": "未抓取",
                "fetch_error": None,
            })

    # 按严重级别排序：high → medium → low
    severity_order = {"high": 0, "medium": 1, "low": 2}
    alerts.sort(key=lambda a: severity_order.get(a["severity"], 9))
    return alerts


def generate_report():
    # 优先使用翻译后的最终数据（含 AI 摘要+翻译），否则回退到增强数据或分类数据
    if TRANSLATED_ITEMS_PATH.exists():
        with open(TRANSLATED_ITEMS_PATH, "r", encoding="utf-8") as f:
            items = json.load(f)
    elif ENHANCED_ITEMS_PATH.exists():
        with open(ENHANCED_ITEMS_PATH, "r", encoding="utf-8") as f:
            items = json.load(f)
    else:
        with open(CLASSIFIED_ITEMS_PATH, "r", encoding="utf-8") as f:
            items = json.load(f)

    now = datetime.now()
    week_str = get_week_number(now)

    # 计算本周起始和结束日期（周一 ~ 周日）
    monday = now - timedelta(days=now.weekday())
    sunday = monday + timedelta(days=6)
    date_range = f"{monday.strftime('%Y.%m.%d')}-{sunday.strftime('%Y.%m.%d')}"

    # 分离 accepted 和 review 条目
    accepted_items = [i for i in items if i.get("filter_decision") != "review"]
    review_items = [i for i in items if i.get("filter_decision") == "review"]

    # 主报告用 accepted（分类统计基于 accepted）
    groups = group_by_category(accepted_items)

    # 前端数据包含所有条目（含 review），通过 filter_decision 区分
    all_items = accepted_items + review_items
    json_items = build_json_items(all_items)

    # 统计
    total_count = len(accepted_items)
    review_count = len(review_items)

    # 信源缺失告警
    source_alerts = generate_source_alerts()

    # 翻译状态（用于模板展示提示横幅）
    translation_warning = ""
    translation_status_path = DATA_DIR / "translation_status.json"
    if translation_status_path.exists():
        try:
            with open(translation_status_path, encoding="utf-8") as f:
                ts = json.load(f)
            if ts.get("status") == "unavailable":
                translation_warning = ts.get("message", "翻译API不可用")
        except Exception:
            pass

    # 保存当前周数据 + 更新清单
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    save_weekly_data(build_json_items(accepted_items), week_str)
    save_weekly_data(build_json_items(review_items), f"{week_str}_review")
    update_manifest(week_str, date_range, total_count)

    # 读取 manifest 供模板使用
    manifest_path = REPORTS_DIR / "manifest.json"
    manifest = []
    if manifest_path.exists():
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)
    template = env.get_template("weekly_report.html")

    # 提取分类名（去掉前缀）和数量，用于三栏模板的侧边栏
    cat_names = []
    cat_counts = []
    for cat in CATEGORY_ORDER:
        if cat in groups:
            # 去掉 "① ", "② " 等前缀
            name = re.sub(r"^[①②③④⑤⑥]\s*", "", cat)
            cat_names.append(name)
            cat_counts.append(len(groups[cat]))

    html = template.render(
        week_str=week_str,
        date_range=date_range,
        generate_time=now.strftime("%Y-%m-%d %H:%M"),
        total_count=total_count,
        review_count=review_count,
        groups=groups,
        json_items=json_items,
        manifest=manifest,
        source_alerts=source_alerts,
        translation_warning=translation_warning,
        cat_names=cat_names,
        cat_counts=cat_counts,
    )

    # 归档旧 HTML
    archive_previous_report()

    # 写新报告（原子写入，避免中断导致 HTML 损坏）
    import tempfile
    tmp = LATEST_REPORT.with_suffix(".tmp")
    try:
        tmp.write_text(html, encoding="utf-8")
        os.replace(str(tmp), str(LATEST_REPORT))
    finally:
        if tmp.exists():
            tmp.unlink()

    print(f"[REPORT] 周报生成完成: {LATEST_REPORT}")
    print(f"[REPORT] 共 {total_count} 条（其中 {review_count} 条待复核）")
    return str(LATEST_REPORT)


if __name__ == "__main__":
    generate_report()
