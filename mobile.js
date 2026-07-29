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
  var _detailCache = {};  // idx (in _currentItems) => full item object
  var _currentWeekData = null;  // full items array for current week
  var _fetchP = null;  // 复用进行中的 fetch

  function loadDetailData(idx, callback) {
    if (_detailCache[idx]) {
      callback(_detailCache[idx]);
      return;
    }
    function afterLoad() {
      callback(_detailCache[idx] || window._currentItems[idx] || null);
    }
    // 首次加载：fetch 当前周的完整数据
    if (!_currentWeekData) {
      if (!_fetchP) {
        var week = window._currentWeek;
        var url = '/reports/data_' + week + '.json';
        _fetchP = fetch(url)
          .then(function(r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
          })
          .then(function(items) {
            _currentWeekData = items;
            // 建立 idx 到完整数据的映射
            var filtered = window._currentItems || [];
            items.forEach(function(item) {
              for (var i = 0; i < filtered.length; i++) {
                var li = window._currentData.items[i] || {};
                if (li.url === item.url) {
                  _detailCache[i] = item;
                  break;
                }
              }
            });
            _fetchP = null;
          })
          .catch(function() {
            _fetchP = null;
          });
      }
      _fetchP.then(afterLoad, afterLoad);
    } else {
      afterLoad();
    }
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

  (function injectBackBtn() {
    var detailPanel = document.querySelector('.detail-panel');
    if (!detailPanel || document.getElementById(BACK_BTN_ID)) return;
    var btn = document.createElement('button');
    btn.className = 'detail-back-btn';
    btn.id = BACK_BTN_ID;
    btn.innerHTML = '\u2190';
    btn.style.display = 'none';
    btn.addEventListener('click', function() {
      detailPanel.classList.remove('m-show');
      btn.style.display = 'none';
    });
    document.body.appendChild(btn);
  })();

  /* =================================================================
   * 辅助函数
   * ================================================================= */

  function esc(s) {
    return ('' + (s || '')).replace(/&/g,'&amp;').replace(/</g,'&lt;')
      .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  function openPanel() { catPanel.classList.add('m-show'); overlay.classList.add('m-show'); }
  function closePanel() { catPanel.classList.remove('m-show'); overlay.classList.remove('m-show'); }

  function buildCatList() {
    var catNames   = window._catNames || [];
    var catPrefixes = window._catPrefixes || [];
    var data       = window._currentData;
    var allCount   = data ? (data.totalCount || 0) : 0;
    var catCounts  = data ? (data.catCounts || []) : [];
    var reviewCount = data ? (data.reviewCount || 0) : 0;

    var html = '<div class="mobile-cat-item active" data-idx="-1">'
      + '<span class="mobile-cat-label">全部</span>'
      + '<span class="mobile-cat-count">' + allCount + '</span></div>';
    for (var i = 0; i < catNames.length; i++) {
      var count  = (catCounts[i] !== undefined) ? catCounts[i] : 0;
      var prefix = catPrefixes[i] || '';
      var label  = prefix ? prefix + ' ' + catNames[i] : catNames[i];
      html += '<div class="mobile-cat-item" data-idx="' + i + '">'
        + '<span class="mobile-cat-label">' + esc(label) + '</span>'
        + '<span class="mobile-cat-count">' + count + '</span></div>';
    }
    if (reviewCount > 0) {
      html += '<div class="mobile-cat-item" data-idx="-2">'
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

    var dp = document.querySelector('.detail-panel');
    if (!dp) return;

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

  var _origRL = window.renderList;
  window.renderList = function() {
    if (typeof _origRL === 'function') _origRL();
    buildCatList();
  };

  var _origAWD = window.applyWeekData;
  window.applyWeekData = function(week) {
    if (typeof _origAWD === 'function') _origAWD(week);
    buildCatList();
    _currentWeekData = null;
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

  console.log('[Mobile] 优化版已激活（按需加载 + transitionend）');
})();
