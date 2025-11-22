(function(){
  // ============================================
  // RedPen Editor Bootstrap v2.0.1
  // Last updated: 2025-11-22
  // ============================================
  var REDPEN_EDITOR_VERSION = '2.0.1';
  console.log('[RedPen Editor] Version: ' + REDPEN_EDITOR_VERSION);
  
  // Checks if editor mode is enabled
  function hasEditorFlag(){
    try {
      var g = window;
      var usp = new URLSearchParams(g.location.search || '');
      var hsp = new URLSearchParams((g.location.hash || '').replace(/^#/, ''));
      var qp = usp.get('editor');
      var hp = hsp.get('editor');
      return (qp === '1' || qp === 'true') || (hp === '1' || hp === 'true') || g.REDPEN_EDITOR === true;
    } catch (e) {
      return window.REDPEN_EDITOR === true;
    }
  }

  var MARKER_SELECTOR = '.circle';

  // Parse coords from dataset string like "[x,y]" or "x,y"
  function parseCoords(str){
    if (!str) return undefined;
    var s = String(str).trim();
    s = s.replace(/^\[/, '').replace(/\]$/, '').replace(/\s+/g, '');
    var parts = s.split(',').map(function(v){ return parseInt(v, 10); });
    if (parts.length >= 2 && !isNaN(parts[0]) && !isNaN(parts[1])) return [parts[0], parts[1]];
    return undefined;
  }

  function ensureSelectedStyleInjected(){
    if (document.getElementById('redpen-editor-selected-style')) return;
    var style = document.createElement('style');
    style.id = 'redpen-editor-selected-style';
    style.textContent = '.is-selected{ outline: 2px solid #DC143C; outline-offset: 2px; }';
    document.head && document.head.appendChild(style);
  }

  function selectMarker(el){
    try {
      document.querySelectorAll(MARKER_SELECTOR + '.is-selected').forEach(function(n){ n.classList.remove('is-selected'); });
      if (el && el.classList) el.classList.add('is-selected');
    } catch (e) { /* noop */ }
  }

  function clearSelection(){
    try {
      document.querySelectorAll(MARKER_SELECTOR + '.is-selected').forEach(function(n){ n.classList.remove('is-selected'); });
    } catch (e) { /* noop */ }
    if (window.RedPenEditor && window.RedPenEditor.state) {
      window.RedPenEditor.state.ui.selectedAnnotationId = null;
      window.RedPenEditor.state.draft.id = undefined;
    }
  }

  function normalizeId(raw){
    var s = (raw || '').trim();
    return s === '' ? undefined : s;
  }

  function init(){
    console.log('[RedPen Editor] Initializing editor mode (v' + REDPEN_EDITOR_VERSION + ')');
    
    // Initialize global state if missing
    if (!window.RedPenEditor) window.RedPenEditor = {};
    if (!window.RedPenEditor.state) {
      window.RedPenEditor.state = {
        editorMode: true,
        ui: { selectedAnnotationId: null, lastAutoGeneralContent: undefined },
        draft: { id: undefined, annType: 'comment', content: '', coords: undefined },
        cache: { general: null },
        editing: { mode: 'none' },
        baseline: null,
        flags: { allowCoordChangeWithoutPrompt: false, mock: (window.REDPEN_MOCKS === true) },
        autoContent: { general: undefined },
        auth: { isAuthenticated: false, userId: undefined, username: undefined, csrfToken: undefined },
        page: { pageId: undefined, serverPageSha: undefined, origW: undefined, origH: undefined, annotations: [] }
      };
    } else {
      // ensure cache exists
      window.RedPenEditor.state.editorMode = true;
      if (!window.RedPenEditor.state.cache) window.RedPenEditor.state.cache = { general: null };
      if (!window.RedPenEditor.state.ui) window.RedPenEditor.state.ui = { selectedAnnotationId: null, lastAutoGeneralContent: undefined };
      if (typeof window.RedPenEditor.state.ui.lastAutoGeneralContent === 'undefined') {
        window.RedPenEditor.state.ui.lastAutoGeneralContent = undefined;
      }
      if (!window.RedPenEditor.state.editing) window.RedPenEditor.state.editing = { mode: 'none' };
      if (!('baseline' in window.RedPenEditor.state)) window.RedPenEditor.state.baseline = null;
      if (!window.RedPenEditor.state.flags) window.RedPenEditor.state.flags = { allowCoordChangeWithoutPrompt: false, mock: (window.REDPEN_MOCKS === true) };
      if (typeof window.RedPenEditor.state.flags.mock === 'undefined') window.RedPenEditor.state.flags.mock = (window.REDPEN_MOCKS === true);
      if (!window.RedPenEditor.state.autoContent) window.RedPenEditor.state.autoContent = { general: undefined };
      if (!window.RedPenEditor.state.auth) window.RedPenEditor.state.auth = { isAuthenticated: false, userId: undefined, username: undefined, csrfToken: undefined };
      if (!window.RedPenEditor.state.page) window.RedPenEditor.state.page = { pageId: undefined, serverPageSha: undefined, origW: undefined, origH: undefined, annotations: [] };
    }

    // ===== HELPER FUNCTIONS - DEFINED EARLY =====
    // These must be defined before any code that uses them
    
    function snapshotFromDraft(d){
      if (!d) d = {};
      var id = (d.id || '').trim ? (d.id || '').trim() : d.id;
      id = (id === '' || typeof id === 'undefined') ? undefined : id;
      var res = { id: id, annType: d.annType || 'comment', content: typeof d.content === 'string' ? d.content : '' };
      if (Array.isArray(d.coords) && d.coords.length === 2 && Number.isFinite(d.coords[0]) && Number.isFinite(d.coords[1])) {
        res.coords = [d.coords[0], d.coords[1]];
      }
      return res;
    }
    
    function isDirty(current, baseline){
      if (!baseline) return false;
      if (!current) return false;
      if (current.annType !== baseline.annType) return true;
      if ((current.content || '') !== (baseline.content || '')) return true;
      if (current.annType === 'general') return false;
      var c1 = current.coords, c2 = baseline.coords;
      if (!c1 && !c2) return false;
      if (!c1 || !c2) return true;
      return !(c1[0] === c2[0] && c1[1] === c2[1]);
    }
    
    function confirmLoseChanges(){
      return window.confirm('У вас есть несохранённые изменения. Отменить их?');
    }
    
    function beginEditingExisting(data){
      console.log('[RedPen Editor] beginEditingExisting called');
      var snap = snapshotFromDraft(data);
      window.RedPenEditor.state.editing.mode = 'existing';
      window.RedPenEditor.state.baseline = snap;
      window.RedPenEditor.state.flags.allowCoordChangeWithoutPrompt = false;
    }
    
    function beginCreatingNew(initialDraft){
      console.log('[RedPen Editor] beginCreatingNew called');
      var snap = snapshotFromDraft(initialDraft);
      window.RedPenEditor.state.editing.mode = 'new';
      window.RedPenEditor.state.baseline = snap;
      window.RedPenEditor.state.flags.allowCoordChangeWithoutPrompt = true;
    }

    // Expose utilities for panel usage
    window.RedPenEditor.clearSelection = clearSelection;

    // Find right block container
    var container = document.getElementById('global-comment-container');
    if (!container) return;

    // ... existing code ...
                : (Array.isArray(window.allAnns) ? window.allAnns : null);
      if (anns && anns.length){
        for (var i=0;i<anns.length;i++){
          var a = anns[i];
          if (a && a.annType === 'general'){
            var id = normalizeId(a.id);
            var text = typeof a.text === 'string' ? a.text : '';
            return { id: id, content: text };
          }
        }
      }
    } catch(e){ /* noop */ }
    // Fallback: from DOM global comment
    try {
      var gc = document.getElementById('global-comment');
      var text = gc ? (gc.textContent || gc.innerText || '') : '';
      if (text && text.trim().length > 0) {
        return { id: undefined, content: text };
      }
    } catch(e){ /* noop */ }
    return null;
  }

  function initGeneralCache(){
    try {
      var el = document.getElementById('global-comment');
      if (!el) return;

      function maybeValidText(t){
        if (!t) return false;
        var tt = String(t).trim();
        return tt.length > 0 && tt !== 'Загрузка…' && tt !== 'Загрузка...';
      }

      function applyIfAppropriate(t){
        if (!window.RedPenEditor || !window.RedPenEditor.state) return;
        // Normalize id for general as unknown
        window.RedPenEditor.state.cache.general = { id: undefined, content: t };
        window.RedPenEditor.state.autoContent.general = t;

        var draft = (window.RedPenEditorPanel && window.RedPenEditorPanel.getDraft)
          ? window.RedPenEditorPanel.getDraft()
          : (window.RedPenEditor.state.draft || {});
        var taVal = (draft && typeof draft.content === 'string') ? draft.content : '';
        var annType = draft.annType || (window.RedPenEditor.state.draft && window.RedPenEditor.state.draft.annType);
        var prevAuto = window.RedPenEditor.state.ui.lastAutoGeneralContent;

        if (annType === 'general' && (taVal.trim() === '' || taVal.trim() === 'Загрузка…' || taVal.trim() === 'Загрузка...' || taVal === prevAuto)) {
          var newDraft = Object.assign({}, window.RedPenEditor.state.draft, { id: undefined, annType: 'general', content: t, coords: undefined });
          window.RedPenEditor.state.draft = newDraft;
          // Since we just loaded existing general, begin editing existing
          beginEditingExisting({ id: undefined, annType: 'general', content: t, coords: undefined });
          if (window.RedPenEditorPanel && window.RedPenEditorPanel.setDraft) {
            window.RedPenEditorPanel.setDraft(window.RedPenEditor.state.draft);
          }
          window.RedPenEditor.state.ui.lastAutoGeneralContent = t;
        }
      }

      var initial = (el.textContent || el.innerText || '');
      if (maybeValidText(initial)) {
        applyIfAppropriate(String(initial).trim());
        return;
      }

      var observer = new MutationObserver(function(){
        var txt = el.textContent || el.innerText || '';
        if (maybeValidText(txt)) {
          applyIfAppropriate(String(txt).trim());
          try { observer.disconnect(); } catch(e) { /* noop */ }
        }
      });
      observer.observe(el, { attributes: true, childList: true, characterData: true, subtree: true });
    } catch(e) { /* noop */ }
  }

  function init(){
    // Initialize global state if missing
    if (!window.RedPenEditor) window.RedPenEditor = {};
    if (!window.RedPenEditor.state) {
      window.RedPenEditor.state = {
        editorMode: true,
        ui: { selectedAnnotationId: null, lastAutoGeneralContent: undefined },
        draft: { id: undefined, annType: 'comment', content: '', coords: undefined },
        cache: { general: null },
        editing: { mode: 'none' },
        baseline: null,
        flags: { allowCoordChangeWithoutPrompt: false, mock: (window.REDPEN_MOCKS === true) },
        autoContent: { general: undefined },
        auth: { isAuthenticated: false, userId: undefined, username: undefined, csrfToken: undefined },
        page: { pageId: undefined, serverPageSha: undefined, origW: undefined, origH: undefined, annotations: [] }
      };
    } else {
      // ... existing state merging code ...
    }

    // ===== DEFINE HELPER FUNCTIONS EARLY =====
    function snapshotFromDraft(d){
      if (!d) d = {};
      var id = (d.id || '').trim ? (d.id || '').trim() : d.id;
      id = (id === '' || typeof id === 'undefined') ? undefined : id;
      var res = { id: id, annType: d.annType || 'comment', content: typeof d.content === 'string' ? d.content : '' };
      if (Array.isArray(d.coords) && d.coords.length === 2 && Number.isFinite(d.coords[0]) && Number.isFinite(d.coords[1])) {
        res.coords = [d.coords[0], d.coords[1]];
      }
      return res;
    }
    
    function isDirty(current, baseline){
      if (!baseline) return false;
      if (!current) return false;
      if (current.annType !== baseline.annType) return true;
      if ((current.content || '') !== (baseline.content || '')) return true;
      if (current.annType === 'general') return false;
      var c1 = current.coords, c2 = baseline.coords;
      if (!c1 && !c2) return false;
      if (!c1 || !c2) return true;
      return !(c1[0] === c2[0] && c1[1] === c2[1]);
    }
    
    function confirmLoseChanges(){
      return window.confirm('У вас есть несохранённые изменения. Отменить их?');
    }
    
    function beginEditingExisting(data){
      var snap = snapshotFromDraft(data);
      window.RedPenEditor.state.editing.mode = 'existing';
      window.RedPenEditor.state.baseline = snap;
      window.RedPenEditor.state.flags.allowCoordChangeWithoutPrompt = false;
    }
    
    function beginCreatingNew(initialDraft){
      var snap = snapshotFromDraft(initialDraft);
      window.RedPenEditor.state.editing.mode = 'new';
      window.RedPenEditor.state.baseline = snap;
      window.RedPenEditor.state.flags.allowCoordChangeWithoutPrompt = true;
    }

    // Expose utilities for panel usage
    window.RedPenEditor.clearSelection = clearSelection;

    // Find right block container
    var container = document.getElementById('global-comment-container');
    if (!container) return;

    // ... rest of init code ...
    Array.prototype.slice.call(container.children).forEach(function(ch){
      if (ch && ch.style) ch.style.display = 'none';
    });

    // Mount editor panel
    if (window.RedPenEditorPanel && typeof window.RedPenEditorPanel.mount === 'function') {
      window.RedPenEditorPanel.mount(container);
      // After mount, sync the panel with initial draft state
      if (window.RedPenEditorPanel.setDraft) {
        window.RedPenEditorPanel.setDraft(window.RedPenEditor.state.draft);
      }
    }

    // Inject selection style (editorMode only)
    ensureSelectedStyleInjected();

    // Start in general mode with empty content; fill later when real content becomes available
    clearSelection();
    window.RedPenEditor.state.ui.selectedAnnotationId = null;
    window.RedPenEditor.state.draft = Object.assign({}, window.RedPenEditor.state.draft, {
      id: undefined,
      annType: 'general',
      content: '',
      coords: undefined
    });
    if (window.RedPenEditorPanel && window.RedPenEditorPanel.setDraft) {
      window.RedPenEditorPanel.setDraft(window.RedPenEditor.state.draft);
      if (window.RedPenEditorPanel.revalidate) try { window.RedPenEditorPanel.revalidate(); } catch(e) {}
    }
    try {
      var typeEl = document.getElementById('redpen-type');
      if (typeEl) typeEl.value = 'general';
      var coordEl = document.getElementById('redpen-coord');
      if (coordEl) { coordEl.value=''; coordEl.disabled = true; coordEl.parentElement && (coordEl.parentElement.style.display='none'); }
    } catch(e){ /* noop */ }
    // We're in new general draft until real content appears
    beginCreatingNew({ id: undefined, annType: 'general', content: '', coords: undefined });

    // Initialize general cache lazily when content is ready
    initGeneralCache();

    // Initialize editing state after start
    window.RedPenEditor.state.editing.mode = 'none';
    window.RedPenEditor.state.baseline = null;
    window.RedPenEditor.state.flags.allowCoordChangeWithoutPrompt = false;

    // Delegated click handling
    var imageContainer = document.getElementById('image-container');
    var pageImage = document.getElementById('page-image');
    if (imageContainer && pageImage) {
      imageContainer.addEventListener('click', function(ev){
        try {
          var target = ev.target;
          var marker = target && target.closest ? target.closest(MARKER_SELECTOR) : null;
          if (marker) {
            handleMarkerClick(marker, ev);
          } else {
            handleEmptyClick(ev);
          }
        } catch (e) { /* noop */ }
      }, { passive: true, capture: true });
    }

    // Type change handler exposed globally for panel to delegate
    window.RedPenEditor.onTypeChange = function(newType, prevType){
      var state = window.RedPenEditor.state;
      var current = (window.RedPenEditorPanel && window.RedPenEditorPanel.getDraft) ? window.RedPenEditorPanel.getDraft() : state.draft;
      var baseline = state.baseline;
      var mode = (state.editing && state.editing.mode) || 'none';

      // If switching to general
      if (newType === 'general') {
        // If moving to an existing general and current is dirty, confirm
        var gen = state.cache && state.cache.general;
        if ((mode === 'existing' || mode === 'new') && isDirty(current, baseline)) {
          if (!confirmLoseChanges()) return false;
        }
        // Prepare draft for general
        if (gen) {
          state.draft = { id: gen.id, annType: 'general', content: gen.content, coords: undefined };
          beginEditingExisting(state.draft);
        } else {
          // no general yet: stay in new with user's current text
          state.draft = Object.assign({}, state.draft, { id: undefined, annType: 'general', coords: undefined });
          beginCreatingNew({ id: undefined, annType: 'general', content: state.draft.content || '', coords: undefined });
        }
        if (window.RedPenEditorPanel && window.RedPenEditorPanel.setDraft) window.RedPenEditorPanel.setDraft(state.draft);
        return true;
      }

      // Switching away from general or between non-general types
      if (mode === 'existing' && isDirty(current, baseline)) {
        if (!confirmLoseChanges()) return false;
      }
      if (mode === 'new' && isDirty(current, baseline)) {
        if (!confirmLoseChanges()) return false;
      }
      // Stay in new mode if current draft has no id
      var next = Object.assign({}, state.draft, { annType: newType });
      state.draft = next;
      if (!next.id) {
        beginCreatingNew({ id: undefined, annType: newType, content: next.content || '', coords: next.coords });
      } else {
        beginEditingExisting({ id: next.id, annType: newType, content: next.content || '', coords: next.coords });
      }
      if (window.RedPenEditorPanel && window.RedPenEditorPanel.setDraft) window.RedPenEditorPanel.setDraft(state.draft);
      return true;
    };

    // Helper: snapshot and dirty check
    function snapshotFromDraft(d){
      if (!d) d = {};
      var id = (d.id || '').trim ? (d.id || '').trim() : d.id;
      id = (id === '' || typeof id === 'undefined') ? undefined : id;
      var res = { id: id, annType: d.annType || 'comment', content: typeof d.content === 'string' ? d.content : '' };
      if (Array.isArray(d.coords) && d.coords.length === 2 && Number.isFinite(d.coords[0]) && Number.isFinite(d.coords[1])) {
        res.coords = [d.coords[0], d.coords[1]];
      }
      return res;
    }
    function isDirty(current, baseline){
      if (!baseline) return false;
      if (!current) return false;
      if (current.annType !== baseline.annType) return true;
      if ((current.content || '') !== (baseline.content || '')) return true;
      // coords ignored for general
      if (current.annType === 'general') return false;
      var c1 = current.coords, c2 = baseline.coords;
      if (!c1 && !c2) return false;
      if (!c1 || !c2) return true;
      return !(c1[0] === c2[0] && c1[1] === c2[1]);
    }
    function confirmLoseChanges(){
      return window.confirm('У вас есть несохранённые изменения. Отменить их?');
    }
    function beginEditingExisting(data){
      var snap = snapshotFromDraft(data);
      window.RedPenEditor.state.editing.mode = 'existing';
      window.RedPenEditor.state.baseline = snap;
      window.RedPenEditor.state.flags.allowCoordChangeWithoutPrompt = false;
    }
    function beginCreatingNew(initialDraft){
      var snap = snapshotFromDraft(initialDraft);
      window.RedPenEditor.state.editing.mode = 'new';
      window.RedPenEditor.state.baseline = snap;
      window.RedPenEditor.state.flags.allowCoordChangeWithoutPrompt = true;
    }

    // Expose helpers for panel
    window.RedPenEditor._isDirty = isDirty;
    window.RedPenEditor._snapshot = snapshotFromDraft;

    // Simple local markers module
    window.RedPenEditor.markers = window.RedPenEditor.markers || (function(){
      function ensureContainer(){
        var host = document.getElementById('overlay-container') || document.getElementById('image-container');
        var img = document.getElementById('page-image');
        // initialize page info
        try {
          var st = window.RedPenEditor.state;
          st.page = st.page || { pageId: undefined, origW: undefined, origH: undefined, annotations: [] };
          if (!st.page.origW || !st.page.origH) {
            if (img && img.naturalWidth && img.naturalHeight) {
              st.page.origW = img.naturalWidth;
              st.page.origH = img.naturalHeight;
            }
          }
        } catch(e){ /* noop */ }
        return { host: host, img: img };
      }
      function toClientOffsets(coords){
        if (!Array.isArray(coords) || coords.length < 2) return [0,0];
        var ctx = ensureContainer();
        var img = ctx.img;
        if (!img) return [coords[0], coords[1]];
        var w = img.getBoundingClientRect ? img.getBoundingClientRect().width : img.width;
        var h = img.getBoundingClientRect ? img.getBoundingClientRect().height : img.height;
        var st = window.RedPenEditor.state;
        var ow = (st.page && st.page.origW) || img.naturalWidth || w;
        var oh = (st.page && st.page.origH) || img.naturalHeight || h;
        var scaleX = w / (ow || 1);
        var scaleY = h / (oh || 1);
        return [coords[0] * scaleX, coords[1] * scaleY];
      }
      function upsert(ann){
        if (!ann || !ann.id) return;
        var ctx = ensureContainer();
        var host = ctx.host;
        if (!host) return;
        var el = document.getElementById(ann.id);
        var isNew = false;
        if (!el) {
          el = document.createElement('div');
          el.id = ann.id;
          el.className = (el.className ? el.className + ' ' : '') + 'circle';
          host.appendChild(el);
          isNew = true;
        }
        // dataset
        try {
          el.dataset.annType = ann.annType || 'comment';
          el.dataset.text = ann.text || '';
          if (Array.isArray(ann.coords)) el.dataset.coords = JSON.stringify(ann.coords);
        } catch(e){ /* noop */ }
        // size and position
        var sizeMap = { main: 100, comment: 50, small: 25 };
        var d = sizeMap[ann.annType] || 50;
        el.style.position = 'absolute';
        el.style.width = d + 'px';
        el.style.height = d + 'px';
        el.style.borderRadius = '50%';
        el.style.cursor = 'pointer';
        el.style.transform = 'translateX(-50%)';
        var color = ann.annType === 'main' ? '#DC143C' : '#0000FF';
        el.style.background = 'radial-gradient(circle, '+color+'80 0%, '+color+'40 50%, '+color+'00 100%)';
        var lt = [0,0];
        if (Array.isArray(ann.coords)) {
          lt = toClientOffsets(ann.coords);
        }
        el.style.left = lt[0] + 'px';
        // center vertically around point roughly similar to viewer
        el.style.top = (lt[1] - d/2) + 'px';

        // --- Popup creation/update for hover ---
        // general has no markers/popups
        if (ann.annType !== 'general') {
          var cx = lt[0];
          var cy = lt[1];
          var popupId = 'popup-' + ann.id;
          var existing = document.getElementById(popupId);

          function attachHoverHandlers(circleEl, popupEl){
            if (!circleEl || !popupEl) return;
            // Avoid duplicate listeners: remove previous if any by cloning
            if (isNew) {
              circleEl.addEventListener('mouseenter', function(){
                if (popupEl.dataset.clickShown !== 'true') {
                  popupEl.style.display = 'block';
                  popupEl.dataset.hoverShown = 'true';
                }
              });
              circleEl.addEventListener('mouseleave', function(){
                if (popupEl.dataset.hoverShown === 'true' && popupEl.dataset.clickShown !== 'true') {
                  setTimeout(function(){
                    if (popupEl.dataset.popupHover !== 'true') {
                      popupEl.style.display = 'none';
                      popupEl.dataset.hoverShown = 'false';
                    }
                  }, 50);
                }
              });
              popupEl.addEventListener('mouseenter', function(){ popupEl.dataset.popupHover = 'true'; });
              popupEl.addEventListener('mouseleave', function(){
                popupEl.dataset.popupHover = 'false';
                if (popupEl.dataset.hoverShown === 'true' && popupEl.dataset.clickShown !== 'true') {
                  popupEl.style.display = 'none';
                  popupEl.dataset.hoverShown = 'false';
                }
              });
            }
          }

          // Helper to place popup element under/above point
          function placeSimplePopup(p){
            if (!p) return;
            p.style.left = cx + 'px';
            p.style.top = (cy + d/2 + 10) + 'px';
            p.style.display = 'none';
            p.dataset.hoverShown = 'false';
            p.dataset.clickShown = 'false';
          }

          var popup;
          try {
            if (typeof window.createCommentPopup === 'function') {
              // Prefer updating existing viewer popup linked to this circle
              var popupIdLinked = el && el.dataset ? el.dataset.popupId : null;
              if (popupIdLinked) {
                popup = document.getElementById(popupIdLinked);
              }
              if (!popup && existing) popup = existing;
              if (popup) {
                // Update content (only text part) using viewer formatter if available
                var htmlText = (typeof formatCommentText === 'function') ? formatCommentText(ann.text || '') : (ann.text || '');
                var ccEl = popup.querySelector('.comment-content');
                if (ccEl) {
                  ccEl.innerHTML = htmlText;
                } else {
                  popup.innerHTML = '<div class="comment-popup-title">Комментарий</div><div class="comment-content">'+ htmlText +'</div>';
                }
                // Reposition near current coords
                popup.style.left = cx + 'px';
                popup.style.top = (cy + d/2 + 10) + 'px';
              } else {
                // No existing popup found: create a simple one and link it
                popup = document.createElement('div');
                popup.className = 'comment-popup';
                popup.id = popupId;
                var htmlText2 = (typeof formatCommentText === 'function') ? formatCommentText(ann.text || '') : (ann.text || '');
                popup.innerHTML = '<div class="comment-popup-title">Комментарий</div><div class="comment-content">'+ htmlText2 +'</div>';
                placeSimplePopup(popup);
                host.appendChild(popup);
                try { el.dataset.popupId = popup.id; } catch(e) { /* noop */ }
              }
            } else {
              // Fallback simple popup
              if (!existing) {
                popup = document.createElement('div');
                popup.className = 'comment-popup';
                popup.id = popupId;
                var htmlText3 = (typeof formatCommentText === 'function') ? formatCommentText(ann.text || '') : (ann.text || '');
                popup.innerHTML = '<div class="comment-popup-title">Комментарий</div><div class="comment-content">'+ htmlText3 +'</div>';
                placeSimplePopup(popup);
                host.appendChild(popup);
              } else {
                popup = existing;
                var htmlText4 = (typeof formatCommentText === 'function') ? formatCommentText(ann.text || '') : (ann.text || '');
                var ccEl2 = popup.querySelector('.comment-content');
                if (ccEl2) { ccEl2.innerHTML = htmlText4; } else { popup.innerHTML = '<div class="comment-popup-title">Комментарий</div><div class="comment-content">'+ htmlText4 +'</div>'; }
                placeSimplePopup(popup);
              }
            }
          } catch(e){ /* noop */ }

          // Attach hover listeners (only once for new circles)
          if (popup && isNew) attachHoverHandlers(el, popup);
        }
      }
      function selectById(id){
        if (!id) return;
        var el = document.getElementById(id) || document.getElementById('circle-'+id);
        if (el) { try { selectMarker(el); } catch(e){ /* noop */ } }
      }
      function clearSel(){ try { clearSelection(); } catch(e){ /* noop */ } }
      function rerenderAll(){
        try {
          var anns = (window.RedPenEditor.state.page && window.RedPenEditor.state.page.annotations) || [];
          for (var i=0;i<anns.length;i++){ upsert(anns[i]); }
        } catch(e){ /* noop */ }
      }
      return { ensureContainer: ensureContainer, toClientPx: toClientOffsets, upsert: upsert, selectById: selectById, clearSelection: clearSel, rerenderAll: rerenderAll };
    })();

    // Helper: snapshot existing DOM markers into local state annotations (single source of truth)
    function snapshotDomMarkersToState(){
      try {
        var st = window.RedPenEditor.state;
        st.page = st.page || { annotations: [], origW: undefined, origH: undefined };
        var img = document.getElementById('page-image');
        if (!img) return;
        var ir = img.getBoundingClientRect();
        var ow = st.page.origW || img.naturalWidth || ir.width;
        var oh = st.page.origH || img.naturalHeight || ir.height;
        var scaleX = (ir.width || img.width) / (ow || 1);
        var scaleY = (ir.height || img.height) / (oh || 1);
        function clientToOriginal(x, y){
          return [ Math.round(x / (scaleX || 1)), Math.round(y / (scaleY || 1)) ];
        }
        var list = [];
        var nodes = document.querySelectorAll('.circle');
        nodes.forEach(function(el){
          if (!el || !el.id) return;
          var ds = el.dataset || {};
          var annType = ds.annType || 'comment';
          if (annType === 'general') return; // no markers for general
          var coords = undefined;
          if (ds.coords) {
            coords = parseCoords(ds.coords);
          }
          if (!Array.isArray(coords)) {
            // compute center relative to image, then map back to original pixels
            var mr = el.getBoundingClientRect();
            var cx = Math.round(mr.left + mr.width/2 - ir.left);
            var cy = Math.round(mr.top + mr.height/2 - ir.top);
            coords = clientToOriginal(cx, cy);
          }
          list.push({ id: el.id, annType: annType, text: (typeof ds.text==='string'?ds.text:''), coords: coords });
        });
        st.page.annotations = list;
      } catch(e){ /* noop */ }
    }

    // Preview/Submit/Cancel API for panel
    window.RedPenEditor.onPreview = function(){
      try {
        var draft = (window.RedPenEditorPanel && window.RedPenEditorPanel.getDraft) ? window.RedPenEditorPanel.getDraft() : window.RedPenEditor.state.draft;
        // Validate via panel
        var v = window.RedPenEditorPanel && typeof window.RedPenEditorPanel.validate === 'function' ? window.RedPenEditorPanel.validate(draft) : { valid: true, errors: {} };
        if (!v.valid) {
          if (window.RedPenEditorPanel && window.RedPenEditorPanel.showErrors) window.RedPenEditorPanel.showErrors(v.errors || {});
          return;
        }
        // Normalize id
        var id = (draft.id && String(draft.id).trim()) ? String(draft.id).trim() : undefined;
        var annType = draft.annType;
        var content = draft.content || '';
        var coords = Array.isArray(draft.coords) ? [draft.coords[0], draft.coords[1]] : undefined;

        // GENERAL: update right block and cache
        if (annType === 'general') {
          // cache
          if (!window.RedPenEditor.state.cache) window.RedPenEditor.state.cache = { general: null };
          window.RedPenEditor.state.cache.general = { id: id, content: content };
          // DOM
          try {
            var gc = document.getElementById('global-comment');
            if (gc) gc.textContent = content;
          } catch(e){ /* noop */ }
          // update state.draft and baseline as existing general
          window.RedPenEditor.state.draft = { id: id, annType: 'general', content: content, coords: undefined };
          beginEditingExisting(window.RedPenEditor.state.draft);
          // Clear marker selection for general
          try { clearSelection(); } catch(e){ /* noop */ }
          // Disable Preview until next change
          if (window.RedPenEditorPanel && window.RedPenEditorPanel.setPreviewEnabled) window.RedPenEditorPanel.setPreviewEnabled(false);
          if (window.RedPenEditorPanel && window.RedPenEditorPanel.revalidate) window.RedPenEditorPanel.revalidate();
          try { window.alert('Сохранено'); } catch(e) {}
          return;
        }

        // MAIN/COMMENT: local upsert into state.page.annotations and DOM
        // Ensure page state exists
        var st = window.RedPenEditor.state;
        st.page = st.page || { pageId: undefined, origW: (document.getElementById('page-image')||{}).naturalWidth, origH: (document.getElementById('page-image')||{}).naturalHeight, annotations: [] };
        var list = st.page.annotations || (st.page.annotations = []);

        var created = false;
        if (!id) {
          // if there is an anonymous placeholder in list, remove it
          for (var k=list.length-1;k>=0;k--) { if (!list[k].id) { list.splice(k,1); } }
          id = 'client-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2,6);
          var newAnn = { id: id, annType: annType, text: content, coords: coords };
          list.push(newAnn);
          created = true;
          window.RedPenEditor.markers.upsert(newAnn);
        } else {
          // find existing
          var found = null;
          for (var i=0;i<list.length;i++){ if (list[i].id === id) { found = list[i]; break; } }
          if (!found) {
            var ann = { id: id, annType: annType, text: content, coords: coords };
            list.push(ann);
            created = true;
            window.RedPenEditor.markers.upsert(ann);
          } else {
            found.annType = annType;
            found.text = content;
            if (Array.isArray(coords)) found.coords = coords;
            window.RedPenEditor.markers.upsert(found);
          }
        }

        // Update selection and state
        st.ui.selectedAnnotationId = id;
        st.draft = { id: id, annType: annType, content: content, coords: coords };
        window.RedPenEditor.markers.selectById(id);

        // Update baseline as existing
        beginEditingExisting({ id: id, annType: annType, content: content, coords: coords });

        // Disable Preview until next change and revalidate
        if (window.RedPenEditorPanel && window.RedPenEditorPanel.setPreviewEnabled) window.RedPenEditorPanel.setPreviewEnabled(false);
        if (window.RedPenEditorPanel && window.RedPenEditorPanel.revalidate) window.RedPenEditorPanel.revalidate();
        try { window.alert('Сохранено'); } catch(e) { /* noop */ }
      } catch(e){ /* noop */ }
    };

    // Backward compatibility alias
    window.RedPenEditor.onSave = function(){ try { window.RedPenEditor.onPreview(); } catch(e) {} };

    // --- Auth / HTTP client (editorMode only) ---
    function apiBase(path){ 
      // Используйте абсолютный URL если на другом хосте
      var baseUrl = window.REDPEN_API_BASE || 'https://api.medinsky.net';
      return baseUrl + path;
    }
    function withJsonHeaders(headers){
      headers = headers || {};
      headers['Content-Type'] = 'application/json';
      return headers;
    }
    async function getCsrf(){
      console.log('[RedPen Editor] 🛡️  Getting CSRF token');
      var st = window.RedPenEditor.state;
      if (st.flags && st.flags.mock === true) {
        console.log('[RedPen Editor] 🎭 Mock mode: generating fake CSRF token');
        st.auth.csrfToken = st.auth.csrfToken || 'mock-csrf-'+Math.random().toString(36).slice(2,8);
        return { csrfToken: st.auth.csrfToken };
      }
      if (st.auth.csrfToken) {
        console.log('[RedPen Editor] ℹ️  CSRF token already cached');
        return { csrfToken: st.auth.csrfToken };
      }
      try {
        console.log('[RedPen Editor] 📡 Fetching /api/auth/csrf');
        const res = await fetch(apiBase('/api/auth/csrf'), { credentials: 'include' });
        console.log('[RedPen Editor] 📥 CSRF response status:', res.status);
        if (!res.ok) {
          console.error('[RedPen Editor] ❌ CSRF request failed');
          throw new Error('csrf_failed');
        }
        const data = await res.json();
        st.auth.csrfToken = data.csrfToken;
        console.log('[RedPen Editor] ✅ Got CSRF token:', data.csrfToken.substring(0, 16) + '...');
        return data;
      } catch (error) {
        console.error('[RedPen Editor] ❌ CSRF error:', error);
        throw error;
      }
    }
    async function apiMe(){
      console.log('[RedPen Editor] 🔍 Checking auth status via /api/auth/me');
      var st = window.RedPenEditor.state;
      if (st.flags && st.flags.mock === true) {
        console.log('[RedPen Editor] 🎭 Mock mode: simulating auth check');
        st.auth.isAuthenticated = true; 
        st.auth.username = st.auth.username || 'mockuser'; 
        st.auth.userId = st.auth.userId || 'mock-'+Math.random().toString(36).slice(2,6);
        console.log('[RedPen Editor] ✅ Mock auth check success:', st.auth.username);
        return { userId: st.auth.userId, username: st.auth.username };
      }
      try {
        console.log('[RedPen Editor] 📡 Fetching /api/auth/me');
        const res = await fetch(apiBase('/api/auth/me'), { credentials: 'include' });
        console.log('[RedPen Editor] 📥 Response status:', res.status);
        
        if (res.status === 401) { 
          console.warn('[RedPen Editor] ⚠️  Not authenticated (401)');
          st.auth.isAuthenticated = false; 
          return null; 
        }
        
        if (!res.ok) {
          console.error('[RedPen Editor] ❌ /api/auth/me failed with status:', res.status);
          throw new Error('me_failed');
        }
        
        const data = await res.json();
        st.auth.isAuthenticated = true; 
        st.auth.userId = data.userId; 
        st.auth.username = data.username;
        console.log('[RedPen Editor] ✅ Auth check success. User:', data.username, 'ID:', data.userId);
        return data;
      } catch (error) {
        console.error('[RedPen Editor] ❌ Auth check error:', error);
        throw error;
      }
    }
    async function loginWithToken(token){
      console.log('[RedPen Editor] 🔐 Login attempt with token length:', token.length);
      var st = window.RedPenEditor.state;
      if (st.flags && st.flags.mock === true) {
        console.log('[RedPen Editor] 🎭 Mock mode: simulating successful login');
        st.auth.isAuthenticated = true; 
        st.auth.username = 'mockuser_'+token.slice(0,4); 
        st.auth.userId = 'mock-'+Math.random().toString(36).slice(2,6);
        console.log('[RedPen Editor] ✅ Mock login success:', st.auth.username);
        return { ok: true };
      }
      try {
        console.log('[RedPen Editor] 📡 Getting CSRF token...');
        await getCsrf();
        console.log('[RedPen Editor] ✅ Got CSRF token');
        
        console.log('[RedPen Editor] 📡 Sending login request to /api/auth/login');
        const res = await fetch(apiBase('/api/auth/login'), {
          method: 'POST',
          headers: withJsonHeaders({ 'X-CSRF-Token': st.auth.csrfToken }),
          body: JSON.stringify({ token: token }),
          credentials: 'include'
        });
        
        console.log('[RedPen Editor] 📥 Login response status:', res.status);
        const responseText = await res.text();
        console.log('[RedPen Editor] 📥 Login response body:', responseText);
        
        if (!res.ok) {
          console.error('[RedPen Editor] ❌ Login failed with status:', res.status);
          throw new Error('login_failed');
        }
        
        console.log('[RedPen Editor] ✅ Login request succeeded, fetching /api/auth/me');
        await apiMe();
        console.log('[RedPen Editor] ✅ Auth check successful, user:', st.auth.username);
        return { ok: true };
      } catch (error) {
        console.error('[RedPen Editor] ❌ Login error:', error);
        throw error;
      }
    }
    async function fetchPageFromServer(pageId){
      var st = window.RedPenEditor.state;
      if (st.flags && st.flags.mock === true) {
        // mock: pretend we got same annotations and new sha
        st.page.serverPageSha = 'mock-sha-'+Date.now().toString(36);
        st.page.annotations = st.page.annotations || [];
        window.RedPenEditor.markers.rerenderAll();
        // also reflect general in right block if present
        var gen = st.cache && st.cache.general ? st.cache.general : null;
        if (gen) { var gc = document.getElementById('global-comment'); if (gc) gc.textContent = gen.content || ''; }
        return;
      }
      const res = await fetch(apiBase('/api/pages/'+encodeURIComponent(pageId)), { credentials:'include' });
      if (!res.ok) throw new Error('fetch_page_failed');
      const data = await res.json();
      st.page.pageId = data.pageId || pageId;
      st.page.serverPageSha = data.serverPageSha;
      st.page.origW = data.origW; st.page.origH = data.origH;
      // Normalize annotations: {id, annType, text, coords}
      st.page.annotations = Array.isArray(data.annotations) ? data.annotations.map(function(a){
        return { id: a.id, annType: a.annType, text: a.text, coords: a.coords };
      }) : [];
      window.RedPenEditor.markers.rerenderAll();
      // Update general right panel
      try {
        var g = (data.annotations || []).find(function(a){ return a.annType==='general'; });
        if (g) {
          st.cache.general = { id: normalizeId(g.id), content: g.text || '' };
          var gc = document.getElementById('global-comment'); if (gc) gc.textContent = g.text || '';
        }
      } catch(e){ /* noop */ }
    }
    async function saveAnnotationToServer(draft){
      var st = window.RedPenEditor.state;
      if (st.flags && st.flags.mock === true) {
        // simulate network and optimistic lock success
        var id = (draft.id && String(draft.id).trim()) || undefined;
        if (!id) id = 'srv-'+Date.now().toString(36)+Math.random().toString(36).slice(2,6);
        var serverPageSha = 'mock-sha-'+Date.now().toString(36);
        return { id: id, serverPageSha: serverPageSha };
      }
      await getCsrf();
      var pageId = st.page && st.page.pageId ? st.page.pageId : undefined;
      var payload = { annType: draft.annType, text: draft.content, clientPageSha: st.page.serverPageSha };
      if (draft.annType !== 'general') payload.coords = draft.coords;
      var url, method;
      if (draft.id && String(draft.id).trim()) {
        url = apiBase('/api/pages/'+encodeURIComponent(pageId)+'/annotations/'+encodeURIComponent(String(draft.id).trim()));
        method = 'PUT';
      } else {
        url = apiBase('/api/pages/'+encodeURIComponent(pageId)+'/annotations');
        method = 'POST';
      }
      const res = await fetch(url, {
        method: method,
        headers: withJsonHeaders({ 'X-CSRF-Token': st.auth.csrfToken }),
        body: JSON.stringify(payload),
        credentials: 'include'
      });
      if (res.status === 401) { throw Object.assign(new Error('unauthorized'), { code: 401 }); }
      if (res.status === 409) { throw Object.assign(new Error('conflict'), { code: 409 }); }
      if (!res.ok) throw new Error('save_failed');
      const data = await res.json();
      return data; // { id, serverPageSha }
    }

    // Simple login modal
    function showLoginModal(message){
      var host = document.getElementById('redpen-login');
      if (!host) return;
      host.style.display = '';
      host.style.border = '1px solid #ccc';
      host.style.padding = '10px';
      host.style.marginTop = '10px';
      host.innerHTML = ''+
        '<div style="font-weight:bold;margin-bottom:6px;">Требуется вход</div>'+
        (message ? '<div style="color:#DC143C;margin-bottom:6px;">'+message+'</div>' : '')+
        '<input id="redpen-login-token" type="text" placeholder="Личный токен" style="width:100%;margin-bottom:6px;" />'+
        '<div style="display:flex;gap:8px;align-items:center;">'+
          '<button id="redpen-login-do">Войти</button>'+
          '<span id="redpen-login-error" style="color:#DC143C;font-size:12px;"></span>'+
        '</div>';
      var btn = document.getElementById('redpen-login-do');
      if (btn) btn.onclick = async function(){
        var inp = document.getElementById('redpen-login-token');
        var token = inp ? String(inp.value || '').trim() : '';
        var errEl = document.getElementById('redpen-login-error');
        if (!token) { if (errEl) errEl.textContent = 'Введите токен'; return; }
        try {
          await loginWithToken(token);
          await apiMe();
          host.style.display = 'none';
          // Small badge
          try {
            var badge = document.getElementById('redpen-login-badge');
            if (!badge) {
              badge = document.createElement('div'); badge.id = 'redpen-login-badge'; badge.style.marginTop='6px';
              var actions = document.querySelector('.redpen-editor-actions');
              if (actions && actions.parentNode) actions.parentNode.insertBefore(badge, actions.nextSibling);
            }
            badge.textContent = 'Вы вошли как '+(window.RedPenEditor.state.auth.username || 'user');
          } catch(e) { /* noop */ }
        } catch(e){ if (errEl) errEl.textContent = 'Проверьте токен'; }
      };
    }

    window.RedPenEditor.onSubmit = async function(){
      try {
        var st = window.RedPenEditor.state;
        var draft = (window.RedPenEditorPanel && window.RedPenEditorPanel.getDraft) ? window.RedPenEditorPanel.getDraft() : st.draft;
        var v = window.RedPenEditorPanel && typeof window.RedPenEditorPanel.validate === 'function' ? window.RedPenEditorPanel.validate(draft) : { valid: true, errors: {} };
        if (!v.valid) { if (window.RedPenEditorPanel && window.RedPenEditorPanel.showErrors) window.RedPenEditorPanel.showErrors(v.errors || {}); return; }
        // Ensure pageId
        try {
          if (!st.page.pageId && typeof window.currentPageId === 'string') st.page.pageId = window.currentPageId.split('_')[1];
        } catch(e) { /* noop */ }
        // Check auth
        if (!st.auth.isAuthenticated) { showLoginModal(); return; }
        // Ensure csrf
        await getCsrf();
        // Compose and send
        var result;
        try {
          result = await saveAnnotationToServer(draft);
        } catch(err) {
          if (err && err.code === 401) { showLoginModal('Сессия истекла, войдите заново'); return; }
          if (err && err.code === 409) {
            var agree = window.confirm('Кто-то изменил страницу. Обновить данные? Вы потеряете локальные несохранённые правки.');
            if (agree) {
              await fetchPageFromServer(st.page.pageId);
              // leave draft and buttons as-is; user may retry
            }
            return;
          }
          window.alert('Не удалось отправить. Попробуйте ещё раз.');
          return;
        }
        // Success
        var serverId = result && result.id ? String(result.id) : (draft.id || undefined);
        st.page.serverPageSha = result && result.serverPageSha ? result.serverPageSha : st.page.serverPageSha;
        // Update local annotations and markers similar to Preview
        var annType = draft.annType;
        var content = draft.content || '';
        var coords = Array.isArray(draft.coords) ? [draft.coords[0], draft.coords[1]] : undefined;
        if (annType === 'general') {
          // update cache and right block
          st.cache.general = { id: serverId ? serverId : undefined, content: content };
          try { var gc = document.getElementById('global-comment'); if (gc) gc.textContent = content; } catch(e){}
          st.draft = { id: serverId, annType: 'general', content: content, coords: undefined };
          beginEditingExisting(st.draft);
          try { window.RedPenEditor.markers.clearSelection(); } catch(e){}
        } else {
          // insert/update annotation in single source list
          st.page.annotations = st.page.annotations || [];
          var oldId = draft.id && String(draft.id).trim() ? String(draft.id).trim() : undefined;
          var idToUse = serverId || oldId;
          if (!idToUse) idToUse = 'srv-'+Date.now().toString(36)+Math.random().toString(36).slice(2,6);
          var found = null;
          for (var i=0;i<st.page.annotations.length;i++){ if (st.page.annotations[i].id === (serverId || oldId)) { found = st.page.annotations[i]; break; } }
          if (!found) { st.page.annotations.push({ id: idToUse, annType: annType, text: content, coords: coords }); }
          else { found.id = idToUse; found.annType = annType; found.text = content; if (Array.isArray(coords)) found.coords = coords; }
          // If id changed from local client-id to server id, remove old marker
          if (oldId && serverId && oldId !== serverId) { var oldEl = document.getElementById(oldId); if (oldEl && oldEl.parentNode) oldEl.parentNode.removeChild(oldEl); }
          // Upsert marker and keep selection
          window.RedPenEditor.markers.upsert({ id: idToUse, annType: annType, text: content, coords: coords });
          window.RedPenEditor.markers.selectById(idToUse);
          st.ui.selectedAnnotationId = idToUse;
          st.draft = { id: idToUse, annType: annType, content: content, coords: coords };
          beginEditingExisting(st.draft);
        }
        // Disable buttons and toast
        if (window.RedPenEditorPanel && window.RedPenEditorPanel.setPreviewEnabled) window.RedPenEditorPanel.setPreviewEnabled(false);
        if (window.RedPenEditorPanel && window.RedPenEditorPanel.setSubmitEnabled) window.RedPenEditorPanel.setSubmitEnabled(false);
        if (window.RedPenEditorPanel && window.RedPenEditorPanel.revalidate) window.RedPenEditorPanel.revalidate();
        try { window.alert('Отправлено'); } catch(e){}
      } catch(e){ /* noop */ }
    };

    window.RedPenEditor.onCancel = function(){
      try {
        var state = window.RedPenEditor.state;
        var current = (window.RedPenEditorPanel && window.RedPenEditorPanel.getDraft) ? window.RedPenEditorPanel.getDraft() : state.draft;
        var base = state.baseline;
        if (!base) {
          // nothing to revert to
          if (window.RedPenEditorPanel && window.RedPenEditorPanel.revalidate) window.RedPenEditorPanel.revalidate();
          return;
        }
        if (isDirty(current, base)) {
          if (!confirmLoseChanges()) return;
        }
        // Revert to baseline
        var revert = { id: base.id, annType: base.annType, content: base.content, coords: base.coords };
        state.draft = Object.assign({}, state.draft, revert);
        if (window.RedPenEditorPanel && window.RedPenEditorPanel.setDraft) window.RedPenEditorPanel.setDraft(state.draft);
        // Clear selection if baseline is general or new without id/coords
        if (base.annType === 'general' || !base.id || (typeof base.id === 'undefined' && !base.coords)) {
          try { clearSelection(); } catch(e) { /* noop */ }
        }
        if (window.RedPenEditorPanel && window.RedPenEditorPanel.revalidate) window.RedPenEditorPanel.revalidate();
      } catch(e){ /* noop */ }
    };

    function handleMarkerClick(marker, event){
      if (event && event.stopPropagation) event.stopPropagation();
      if (event && event.stopImmediatePropagation) event.stopImmediatePropagation();

      // Before switching, check unsaved changes
      var currentDraft = (window.RedPenEditorPanel && window.RedPenEditorPanel.getDraft) ? window.RedPenEditorPanel.getDraft() : (window.RedPenEditor.state && window.RedPenEditor.state.draft) || { annType: 'comment', content: '' };
      var baseline = window.RedPenEditor.state.baseline;
      var mode = (window.RedPenEditor.state.editing && window.RedPenEditor.state.editing.mode) || 'none';
      if ((mode === 'existing' || mode === 'new') && isDirty(currentDraft, baseline)) {
        if (!confirmLoseChanges()) return; // cancel transition
      }

      // Extract id: empty string -> undefined
      var rawId = (marker.id || '').trim();
      var id = rawId === '' ? undefined : rawId;

      // Resolve coords
      var ds = marker.dataset || {};
      var coords = parseCoords(ds.coords);
      if (!Array.isArray(coords)) {
        var img = document.getElementById('page-image');
        if (img && marker.getBoundingClientRect) {
          var mr = marker.getBoundingClientRect();
          var ir = img.getBoundingClientRect();
          var cx = Math.round(mr.left + mr.width/2 - ir.left);
          var cy = Math.round(mr.top + mr.height/2 - ir.top);
          // map client to original pixel coords
          var ow = (window.RedPenEditor.state.page && window.RedPenEditor.state.page.origW) || img.naturalWidth || ir.width;
          var oh = (window.RedPenEditor.state.page && window.RedPenEditor.state.page.origH) || img.naturalHeight || ir.height;
          var scaleX = (ir.width || img.width) / (ow || 1);
          var scaleY = (ir.height || img.height) / (oh || 1);
          var ox = Math.round(cx / (scaleX || 1));
          var oy = Math.round(cy / (scaleY || 1));
          coords = [ox, oy];
        }
      }

      // Determine type and content without overwriting with empty text
      currentDraft = (window.RedPenEditor && window.RedPenEditor.state && window.RedPenEditor.state.draft) || { annType: 'comment', content: '' };
      var annType = ds.annType || currentDraft.annType || 'comment';
      var content = (typeof ds.text === 'string' && ds.text.trim().length > 0) ? ds.text : currentDraft.content;

      // Update state (merge)
      window.RedPenEditor.state.ui.selectedAnnotationId = id || null;
      window.RedPenEditor.state.draft = Object.assign({}, currentDraft, { id: id, annType: annType, content: content, coords: coords });
      beginEditingExisting({ id: id, annType: annType, content: content, coords: coords });

      // Update UI
      selectMarker(marker);
      if (window.RedPenEditorPanel && window.RedPenEditorPanel.setDraft) {
        window.RedPenEditorPanel.setDraft(window.RedPenEditor.state.draft);
        if (window.RedPenEditorPanel.revalidate) try { window.RedPenEditorPanel.revalidate(); } catch(e) {}
      }
      // Ensure coord input enabled when not general and sync type select
      try {
        var coordEl2 = document.getElementById('redpen-coord');
        if (coordEl2) {
          var isGen = window.RedPenEditor.state.draft.annType === 'general';
          coordEl2.disabled = isGen;
          coordEl2.parentElement && (coordEl2.parentElement.style.display = isGen ? 'none' : '');
        }
        var typeEl2 = document.getElementById('redpen-type');
        if (typeEl2) typeEl2.value = window.RedPenEditor.state.draft.annType;
      } catch(e){ /* noop */ }
    }

    function handleEmptyClick(event){
      // Ignore clicks in general mode
      try {
        var currentType = (window.RedPenEditorPanel && window.RedPenEditorPanel.getDraft) ? window.RedPenEditorPanel.getDraft().annType : (window.RedPenEditor.state && window.RedPenEditor.state.draft && window.RedPenEditor.state.draft.annType);
        if (currentType === 'general') return;
      } catch(e){ /* noop */ }

      var img = document.getElementById('page-image');
      if (!img || !img.getBoundingClientRect) return;
      var r = img.getBoundingClientRect();
      var x = Math.round(event.clientX - r.left);
      var y = Math.round(event.clientY - r.top);
      // map to original pixel coords
      var ow = (window.RedPenEditor.state.page && window.RedPenEditor.state.page.origW) || img.naturalWidth || r.width;
      var oh = (window.RedPenEditor.state.page && window.RedPenEditor.state.page.origH) || img.naturalHeight || r.height;
      var scaleX = (r.width || img.width) / (ow || 1);
      var scaleY = (r.height || img.height) / (oh || 1);
      var ox = Math.round(x / (scaleX || 1));
      var oy = Math.round(y / (scaleY || 1));
      var coords = [ox, oy];

      var mode = (window.RedPenEditor.state.editing && window.RedPenEditor.state.editing.mode) || 'none';
      var current = window.RedPenEditorPanel && window.RedPenEditorPanel.getDraft
        ? window.RedPenEditorPanel.getDraft()
        : (window.RedPenEditor.state && window.RedPenEditor.state.draft) || { annType: 'comment', content: '' };

      if (mode === 'existing') {
        // Check for dirty before leaving existing
        if (isDirty(current, window.RedPenEditor.state.baseline)) {
          if (!confirmLoseChanges()) return; // cancel
        }
        // approved: clear selection and start creating new
        clearSelection();
        var newDraft = { id: undefined, annType: current.annType, content: '', coords: coords };
        window.RedPenEditor.state.draft = Object.assign({}, window.RedPenEditor.state.draft, newDraft);
        beginCreatingNew(newDraft);
      } else {
        // mode new or none: free change of coords without prompt
        clearSelection();
        window.RedPenEditor.state.draft = Object.assign({}, window.RedPenEditor.state.draft, { id: undefined, annType: current.annType, content: current.content, coords: coords });
        if (mode !== 'new') {
          beginCreatingNew({ id: undefined, annType: current.annType, content: current.content, coords: coords });
        }
      }

      if (window.RedPenEditorPanel && window.RedPenEditorPanel.setDraft) {
        window.RedPenEditorPanel.setDraft(window.RedPenEditor.state.draft);
        if (window.RedPenEditorPanel.revalidate) try { window.RedPenEditorPanel.revalidate(); } catch(e) {}
      }
    }

    // As a fallback, snapshot existing markers a bit later in case hooks missed the render
    setTimeout(function(){ try { snapshotDomMarkersToState(); } catch(e){} }, 1000);
  }

  // Export globals per spec
  window.RedPenEditor = window.RedPenEditor || {};
  window.RedPenEditor.init = init;
  // Hook to receive annotations from viewer code for immediate general loading
  window.RedPenEditor.onAnnotationsLoaded = function(anns){
    try {
      if (!Array.isArray(anns)) return;
      // Capture current pageId like '007' from global currentPageId 'page_007'
      try {
        var cpid = (typeof window.currentPageId === 'string') ? window.currentPageId : '';
        if (cpid && cpid.indexOf('_') !== -1) {
          var pid = cpid.split('_')[1];
          if (window.RedPenEditor.state && window.RedPenEditor.state.page) {
            window.RedPenEditor.state.page.pageId = pid;
          }
        }
      } catch(e) { /* noop */ }
      var gen = null;
      for (var i=0;i<anns.length;i++){ var a = anns[i]; if (a && a.annType === 'general'){ gen = { id: normalizeId(a.id), content: (typeof a.text==='string'?a.text:'') }; break; } }
      if (!window.RedPenEditor.state.cache) window.RedPenEditor.state.cache = { general: null };
      window.RedPenEditor.state.cache.general = gen;
      // If in general mode and user hasn’t typed (textarea empty or matches last auto), set content
      var draft = (window.RedPenEditorPanel && window.RedPenEditorPanel.getDraft) ? window.RedPenEditorPanel.getDraft() : window.RedPenEditor.state.draft;
      var ta = (draft && typeof draft.content === 'string') ? draft.content : '';
      var prevAuto = window.RedPenEditor.state.ui && window.RedPenEditor.state.ui.lastAutoGeneralContent;
      if (gen && draft && draft.annType === 'general' && (ta.trim()==='' || ta===prevAuto)){
        var newDraft = { id: gen.id, annType: 'general', content: gen.content, coords: undefined };
        window.RedPenEditor.state.draft = Object.assign({}, window.RedPenEditor.state.draft, newDraft);
        if (typeof beginEditingExisting === 'function') { beginEditingExisting(newDraft); }
        if (window.RedPenEditorPanel && window.RedPenEditorPanel.setDraft) {
          window.RedPenEditorPanel.setDraft(window.RedPenEditor.state.draft);
          if (window.RedPenEditorPanel.revalidate) try { window.RedPenEditorPanel.revalidate(); } catch(e) {}
        }
        if (window.RedPenEditor.state && window.RedPenEditor.state.ui) window.RedPenEditor.state.ui.lastAutoGeneralContent = gen.content;
      }
      // Schedule snapshot of DOM markers to build local annotation store after viewer renders them
      setTimeout(function(){ try { snapshotDomMarkersToState(); } catch(e){} }, 150);
      setTimeout(function(){ try { snapshotDomMarkersToState(); } catch(e){} }, 600);
    } catch(e){ /* noop */ }
  };
  window.hasEditorFlag = hasEditorFlag; // optional external use

  // Self-run only when flag present
  if (hasEditorFlag()) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function(){
        try { init(); } catch(e) { /* noop */ }
      }, { once: true });
    } else {
      try { init(); } catch(e) { /* noop */ }
    }
  }
})();
