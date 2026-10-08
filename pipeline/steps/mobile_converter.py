"""移动版转换 — 桌面 HTML → 剥离详情数据 → 注入移动端 CSS+JS

在 report_generator 之后运行：
  1. Security_Reports.html   → 剥离详情字段（按需加载）+ 注入桌面端详情加载 JS
  2. Security_Reports.html   → 剥离全部详情字段 + 注入移动端 CSS/JS → _mobile.html

2026-09-29 修复：
  1. **详情面板移除方式**：原先用“逐字符数 <div> / </div> 配平”的方式切掉详情
     面板。该方式不区分属性、注释与 <script> 内的字符串，一旦面板内出现脚本
     字符串就会计数错位，产出结构损坏的 HTML。现在模板里加了显式标记
     <!--DETAIL_PANEL_START--> / <!--DETAIL_PANEL_END-->，按标记切片；
     标记缺失时回退到旧算法并给出明确警告。
  2. **内联 JSON 未找到时静默降级**：原先只打印一行提示就继续，可能产出未剥离
     详情的巨大页面。现在给出明确的 WARNING 并说明后果。
  3. HTML 输出改为原子写入，避免中断留下半个文件被浏览器缓存。
"""

import json
import re
from pathlib import Path

from ..utils import atomic_write_text, precompress

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
REPORTS_DIR = PROJECT_DIR / "reports"
MOBILE_CSS_PATH = PROJECT_DIR / "pipeline" / "assets" / "mobile.css"
MOBILE_JS_PATH = PROJECT_DIR / "pipeline" / "assets" / "mobile.js"

DETAIL_START = "<!--DETAIL_PANEL_START-->"
DETAIL_END = "<!--DETAIL_PANEL_END-->"

# 详情字段（按需从 data_*.json 加载，不内联到页面）
DESKTOP_SKIP_FIELDS = frozenset({
    'full_body', 'summary', 'ai_summary', 'scoring_matched',
})
MOBILE_SKIP_FIELDS = frozenset({
    'full_body', 'summary', 'ai_summary', 'scoring_matched',
})


def _extract_json_array(html: str, marker: str) -> tuple:
    """提取 JS 中 `marker` 后的 JSON 数组，返回 (items_list, start, end)"""
    start = html.find(marker)
    if start < 0:
        return None, -1, -1
    start += len(marker)

    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(html)):
        c = html[i]
        if esc:
            esc = False
            continue
        if c == '\\' and in_str:
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                try:
                    items = json.loads(html[start:i+1])
                    return items, start, i+1
                except json.JSONDecodeError:
                    return None, -1, -1
    return None, -1, -1


def _rebuild_json(html: str, items: list, start: int, end: int) -> str:
    """替换 HTML 中 [start:end) 范围为新 JSON"""
    new_json = json.dumps(items, ensure_ascii=False, separators=(',', ':'))
    return html[:start] + new_json + html[end:]


def _strip_fields(items: list, skip_fields: set) -> list:
    """从每个 dict 中剔除指定字段"""
    result = []
    for item in items:
        if isinstance(item, dict):
            result.append({k: v for k, v in item.items() if k not in skip_fields})
        else:
            result.append(item)
    return result


def _inject_detail_loader(html: str) -> str:
    """注入桌面端按需加载全部详情字段的 JS"""
    loader_js = """
(function(){
  var _fullData = {};   /* catIdx => {url: fullItem} */
  var _fetchP = {};     /* catIdx => Promise */
  var _reqId = 0;
  window.selectItem = function(idx) {
    _currentSel = idx;
    var cards = document.querySelectorAll('.list-card');
    cards.forEach(function(el) { el.classList.remove('active'); });
    var card = document.querySelector('.list-card[data-idx="' + idx + '"]');
    if (card) card.classList.add('active');
    loadBody(idx);
  };
  function loadBody(idx) {
    var item = window._currentItems && window._currentItems[idx];
    if (!item) return;
    if (item.full_body && item.full_body.length > 100) {
      if (typeof window._renderDetail === 'function') {
        try { window._renderDetail(item); } catch(e) { console.error('[Loader] render error:', e); }
      }
      return;
    }
    var localReq = ++_reqId;
    /* 确定分类索引：待复核走 review 文件，正常走 cat 文件 */
    var catIdx = item.filter_decision === 'review' ? 'review'
               : (item._catIdx >= 0 ? String(item._catIdx) : '0');
    function doRender() {
      if (localReq !== _reqId) return;
      var catData = _fullData[catIdx];
      if (!catData) return;
      var fullItem = catData[item.url];
      if (!fullItem) return;
      item.full_body = fullItem.full_body;
      item.summary = fullItem.summary;
      item.ai_summary = fullItem.ai_summary;
      item.scoring_matched = fullItem.scoring_matched;
      if (_currentSel === idx && typeof window._renderDetail === 'function') {
        try { window._renderDetail(item); }
        catch(e) { console.error('[Loader] _renderDetail error:', e); }
      }
    }
    if (_fullData[catIdx]) { doRender(); return; }
    if (!_fetchP[catIdx]) {
      var week = window._currentWeek;
      var suffix = catIdx === 'review' ? '_review' : '_cat_' + catIdx;
      _fetchP[catIdx] = fetch('/reports/data_' + week + suffix + '.json')
        .then(function(r) { return r.json(); })
        .then(function(items) {
          var byUrl = {};
          items.forEach(function(it) { byUrl[it.url] = it; });
          _fullData[catIdx] = byUrl;
          _fetchP[catIdx] = null;
        })
        .catch(function() { _fetchP[catIdx] = null; });
    }
    _fetchP[catIdx].then(doRender, doRender);
  }
})();
"""
    last_script = html.rfind('</script>')
    if last_script > 0:
        html = html[:last_script] + loader_js + html[last_script:]
    return html


def _remove_detail_panel_by_depth(html: str) -> str:
    """回退方案：按 <div> / </div> 配平切掉详情面板（仅在标记缺失时使用）"""
    marker = '<div class="detail-panel" id="detailPanel">'
    start = html.find(marker)
    if start < 0:
        return html
    i = start + len(marker)
    depth = 1
    while i < len(html) and depth > 0:
        if html[i:i+4] == '<div' and i + 4 < len(html) and not html[i+4].isalpha():
            depth += 1
            i += 4
        elif html[i:i+6] == '</div>':
            depth -= 1
            i += 6
        else:
            i += 1
    return html[:start] + html[i:]


def _remove_detail_panel(html: str) -> str:
    """移除 .detail-panel 元素（移动端点击文章时再动态创建，减少初始体积）"""
    start = html.find(DETAIL_START)
    end = html.find(DETAIL_END)
    if start >= 0 and end > start:
        return html[:start] + html[end + len(DETAIL_END):]
    print("[CONVERT] 警告: 模板中未找到详情面板标记 "
          f"({DETAIL_START} / {DETAIL_END})，改用旧的结构配平方式移除；"
          "若面板内含脚本字符串，可能产生结构损坏的 HTML")
    return _remove_detail_panel_by_depth(html)


def run():
    html_path = REPORTS_DIR / "Security_Reports.html"
    if not html_path.exists():
        print("[MOBILE] Security_Reports.html 不存在，跳过")
        return None

    html = html_path.read_text(encoding="utf-8")

    # ── 1. 桌面版：剥离详情字段 + 注入按需加载 JS ──
    # 注意用 `is not None` 判断：空报告时 _allItems 渲染为 []，
    # 用真值判断会把它误当成“未找到内联 JSON”
    items, start, end = _extract_json_array(html, 'var _allItems = ')
    desktop_html = html
    if items is not None:
        stripped = _strip_fields(items, DESKTOP_SKIP_FIELDS)
        desktop_html = _rebuild_json(html, stripped, start, end)
        desktop_html = _inject_detail_loader(desktop_html)
        atomic_write_text(html_path, desktop_html)
        print(f"[CONVERT] 桌面版: {len(html):,} → {len(desktop_html):,} bytes "
              f"({len(items)} 条, 已剔除详情字段，改为按需加载)")
    else:
        print("[CONVERT] 警告: 未在周报中找到内联 JSON（var _allItems = ），"
              "桌面版详情将全部内联，体积偏大；请检查模板是否被改动")

    # ── 2. 移动版：进一步剥离 + 注入 CSS/JS ──
    mobile_css = MOBILE_CSS_PATH.read_text(encoding="utf-8") if MOBILE_CSS_PATH.exists() else ""
    mobile_js = MOBILE_JS_PATH.read_text(encoding="utf-8") if MOBILE_JS_PATH.exists() else ""
    if not mobile_css:
        print("[CONVERT] 警告: 未找到 mobile.css，移动版样式将不完整")
    if not mobile_js:
        print("[CONVERT] 警告: 未找到 mobile.js，移动版交互将不可用")

    mobile_html = desktop_html
    before_mobile = len(mobile_html)

    m_items, m_start, m_end = _extract_json_array(mobile_html, 'var _allItems = ')
    if m_items is not None:
        m_stripped = _strip_fields(m_items, MOBILE_SKIP_FIELDS)
        mobile_html = _rebuild_json(mobile_html, m_stripped, m_start, m_end)

    if mobile_css:
        mobile_html = mobile_html.replace('</style>', mobile_css + '\n</style>', 1)

    if mobile_js:
        last_se = mobile_html.rfind('</script>')
        if last_se > 0:
            mobile_html = mobile_html[:last_se] + '\n' + mobile_js + '\n' + mobile_html[last_se:]

    mobile_html = _remove_detail_panel(mobile_html)

    mobile_path = REPORTS_DIR / "Security_Reports_mobile.html"
    atomic_write_text(mobile_path, mobile_html)
    print(f"[CONVERT] 移动版: {before_mobile:,} → {len(mobile_html):,} bytes")

    # ── 3. 预压缩 HTML ──
    for f in (html_path, mobile_path):
        gz = precompress(f)
        if gz:
            print(f"[COMPRESS] {gz.name} ({gz.stat().st_size:,} bytes)")

    return mobile_path


if __name__ == '__main__':
    run()
