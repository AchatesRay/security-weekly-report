"""
移动版转换 — 桌面 HTML → 剥离详情数据 → 注入移动端 CSS+JS

在 report_generator 之后运行：
  1. Security_Reports.html   → 剥离 full_body（按需加载）+ 注入桌面端详情加载 JS
  2. Security_Reports.html   → 剥离全部详情字段 + 注入移动端 CSS/JS → _mobile.html
"""
import json
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
REPORTS_DIR = PROJECT_DIR / "reports"
MOBILE_CSS_PATH = PROJECT_DIR / "pipeline" / "assets" / "mobile.css"
MOBILE_JS_PATH = PROJECT_DIR / "pipeline" / "assets" / "mobile.js"

# 桌面端剔除全部详情字段（按需加载）
DESKTOP_SKIP_FIELDS = frozenset({
    'full_body', 'summary', 'ai_summary', 'scoring_matched',
})

# 移动端剔除全部详情字段
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
    console.log('[Loader] idx=' + idx + ' catIdx=' + catIdx + ' url=' + (item.url || '').slice(-30));
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


def _remove_detail_panel(html: str) -> str:
    """移除 .detail-panel 元素（移动端在列表页不应包含详情 DOM）"""
    marker = '<div class="detail-panel" id="detailPanel">'
    start = html.find(marker)
    if start < 0:
        return html
    i = start + len(marker)
    depth = 1
    while i < len(html) and depth > 0:
        if html[i:i+4] == '<div' and not html[i+4].isalpha():
            depth += 1
            i += 4
        elif html[i:i+6] == '</div>':
            depth -= 1
            i += 6
        else:
            i += 1
    return html[:start] + html[i:]


def run():
    html_path = REPORTS_DIR / "Security_Reports.html"
    if not html_path.exists():
        print("[MOBILE] Security_Reports.html 不存在，跳过")
        return

    html = html_path.read_text(encoding="utf-8")

    # ── 1. 处理桌面版：仅剥离 full_body + 注入按需加载 JS ──
    items, start, end = _extract_json_array(html, 'var _allItems = ')
    if items:
        stripped = _strip_fields(items, DESKTOP_SKIP_FIELDS)
        desktop_html = _rebuild_json(html, stripped, start, end)
        desktop_html = _inject_detail_loader(desktop_html)
        html_path.write_text(desktop_html, encoding="utf-8")
        desktop_size = len(desktop_html)
        print(f"[CONVERT] 桌面版: {len(html):,} → {desktop_size:,} bytes "
              f"({len(items)} 条, 已剔除 full_body)")
    else:
        desktop_size = len(html)
        print("[CONVERT] 桌面版: 未找到内联 JSON，跳过剥离")

    # ── 2. 处理移动版：剥离全部详情字段 + 注入 CSS/JS ──
    mobile_css = MOBILE_CSS_PATH.read_text(encoding="utf-8") if MOBILE_CSS_PATH.exists() else ""
    mobile_js = MOBILE_JS_PATH.read_text(encoding="utf-8") if MOBILE_JS_PATH.exists() else ""

    mobile_html = desktop_html if items else html
    before_mobile = len(mobile_html)

    # 剥离详情字段（从已剔除 full_body 的桌面版进一步剥离）
    m_items, m_start, m_end = _extract_json_array(mobile_html, 'var _allItems = ')
    if m_items:
        m_stripped = _strip_fields(m_items, MOBILE_SKIP_FIELDS)
        mobile_html = _rebuild_json(mobile_html, m_stripped, m_start, m_end)

    # 注入移动端 CSS
    if mobile_css:
        mobile_html = mobile_html.replace('</style>', mobile_css + '\n</style>', 1)

    # 注入移动端 JS
    if mobile_js:
        last_se = mobile_html.rfind('</script>')
        if last_se > 0:
            mobile_html = mobile_html[:last_se] + '\n' + mobile_js + '\n' + mobile_html[last_se:]

    # 移除详情面板 DOM（点击文章时再动态创建，减少初始 HTML 体积）
    mobile_html = _remove_detail_panel(mobile_html)

    mobile_path = REPORTS_DIR / "Security_Reports_mobile.html"
    mobile_path.write_text(mobile_html, encoding="utf-8")
    print(f"[CONVERT] 移动版: {before_mobile:,} → {len(mobile_html):,} bytes")

    # ── 3. 预压缩 HTML 文件 ──
    try:
        from ..utils import precompress
        for f in [html_path, mobile_path]:
            gz = precompress(f)
            if gz:
                print(f"[COMPRESS] {gz.name} ({gz.stat().st_size:,} bytes)")
    except ImportError:
        pass


if __name__ == '__main__':
    run()