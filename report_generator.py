import json
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from jinja2 import Environment, FileSystemLoader

DATA_DIR = Path("data")
REPORTS_DIR = Path("reports")
TEMPLATES_DIR = Path("templates")
CLASSIFIED_ITEMS_PATH = DATA_DIR / "classified_items.json"
LATEST_REPORT = REPORTS_DIR / "Security_Reports.html"

CATEGORY_ORDER = [
    "① AI/LLM 安全",
    "② 威胁情报与攻防对抗",
    "③ 漏洞态势与供应链安全",
    "④ 政策法规与标准框架",
    "⑤ 产业动态与技术趋势",
    "⑥ 数据安全与隐私保护",
    "未分类",
]

# CSS 颜色
URGENCY_COLORS = {
    "需立即响应": "#dc3545",
    "近期需关注": "#ffc107",
    "持续关注": "#0d6efd",
    "知识积累": "#6c757d",
}


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


def get_executive_summary(items: list[dict]) -> list[dict]:
    """获取架构师摘要：紧迫性为 🚨 和 ⏰ 的内容"""
    urgent = [i for i in items if i.get("urgency") == "需立即响应"]
    watch = [i for i in items if i.get("urgency") == "近期需关注"]
    return urgent, watch


def archive_previous_report():
    """将现有 Security_Reports.html 重命名为带周号的归档文件"""
    if LATEST_REPORT.exists():
        # 读取旧文件 mtime 所在周
        mtime = datetime.fromtimestamp(LATEST_REPORT.stat().st_mtime)
        week_str = get_week_number(mtime)
        archive_name = REPORTS_DIR / f"Security_Reports_{week_str}.html"
        shutil.move(str(LATEST_REPORT), str(archive_name))
        print(f"[REPORT] 归档旧报告: {archive_name.name}")


def generate_report():
    with open(CLASSIFIED_ITEMS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    now = datetime.now()
    week_str = get_week_number(now)

    # 计算本周起始和结束日期（周一 ~ 周日）
    monday = now - timedelta(days=now.weekday())
    sunday = monday + timedelta(days=6)
    date_range = f"{monday.strftime('%Y.%m.%d')}-{sunday.strftime('%Y.%m.%d')}"

    groups = group_by_category(items)
    urgent_items, watch_items = get_executive_summary(items)

    # 统计
    total_count = len(items)
    urgent_count = len(urgent_items)
    watch_count = len(watch_items)

    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
    template = env.get_template("weekly_report.html")

    html = template.render(
        week_str=week_str,
        date_range=date_range,
        generate_time=now.strftime("%Y-%m-%d %H:%M"),
        total_count=total_count,
        urgent_count=urgent_count,
        watch_count=watch_count,
        urgent_items=urgent_items,
        watch_items=watch_items,
        groups=groups,
        urgency_colors=URGENCY_COLORS,
    )

    # 归档旧报告
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    archive_previous_report()

    # 写新报告
    with open(LATEST_REPORT, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[REPORT] 周报生成完成: {LATEST_REPORT}")
    print(f"[REPORT] 共 {total_count} 条, 🚨 {urgent_count} 条, ⏰ {watch_count} 条")
    return str(LATEST_REPORT)


if __name__ == "__main__":
    generate_report()
