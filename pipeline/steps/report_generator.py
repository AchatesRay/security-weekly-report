"""报告生成 — Jinja2 渲染 HTML 周报（桌面版）

数据流：translated_items.json（优先）> enhanced_items.json > classified_items.json

2026-09-29 修复：
  1. **数据文件与报告不一致**：原先先写 data_*.json，再渲染 HTML。若渲染
     抛异常，周报 HTML 保持旧版而 data_*.json 已是新版，前端按需加载详情时
     会拿到与列表不匹配的数据。现在渲染成功后才落盘数据文件。
  2. **分类兜底**：group_by_category 依赖 CATEGORY_ORDER 必须包含“未分类”，
     若管理员把 settings.json 的 category_order 改掉（例如删掉“未分类”），
     分类落入“未分类”时会 KeyError 并让整个报告生成失败。现在强制补全。
  3. **缺失输入静默降级**：三个数据源都不存在时原先直接 FileNotFoundError
     被上层吞掉，只打印一行错误并保留旧报告。现在抛出明确的错误信息，
     由编排器统一报告失败。
  4. 相对路径全部改为项目绝对路径。
"""

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader

from .image_localizer import localize_items, prune_assets

from ..utils import (DATA_DIR, REPORTS_DIR, SETTINGS_PATH, SOURCES_PATH,
                     TEMPLATES_DIR, atomic_write, atomic_write_text, precompress)

CLASSIFIED_ITEMS_PATH = DATA_DIR / "classified_items.json"
TRANSLATED_ITEMS_PATH = DATA_DIR / "translated_items.json"
ENHANCED_ITEMS_PATH = DATA_DIR / "enhanced_items.json"
FETCH_STATUS_PATH = DATA_DIR / "fetch_status.json"
SOURCE_HEALTH_PATH = DATA_DIR / "source_health.json"
LATEST_REPORT = REPORTS_DIR / "Security_Reports.html"

UNCLASSIFIED = "未分类"

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
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        order = cfg.get("category_order", [])
        if isinstance(order, list) and order:
            return [c for c in order if isinstance(c, str) and c.strip()]
    except Exception:
        pass
    return []


DEFAULT_CATEGORY_ORDER = [
    "① AI/LLM 安全",
    "② 威胁情报与攻防对抗",
    "③ 漏洞态势与供应链安全",
    "④ 政策法规与标准框架",
    "⑤ 产业动态与技术趋势",
    "⑥ 数据安全与隐私保护",
    UNCLASSIFIED,
]


def _category_order() -> list[str]:
    """返回分类顺序，并保证“未分类”始终存在（否则兜底分组会 KeyError）"""
    order = _load_category_order() or list(DEFAULT_CATEGORY_ORDER)
    if UNCLASSIFIED not in order:
        order.append(UNCLASSIFIED)
    return order


def get_week_number(dt: datetime) -> str:
    """返回 ISO 周号字符串，如 2026W26"""
    iso = dt.isocalendar()
    return f"{iso[0]}W{iso[1]:02d}"


def _load_reader_profile() -> dict:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("reader_profile", {}) or {}
    except Exception:
        return {}


def _load_quality() -> dict:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("quality", {}) or {}
    except Exception:
        return {}


def _rank_key(it: dict) -> float:
    """报告排序：优先相关性，其次深度，最后关键词原始强度。"""
    for key in ("raw_score", "relevance_score", "confidence_score"):
        v = it.get(key)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


def _apply_diversity(items: list[dict], max_per_source: int = 3,
                     max_series_per_source: int = 1) -> list[dict]:
    """单源配额 + 同系列合并：避免一个信源/一个周报系列霸榜。"""
    ranked = sorted(items, key=_rank_key, reverse=True)
    counts: Counter = Counter()
    series_counts: Counter = Counter()
    out = []
    for item in ranked:
        src = item.get("source_name") or ""
        if counts[src] >= max_per_source:
            continue
        if item.get("series_hit"):
            if series_counts[src] >= max_series_per_source:
                continue
            series_counts[src] += 1
        counts[src] += 1
        out.append(item)
    return out


def group_by_category(items: list[dict]) -> dict:
    """兼容旧接口：按分类对内容分组。"""
    order = _category_order()
    groups = {cat: [] for cat in order}
    for item in items:
        cat = item.get("category", UNCLASSIFIED)
        if cat not in groups:
            cat = UNCLASSIFIED
        groups[cat].append(item)
    return {k: v for k, v in groups.items() if v}


def _source_color(source_name: str) -> int:
    """为信源名称生成确定性的色相值 (0-360)"""
    h = int(hashlib.md5(source_name.encode()).hexdigest()[:6], 16) % 360
    return h


def _strip_summary_prefix(body: str, summary: str, min_overlap: int = 60) -> str:
    """正文以摘要开头时剥掉这段重复内容（摘要栏已经显示过一遍）。

    只剥离"从正文开头起、忽略空白后完全一致"的那一段：
    - 摘要被翻译成中文、正文仍是外文时，二者对不上，自然不会误剥；
    - 正文与摘要基本同文（剥完所剩无几）时返回空串：既然没有别的内容，
      就不必在正文栏把同一段话再显示一遍。
    """
    if not body or not summary:
        return body
    key = re.sub(r"\s+", "", summary).rstrip("….。！？!?")
    if len(key) < min_overlap:
        return body

    i = 0
    cut = 0
    for idx, ch in enumerate(body):
        if ch.isspace():
            continue
        if i < len(key) and ch == key[i]:
            i += 1
            cut = idx + 1
            if i == len(key):
                break
        else:
            return body
    if i < len(key):
        return body

    rest = body[cut:].lstrip()
    return rest if len(rest) >= min_overlap else ""


def _strip_html_summary_prefix(html_text: str, summary: str,
                                min_overlap: int = 60) -> str:
    """结构化正文以摘要开头时剥掉第一个块（摘要栏已展示过）。

    纯文本链路的 _strip_summary_prefix() 处理不了标签；这里只看第一个
    块级节点的可见文字，匹配则整块移除，避免同一段话在页面上出现两遍。
    """
    if not html_text or not summary:
        return html_text
    key = re.sub(r"\s+", "", summary).rstrip("….。！？!?")
    if len(key) < min_overlap:
        return html_text
    try:
        soup = BeautifulSoup(html_text, "html.parser")
    except Exception:
        return html_text

    first = soup.find(["p", "h1", "h2", "h3", "h4", "h5", "h6",
                       "blockquote", "li"])
    if first is None:
        return html_text
    text = re.sub(r"\s+", "", first.get_text())
    if not text:
        return html_text

    head = key[:min(len(key), 120)]
    if text.startswith(head[:40]) or head.startswith(text[:40]):
        first.decompose()
        rest = str(soup).strip()
        visible = re.sub(r"<[^>]+>", "", rest).strip()
        return rest if len(visible) >= min_overlap else ""
    return html_text


def build_json_items(items: list[dict]) -> list[dict]:
    """预处理条目为前端 JSON 格式"""
    result = []
    for item in items:
        summary_zh = item.get("summary_zh") or ""
        summary_orig = item.get("summary", "") or ""
        # 译文可信（显式翻译成功）→ 用译文；否则若译文长度不及原文一半，
        # 说明摘要曾被全文替换、译文只覆盖了原短摘要，此时回退展示原文
        if summary_zh and (item.get("summary_translated")
                           or len(summary_zh) >= len(summary_orig) * 0.5):
            summary = summary_zh
        else:
            summary = summary_orig

        ai_summary = item.get("ai_summary_zh") or item.get("ai_summary") or ""
        full_body = item.get("full_body") or ""
        content_html = item.get("content_html") or ""
        # 第二档：摘要栏与正文栏不再把同一段话显示两遍
        if ai_summary:
            full_body = _strip_summary_prefix(full_body, ai_summary)
            summary = _strip_summary_prefix(summary, ai_summary)
            content_html = _strip_html_summary_prefix(content_html, ai_summary)

        result.append({
            "title": item.get("title_zh") or item.get("title", ""),
            "summary": summary,
            # AI 生成的中文摘要（优先使用翻译后的版本）
            "ai_summary": ai_summary,
            # 摘要来源：extractive（自动提炼）/ fallback（原文节选）/ empty
            "ai_summary_kind": (item.get("ai_summary_kind")
                                or ("extractive" if ai_summary else "empty")),
            "url": item.get("url", ""),
            "source_name": item.get("source_name", ""),
            "published_date": (item.get("published_date") or "")[:10],
            "content_type": item.get("content_type") or "",
            "source_type": item.get("source_type") or "",
            "category": item.get("category", UNCLASSIFIED),
            "merged_sources": item.get("merged_sources") or [],
            "duplicate_count": item.get("duplicate_count", 0),
            "duplicate_urls": item.get("duplicate_urls") or [],
            "cluster_sources": item.get("cluster_sources") or [],
            "fulltext_fetched": item.get("fulltext_fetched"),
            "extract_strategy": item.get("extract_strategy", ""),
            "scoring_matched": item.get("scoring_matched") or {},
            "source_hue": _source_color(item.get("source_name", "")),
            "filter_decision": item.get("filter_decision", ""),
            "confidence_score": item.get("confidence_score", 0),
            # 不截顶的证据强度，用于前端排序/展示（截顶分大量并列满分）
            "raw_score": item.get("raw_score", item.get("confidence_score", 0)),
            "relevance_score": item.get("relevance_score", 0),
            "depth_score": item.get("depth_score", 0),
            "topic_fit": item.get("topic_fit", 0),
            "commercial_score": item.get("commercial_score", 0),
            "authority_tier": item.get("authority_tier", ""),
            "selection_reason": item.get("selection_reason", ""),
            "score_breakdown": item.get("score_breakdown") or {},
            "series_hit": bool(item.get("series_hit")),
            "full_body": full_body,
            # 结构化正文（白名单净化后的 HTML）；前端优先渲染它，
            # 为空时回退到按纯文本分段的旧逻辑
            "content_html": content_html,
            # 供前端如实标注“未翻译”的内容（只看报告实际展示的标题与摘要；
            # 正文按设计不翻译，不参与该标记）
            "untranslated": bool(item.get("title_translated") is False
                                 or item.get("ai_summary_translated") is False),
        })
    return result


def save_weekly_data(items: list[dict], week_str: str):
    """将本周数据保存为独立 JSON 文件"""
    path = REPORTS_DIR / f"data_{week_str}.json"
    atomic_write(path, items)


PARSED_ITEMS_PATH_FALLBACK = DATA_DIR / "parsed_items.json"


def _count_items_by_source() -> dict:
    """统计每个信源最终留下的条目数（优先用最终产物）"""
    for path in (CLASSIFIED_ITEMS_PATH, PARSED_ITEMS_PATH_FALLBACK):
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                counts: dict[str, int] = {}
                for i in data:
                    name = i.get("source_name", "")
                    counts[name] = counts.get(name, 0) + 1
                return counts
            except Exception:
                continue
    return {}


def generate_source_alerts() -> list[dict]:
    """检查哪些启用的信源本周未获取到数据，返回告警列表。"""
    alerts = []
    try:
        with open(SOURCES_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[REPORT] 信源配置读取失败，跳过信源告警: {e}")
        return alerts

    sources = cfg.get("sources", [])
    if not isinstance(sources, list):
        return alerts

    fetch_status = {}
    if FETCH_STATUS_PATH.exists():
        try:
            with open(FETCH_STATUS_PATH, encoding="utf-8") as f:
                fetch_status = json.load(f)
        except Exception:
            pass

    source_health = {}
    if SOURCE_HEALTH_PATH.exists():
        try:
            with open(SOURCE_HEALTH_PATH, encoding="utf-8") as f:
                source_health = json.load(f)
        except Exception:
            pass

    source_item_counts = _count_items_by_source()

    for src in sources:
        if not isinstance(src, dict) or not src.get("enabled", True):
            continue
        name = src.get("name")
        if not name:
            continue
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
                "reason": f"连续失败已进入退避重试（当前间隔 {cons_fails} 轮）",
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

    severity_order = {"high": 0, "medium": 1, "low": 2}
    alerts.sort(key=lambda a: (severity_order.get(a["severity"], 9), a["source_name"]))
    return alerts


def _load_report_items() -> tuple[list[dict], str]:
    """按优先级装载报告数据，返回 (条目, 来源文件)"""
    for path in (TRANSLATED_ITEMS_PATH, ENHANCED_ITEMS_PATH, CLASSIFIED_ITEMS_PATH):
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise ValueError(f"{path.name} 内容不是列表，文件可能已损坏")
            return data, path.name
    raise FileNotFoundError(
        "找不到任何可用的报告数据源（translated_items.json / enhanced_items.json / "
        "classified_items.json 均不存在）。请检查管道前序步骤是否失败。")


def _precompress_reports(week_str: str):
    """预压缩周报数据 JSON 文件"""
    for f in REPORTS_DIR.glob(f"data_{week_str}*.json"):
        gz = precompress(f)
        if gz:
            print(f"[COMPRESS] {gz.name} ({gz.stat().st_size:,} bytes)")


def generate_report():
    items, source_file = _load_report_items()
    print(f"[REPORT] 数据来源: {source_file}（{len(items)} 条）")

    now = datetime.now()
    week_str = get_week_number(now)

    # 计算本周起始和结束日期（周一 ~ 周日）
    monday = now - timedelta(days=now.weekday())
    sunday = monday + timedelta(days=6)
    date_range = f"{monday.strftime('%Y.%m.%d')}-{sunday.strftime('%Y.%m.%d')}"

    # 分离 accepted 和 review；收录内容先做单源/系列配额，再按相关性排序。
    profile = _load_reader_profile()
    quality = _load_quality()
    quotas = quality.get("quotas", {}) or {}
    max_per_source = int(quotas.get("max_per_source", 3) or 3)
    max_total = int(profile.get("max_total_items", 80) or 80)

    accepted_all = [i for i in items if i.get("filter_decision") != "review"]
    accepted_all = _apply_diversity(accepted_all, max_per_source=max_per_source)
    accepted_items = sorted(accepted_all, key=_rank_key, reverse=True)[:max_total]
    review_items = sorted((i for i in items if i.get("filter_decision") == "review"),
                          key=_rank_key, reverse=True)

    # 主题分类（①~⑦）：供侧栏导航、分类计数与分类 JSON 使用。
    groups = group_by_category(accepted_items)

    all_items = accepted_items + review_items

    # 正文图片本地化：只处理最终展示的条目（不给被过滤掉的文章白下载图片）。
    # 失败时保留原外链，不影响报告生成。
    try:
        img_stats = localize_items(all_items)
        if img_stats.get("items_with_html"):
            print(f"[IMAGES] 正文图片: 本地化 {img_stats['images_localized']} 张，"
                  f"复用 {img_stats['images_reused']} 张，失败 {img_stats['images_failed']} 张"
                  f"（新增 {img_stats['bytes'] / 1024:.0f} KB）")
    except Exception as e:  # noqa: BLE001 — 图片是锦上添花，不能拖垮报告
        print(f"[IMAGES] 图片本地化失败，保留原链: {type(e).__name__}: {e}")

    json_items = build_json_items(all_items)

    total_count = len(accepted_items)
    review_count = len(review_items)

    source_alerts = generate_source_alerts()

    # 翻译状态（用于模板展示提示横幅）
    translation_warning = ""
    translation_status_path = DATA_DIR / "translation_status.json"
    if translation_status_path.exists():
        try:
            with open(translation_status_path, encoding="utf-8") as f:
                ts = json.load(f)
            if ts.get("status") in ("unavailable", "partial"):
                translation_warning = ts.get("message", "部分内容未能翻译")
        except Exception:
            pass

    # 主题分类标题与数量（侧栏导航；前缀由模板统一加，这里只保留分类名）
    cat_names = []
    cat_counts = []
    for cat in groups:
        name = re.sub(r"^[★●◐①②③④⑤⑥⑦]\s*", "", cat)
        cat_names.append(name)
        cat_counts.append(len(groups[cat]))

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    template = env.get_template("weekly_report.html")

    html = template.render(
        week_str=week_str,
        date_range=date_range,
        generate_time=now.strftime("%Y-%m-%d %H:%M"),
        total_count=total_count,
        review_count=review_count,
        groups=groups,
        json_items=json_items,
        source_alerts=source_alerts,
        translation_warning=translation_warning,
        cat_names=cat_names,
        cat_counts=cat_counts,
    )

    # ── 渲染成功后才落盘 ──
    # 顺序很关键：data_*.json 是前端按需加载详情的依据，必须先确保 HTML
    # 能成功渲染，否则会出现“新数据 + 旧页面”的不一致
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    save_weekly_data(build_json_items(accepted_items), week_str)
    save_weekly_data(build_json_items(review_items), f"{week_str}_review")
    for cat_idx, (cat_name, cat_items) in enumerate(groups.items()):
        save_weekly_data(build_json_items(cat_items), f"{week_str}_cat_{cat_idx}")

    atomic_write_text(LATEST_REPORT, html)

    print(f"[REPORT] 周报生成完成: {LATEST_REPORT}")
    print(f"[REPORT] 共 {total_count} 条（其中 {review_count} 条待复核）")

    # 清理过期图片目录（保留当前条目引用的目录，避免误删正在用的图）
    try:
        removed = prune_assets(keep_ids={i.get("item_id") for i in all_items if i.get("item_id")})
        if removed:
            print(f"[IMAGES] 清理过期图片目录 {removed} 个")
    except Exception as e:  # noqa: BLE001
        print(f"[IMAGES] 清理过期目录失败: {type(e).__name__}: {e}")

    _precompress_reports(week_str)

    return str(LATEST_REPORT)


if __name__ == "__main__":
    generate_report()
