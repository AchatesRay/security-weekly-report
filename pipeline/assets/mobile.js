(function(){
  'use strict';

  if (window.innerWidth > 768) return;

  var CAT_PANEL_ID = 'mobileCatPanel';
  var OVERLAY_ID   = 'mobileOverlay';
  var NAV_BTN_ID   = 'mobileNavBtn';
  var BACK_BTN_ID  = 'detailBackBtn';
  var LIST_ID      = 'listItems';
  var CAT_BODY_ID  = 'catBody';
  var BREAKPOINT   = 768;

  document.documentElement.classList.add('is-mobile');

  /* =================================================================
   * 详情数据缓存（按需加载）
   * ================================================================= */
  var _detailCache = {};  /* url => fullItem */
  var _fullData = {};     /* catIdx => {url: fullItem} */
  var _fetchP = {};       /* catIdx => Promise */

  /* 独立跟踪移动端分类选择，与桌面端 _currentCat 隔离 */
  var _mobileCat = window._currentCat !== undefined ? window._currentCat : 0;

  function _fetchCatData(catIdx) {
    if (_fullData[catIdx] || _fetchP[catIdx]) return;
    var week = window._currentWeek;
    var suffix = catIdx === 'review' ? '_review' : '_cat_' + catIdx;
    _fetchP[catIdx] = fetch('/reports/data_' + week + suffix + '.json')
      .then(function(r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function(items) {
        var byUrl = {};
        items.forEach(function(fullItem) {
          byUrl[fullItem.url] = fullItem;
          _detailCache[fullItem.url] = fullItem;
        });
        _fullData[catIdx] = byUrl;
        _fetchP[catIdx] = null;
      })
      .catch(function() {
        _fetchP[catIdx] = null;
      });
  }

  function loadDetailData(idx, callback) {
    var item = window._currentItems && window._currentItems[idx];
    if (!item) { callback(null); return; }
    if (_detailCache[item.url]) { callback(_detailCache[item.url]); return; }
    var catIdx = item.filter_decision === 'review' ? 'review'
               : (item._catIdx >= 0 ? String(item._catIdx) : '0');
    function afterLoad() { callback(_detailCache[item.url] || item); }
    if (_fullData[catIdx]) { afterLoad(); return; }
    _fetchCatData(catIdx);
    _fetchP[catIdx].then(afterLoad, afterLoad);
  }

  function renderDetail(item) {
    if (!item || typeof window.selectItemByData !== 'function') return;
    window.selectItemByData(item);
  }

  /* =================================================================
   * DOM 组件创建
   * ================================================================= */

  var navBtn = document.createElement('button');
  navBtn.className = 'mobile-nav-btn';
  navBtn.id = NAV_BTN_ID;
  navBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
    + '<line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/>'
    + '<line x1="3" y1="18" x2="21" y2="18"/></svg>';
  document.body.appendChild(navBtn);

  var catPanel = document.createElement('div');
  catPanel.className = 'mobile-cat-panel';
  catPanel.id = CAT_PANEL_ID;
  catPanel.innerHTML = '<div class="mobile-cat-header">'
    + '<span class="mobile-cat-title">分类</span>'
    + '<button class="mobile-cat-close" id="catCloseBtn">&times;</button></div>'
    + '<div class="mobile-cat-body" id="' + CAT_BODY_ID + '"></div>';
  document.body.appendChild(catPanel);

  var overlay = document.createElement('div');
  overlay.className = 'mobile-overlay';
  overlay.id = OVERLAY_ID;
  document.body.appendChild(overlay);

  /* =================================================================
   * 详情面板懒加载（首次点击文章时动态创建 DOM）
   * ================================================================= */
  function ensureDetailPanel() {
    var panel = document.querySelector('.detail-panel');
    if (panel) return panel;

    var html = '<div class="detail-panel" id="detailPanel">'
      + '<div class="detail-empty" id="detailEmpty">'
        + '<div class="detail-empty-icon">◆ ◆ ◆</div>'
        + '<p>选择一条情报查看详情</p>'
      + '</div>'
      + '<div class="detail-content" id="detailContent" style="display:none">'
        + '<div class="detail-meta" id="detailMeta"></div>'
        + '<h1 class="detail-title" id="detailTitle"></h1>'
        + '<div class="detail-divider"></div>'
        + '<div class="detail-ai-summary" id="detailAiSummary" style="display:none">'
          + '<div class="detail-ai-summary-header">摘要</div>'
          + '<div class="detail-ai-summary-text" id="detailAiSummaryText"></div>'
        + '</div>'
        + '<div class="detail-summary" id="detailSummary"></div>'
        + '<div class="detail-matched-kw" id="detailMatchedKw"></div>'
        + '<div class="detail-tags" id="detailTags"></div>'
        + '<a class="detail-original-link" id="detailLink" href="#" target="_blank">阅读原文</a>'
        + '<div class="detail-footer">'
          + '<p>本报告由安全态势感知系统自动生成 · 信息仅供参考</p>'
        + '</div>'
      + '</div>'
    + '</div>';
    document.body.insertAdjacentHTML('beforeend', html);

    // 重新挂载全局 DOM 引用，使桌面版 _renderDetail 能操作新面板
    window.dEmpty = document.getElementById('detailEmpty');
    window.dContent = document.getElementById('detailContent');
    window.dMeta = document.getElementById('detailMeta');
    window.dTitle = document.getElementById('detailTitle');
    window.dSummary = document.getElementById('detailSummary');
    window.dAiSummary = document.getElementById('detailAiSummary');
    window.dAiSummaryText = document.getElementById('detailAiSummaryText');
    window.dTags = document.getElementById('detailTags');
    window.dMatchedKw = document.getElementById('detailMatchedKw');
    window.dLink = document.getElementById('detailLink');

    // 创建返回按钮
    if (!document.getElementById(BACK_BTN_ID)) {
      var btn = document.createElement('button');
      btn.className = 'detail-back-btn';
      btn.id = BACK_BTN_ID;
      btn.innerHTML = '←';
      btn.style.display = 'none';
      btn.addEventListener('click', function() {
        document.querySelector('.detail-panel').classList.remove('m-show');
        btn.style.display = 'none';
      });
      document.body.appendChild(btn);
    }

    return document.querySelector('.detail-panel');
  }

  /* =================================================================
   * 辅助函数
   * ================================================================= */

  function esc(s) {
    return ('' + (s || '')).replace(/&/g,'&amp;').replace(/</g,'&lt;')
      .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  function openPanel() { buildCatList(); catPanel.classList.add('m-show'); overlay.classList.add('m-show'); }
  function closePanel() { catPanel.classList.remove('m-show'); overlay.classList.remove('m-show'); }

  function buildCatList() {
    var catNames   = window._catNames || [];
    var catPrefixes = window._catPrefixes || [];
    var data       = window._currentData;
    var allCount   = data ? (data.totalCount || 0) : 0;
    var catCounts  = data ? (data.catCounts || []) : [];
    var reviewCount = data ? (data.reviewCount || 0) : 0;

    var cur = _mobileCat;
    var html = '<div class="mobile-cat-item' + (cur === -1 ? ' active' : '') + '" data-idx="-1">'
      + '<span class="mobile-cat-label">全部</span>'
      + '<span class="mobile-cat-count">' + allCount + '</span></div>';
    for (var i = 0; i < catNames.length; i++) {
      var count  = (catCounts[i] !== undefined) ? catCounts[i] : 0;
      var prefix = catPrefixes[i] || '';
      var label  = prefix ? prefix + ' ' + catNames[i] : catNames[i];
      html += '<div class="mobile-cat-item' + (i === cur ? ' active' : '') + '" data-idx="' + i + '">'
        + '<span class="mobile-cat-label">' + esc(label) + '</span>'
        + '<span class="mobile-cat-count">' + count + '</span></div>';
    }
    if (reviewCount > 0) {
      html += '<div class="mobile-cat-item' + (cur === -2 ? ' active' : '') + '" data-idx="-2">'
        + '<span class="mobile-cat-label">待复核</span>'
        + '<span class="mobile-cat-count">' + reviewCount + '</span></div>';
    }
    var body = document.getElementById(CAT_BODY_ID);
    if (body) body.innerHTML = html;
  }

  /* =================================================================
   * 事件委托
   * ================================================================= */

  document.getElementById(CAT_BODY_ID).addEventListener('click', function(e) {
    var item = e.target.closest('.mobile-cat-item');
    if (!item) return;
    var idx = parseInt(item.dataset.idx);
    if (isNaN(idx)) return;
    _mobileCat = idx;
    window._currentCat = idx;
    this.querySelectorAll('.mobile-cat-item').forEach(function(t) { t.classList.remove('active'); });
    item.classList.add('active');
    document.querySelectorAll('.nav-item').forEach(function(n) { n.classList.remove('active'); });
    var dn = document.querySelector('.nav-item[data-idx="' + idx + '"]');
    if (dn) dn.classList.add('active');
    if (typeof window.renderList === 'function') window.renderList();
    var dp = document.querySelector('.detail-panel');
    if (dp) dp.classList.remove('m-show');
    var backBtn = document.getElementById(BACK_BTN_ID);
    if (backBtn) backBtn.style.display = 'none';
    closePanel();
  });

  navBtn.addEventListener('click', openPanel);
  document.getElementById('catCloseBtn').addEventListener('click', closePanel);
  overlay.addEventListener('click', closePanel);

  // 列表点击：先滑入面板，再按需加载数据
  document.getElementById(LIST_ID).addEventListener('click', function(e) {
    var card = e.target.closest('.list-card, .list-item');
    if (!card) return;
    var idx = parseInt(card.dataset.idx);
    if (isNaN(idx)) return;

    // 首次点击时动态创建详情面板
    var dp = ensureDetailPanel();

    // 强制重排，确保 CSS 过渡能捕捉到初始状态（translateX(100%)）
    void dp.offsetHeight;

    // 高亮当前卡片
    this.querySelectorAll('.list-card, .list-item').forEach(function(el) { el.classList.remove('active'); });
    card.classList.add('active');

    // 滑入面板（无内容，避免闪现）
    dp.classList.add('m-show');
    dp.scrollTop = 0;
    var backBtn = document.getElementById(BACK_BTN_ID);
    if (backBtn) backBtn.style.display = 'flex';

    // 等面板滑入完成后，再加载数据
    var onSlideDone = function() {
      dp.removeEventListener('transitionend', onSlideDone);
      // 显示加载中的占位
      var dContent = document.getElementById('detailContent');
      var dEmpty = document.getElementById('detailEmpty');
      if (dContent) dContent.style.display = 'block';
      if (dEmpty) dEmpty.style.display = 'none';
      // 按需加载详情
      loadDetailData(idx, function(item) {
        renderDetail(item);
      });
    };
    dp.addEventListener('transitionend', onSlideDone);
  });

  /* =================================================================
   * 初始化
   * ================================================================= */

  buildCatList();

  // 禁用桌面端 selectItem（它触发冗余 JSON 下载），移动端通过 delegation 自行处理
  window.selectItem = function() {};

  function prefetchCurrentCat() {
    var items = window._currentItems;
    if (!items || !items.length) return;
    var first = items[0];
    var catIdx = first.filter_decision === 'review' ? 'review'
               : (first._catIdx >= 0 ? String(first._catIdx) : '0');
    _fetchCatData(catIdx);
  }

  var _origRL = window.renderList;
  window.renderList = function() {
    window._currentCat = _mobileCat;
    if (typeof _origRL === 'function') _origRL();
    buildCatList();
    prefetchCurrentCat();
  };

  var _origAWD = window.applyWeekData;
  window.applyWeekData = function(week) {
    if (typeof _origAWD === 'function') _origAWD(week);
    buildCatList();
    _fullData = {};
    _fetchP = {};
    _detailCache = {};
  };

  var resizeTimer;
  window.addEventListener('resize', function() {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function() {
      if (window.innerWidth <= BREAKPOINT) {
        document.documentElement.classList.add('is-mobile');
      } else {
        document.documentElement.classList.remove('is-mobile');
        var dp = document.querySelector('.detail-panel');
        if (dp) dp.classList.remove('m-show');
        closePanel();
        var backBtn = document.getElementById(BACK_BTN_ID);
        if (backBtn) backBtn.style.display = 'none';
      }
    }, 300);
  });

})();
