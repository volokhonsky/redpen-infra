
(function(){
  var REDPEN_EDITOR_VERSION = '2.1.0';
  console.log('[RedPen Editor] Bootstrap v' + REDPEN_EDITOR_VERSION + ' loading');

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
    console.log('[RedPen Editor] Initializing v' + REDPEN_EDITOR_VERSION);

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
      window.RedPenEditor.state.editorMode = true;
      if (!window.RedPenEditor.state.cache) window.RedPenEditor.state.cache = { general: null };
      if (!window.RedPenEditor.state.ui) window.RedPenEditor.state.ui = { selectedAnnotationId: null, lastAutoGeneralContent: undefined };
      if (!window.RedPenEditor.state.editing) window.RedPenEditor.state.editing = { mode: 'none' };
      if (!('baseline' in window.RedPenEditor.state)) window.RedPenEditor.state.baseline = null;
      if (!window.RedPenEditor.state.flags) window.RedPenEditor.state.flags = { allowCoordChangeWithoutPrompt: false, mock: (window.REDPEN_MOCKS === true) };
      if (!window.RedPenEditor.state.autoContent) window.RedPenEditor.state.autoContent = { general: undefined };
      if (!window.RedPenEditor.state.auth) window.RedPenEditor.state.auth = { isAuthenticated: false, userId: undefined, username: undefined, csrfToken: undefined };
      if (!window.RedPenEditor.state.page) window.RedPenEditor.state.page = { pageId: undefined, serverPageSha: undefined, origW: undefined, origH: undefined, annotations: [] };
    }

    // ===== HELPER FUNCTIONS (NO DUPLICATES) =====
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

    function findGeneralOnPage(){
      try {
        var anns = (window.RedPenPage && Array.isArray(window.RedPenPage.annotations)) ? window.RedPenPage.annotations
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
          if (annType === 'general') return;
          var coords = undefined;
          if (ds.coords) {
            coords = parseCoords(ds.coords);
          }
          if (!Array.isArray(coords)) {
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

    function apiBase(path){
      var baseUrl = window.REDPEN_API_BASE || 'https://api.medinsky.net';
      return baseUrl + path;
    }

    function withJsonHeaders(headers){
      headers = headers || {};
      headers['Content-Type'] = 'application/json';
      return headers;
    }

    async function getCsrf(){
      var st = window.RedPenEditor.state;
      if (st.flags && st.flags.mock === true) {
        st.auth.csrfToken = st.auth.csrfToken || 'mock-csrf-'+Math.random().toString(36).slice(2,8);
        return { csrfToken: st.auth.csrfToken };
      }
      if (st.auth.csrfToken) {
        return { csrfToken: st.auth.csrfToken };
      }
      try {
        const res = await fetch(apiBase('/api/auth/csrf'), { credentials: 'include' });
        if (!res.ok) throw new Error('csrf_failed');
        const data = await res.json();
        st.auth.csrfToken = data.csrfToken;
        return data;
      } catch (error) {
        console.error('[RedPen Editor] CSRF error:', error);
        throw error;
      }
    }

    async function apiMe(){
      var st = window.RedPenEditor.state;
      if (st.flags && st.flags.mock === true) {
        st.auth.isAuthenticated = true;
        st.auth.username = st.auth.username || 'mockuser';
        st.auth.userId = st.auth.userId || 'mock-'+Math.random().toString(36).slice(2,6);
        return { userId: st.auth.userId, username: st.auth.username };
      }
      try {
        const res = await fetch(apiBase('/api/auth/me'), { credentials: 'include' });
        if (res.status === 401) {
          st.auth.isAuthenticated = false;
          return null;
        }
        if (!res.ok) throw new Error('me_failed');
        const data = await res.json();
        st.auth.isAuthenticated = true;
        st.auth.userId = data.userId;
        st.auth.username = data.username;
        return data;
      } catch (error) {
        console.error('[RedPen Editor] Auth error:', error);
        throw error;
      }
    }

    async function loginWithToken(token){
      var st = window.RedPenEditor.state;
      if (st.flags && st.flags.mock === true) {
        st.auth.isAuthenticated = true;
        st.auth.username = 'mockuser_'+token.slice(0,4);
        st.auth.userId = 'mock-'+Math.random().toString(36).slice(2,6);
        return { ok: true };
      }
      try {
        await getCsrf();
        const res = await fetch(apiBase('/api/auth/login'), {
          method: 'POST',
          headers: withJsonHeaders({ 'X-CSRF-Token': st.auth.csrfToken }),
          body: JSON.stringify({ token: token }),
          credentials: 'include'
        });
        if (!res.ok) throw new Error('login_failed');
        await apiMe();
        return { ok: true };
      } catch (error) {
        console.error('[RedPen Editor] Login error:', error);
        throw error;
      }
    }

    async function fetchPageFromServer(pageId){
      var st = window.RedPenEditor.state;
      if (st.flags && st.flags.mock === true) {
        st.page.serverPageSha = 'mock-sha-'+Date.now().toString(36);
        st.page.annotations = st.page.annotations || [];
        window.RedPenEditor.markers.rerenderAll();
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
      st.page.annotations = Array.isArray(data.annotations) ? data.annotations.map(function(a){
        return { id: a.id, annType: a.annType, text: a.text, coords: a.coords };
      }) : [];
      window.RedPenEditor.markers.rerenderAll();
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
      return data;
    }

    function showLoginModal(message){
      var host = document.getElementById('redpen-login');
      if (!host) return;
      host.style.display = '';
      host.innerHTML = '<div style="font-weight:bold;margin-bottom:6px;">Требуется вход</div>'+
        (message ? '<div style="color:#DC143C;margin-bottom:6px;">'+message+'</div>' : '')+
        '<input id="redpen-login-token" type="text" placeholder="Личный токен" style="width:100%;margin-bottom:6px;" />'+
        '<div style="display:flex;gap:8px;">'+
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
        } catch(e){ if (errEl) errEl.textContent = 'Проверьте токен'; }
      };
    }

    function handleMarkerClick(marker, event){
      if (event) { event.stopPropagation(); event.stopImmediatePropagation(); }
      var currentDraft = (window.RedPenEditorPanel && window.RedPenEditorPanel.getDraft) ? window.RedPenEditorPanel.getDraft() : (window.RedPenEditor.state && window.RedPenEditor.state.draft) || { annType: 'comment', content: '' };
      var baseline = window.RedPenEditor.state.baseline;
      var mode = (window.RedPenEditor.state.editing && window.RedPenEditor.state.editing.mode) || 'none';
      if ((mode === 'existing' || mode === 'new') && isDirty(currentDraft, baseline)) {
        if (!confirmLoseChanges()) return;
      }
      var rawId = (marker.id || '').trim();
      var id = rawId === '' ? undefined : rawId;
      var ds = marker.dataset || {};
      var coords = parseCoords(ds.coords);
      if (!Array.isArray(coords)) {
        var img = document.getElementById('page-image');
        if (img && marker.getBoundingClientRect) {
          var mr = marker.getBoundingClientRect();
          var ir = img.getBoundingClientRect();
          var cx = Math.round(mr.left + mr.width/2 - ir.left);
          var cy = Math.round(mr.top + mr.height/2 - ir.top);
          var ow = (window.RedPenEditor.state.page && window.RedPenEditor.state.page.origW) || img.naturalWidth || ir.width;
          var oh = (window.RedPenEditor.state.page && window.RedPenEditor.state.page.origH) || img.naturalHeight || ir.height;
          var scaleX = (ir.width || img.width) / (ow || 1);
          var scaleY = (ir.height || img.height) / (oh || 1);
          coords = [Math.round(cx / scaleX), Math.round(cy / scaleY)];
        }
      }
      currentDraft = (window.RedPenEditor && window.RedPenEditor.state && window.RedPenEditor.state.draft) || { annType: 'comment', content: '' };
      var annType = ds.annType || currentDraft.annType || 'comment';
      var content = (typeof ds.text === 'string' && ds.text.trim().length > 0) ? ds.text : currentDraft.content;
      window.RedPenEditor.state.ui.selectedAnnotationId = id || null;
      window.RedPenEditor.state.draft = Object.assign({}, currentDraft, { id: id, annType: annType, content: content, coords: coords });
      beginEditingExisting({ id: id, annType: annType, content: content, coords: coords });
      selectMarker(marker);
      if (window.RedPenEditorPanel && window.RedPenEditorPanel.setDraft) {
        window.RedPenEditorPanel.setDraft(window.RedPenEditor.state.draft);
        if (window.RedPenEditorPanel.revalidate) try { window.RedPenEditorPanel.revalidate(); } catch(e) {}
      }
    }

    function handleEmptyClick(event){
      try {
        var currentType = (window.RedPenEditorPanel && window.RedPenEditorPanel.getDraft) ? window.RedPenEditorPanel.getDraft().annType : (window.RedPenEditor.state && window.RedPenEditor.state.draft && window.RedPenEditor.state.draft.annType);
        if (currentType === 'general') return;
      } catch(e){ /* noop */ }
      var img = document.getElementById('page-image');
      if (!img || !img.getBoundingClientRect) return;
      var r = img.getBoundingClientRect();
      var x = Math.round(event.clientX - r.left);
      var y = Math.round(event.clientY - r.top);
      var ow = (window.RedPenEditor.state.page && window.RedPenEditor.state.page.origW) || img.naturalWidth || r.width;
      var oh = (window.RedPenEditor.state.page && window.RedPenEditor.state.page.origH) || img.naturalHeight || r.height;
      var scaleX = (r.width || img.width) / (ow || 1);
      var scaleY = (r.height || img.height) / (oh || 1);
      var coords = [Math.round(x / scaleX), Math.round(y / scaleY)];
      var mode = (window.RedPenEditor.state.editing && window.RedPenEditor.state.editing.mode) || 'none';
      var current = window.RedPenEditorPanel && window.RedPenEditorPanel.getDraft
        ? window.RedPenEditorPanel.getDraft()
        : (window.RedPenEditor.state && window.RedPenEditor.state.draft) || { annType: 'comment', content: '' };
      if (mode === 'existing') {
        if (isDirty(current, window.RedPenEditor.state.baseline)) {
          if (!confirmLoseChanges()) return;
        }
        clearSelection();
        var newDraft = { id: undefined, annType: current.annType, content: '', coords: coords };
        window.RedPenEditor.state.draft = Object.assign({}, window.RedPenEditor.state.draft, newDraft);
        beginCreatingNew(newDraft);
      } else {
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

    // ===== MAIN INIT FLOW =====
    window.RedPenEditor.clearSelection = clearSelection;
    window.RedPenEditor._isDirty = isDirty;
    window.RedPenEditor._snapshot = snapshotFromDraft;

    var container = document.getElementById('global-comment-container');
    if (!container) return;

    Array.prototype.slice.call(container.children).forEach(function(ch){
      if (ch && ch.style) ch.style.display = 'none';
    });

    if (window.RedPenEditorPanel && typeof window.RedPenEditorPanel.mount === 'function') {
      window.RedPenEditorPanel.mount(container);
      if (window.RedPenEditorPanel.setDraft) {
        window.RedPenEditorPanel.setDraft(window.RedPenEditor.state.draft);
      }
    }

    ensureSelectedStyleInjected();
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
      if (coordEl) { coordEl.value=''; coordEl.disabled = true; if (coordEl.parentElement) coordEl.parentElement.style.display='none'; }
    } catch(e){ /* noop */ }

    beginCreatingNew({ id: undefined, annType: 'general', content: '', coords: undefined });
    initGeneralCache();

    window.RedPenEditor.state.editing.mode = 'none';
    window.RedPenEditor.state.baseline = null;

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

    window.RedPenEditor.onTypeChange = function(newType, prevType){
      var state = window.RedPenEditor.state;
      var current = (window.RedPenEditorPanel && window.RedPenEditorPanel.getDraft) ? window.RedPenEditorPanel.getDraft() : state.draft;
      var baseline = state.baseline;
      var mode = (state.editing && state.editing.mode) || 'none';
      if (newType === 'general') {
        var gen = state.cache && state.cache.general;
        if ((mode === 'existing' || mode === 'new') && isDirty(current, baseline)) {
          if (!confirmLoseChanges()) return false;
        }
        if (gen) {
          state.draft = { id: gen.id, annType: 'general', content: gen.content, coords: undefined };
          beginEditingExisting(state.draft);
        } else {
          state.draft = Object.assign({}, state.draft, { id: undefined, annType: 'general', coords: undefined });
          beginCreatingNew({ id: undefined, annType: 'general', content: state.draft.content || '', coords: undefined });
        }
        if (window.RedPenEditorPanel && window.RedPenEditorPanel.setDraft) window.RedPenEditorPanel.setDraft(state.draft);
        return true;
      }
      if (mode === 'existing' && isDirty(current, baseline)) { if (!confirmLoseChanges()) return false; }
      if (mode === 'new' && isDirty(current, baseline)) { if (!confirmLoseChanges()) return false; }
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

    window.RedPenEditor.markers = window.RedPenEditor.markers || (function(){
      function ensureContainer(){
        var host = document.getElementById('overlay-container') || document.getElementById('image-container');
        var img = document.getElementById('page-image');
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
        if (!el) {
          el = document.createElement('div');
          el.id = ann.id;
          el.className = 'circle';
          host.appendChild(el);
        }
        try {
          el.dataset.annType = ann.annType || 'comment';
          el.dataset.text = ann.text || '';
          if (Array.isArray(ann.coords)) el.dataset.coords = JSON.stringify(ann.coords);
        } catch(e){ /* noop */ }
        var sizeMap = { main: 100, comment: 50, small: 25 };
        var d = sizeMap[ann.annType] || 50;
        el.style.cssText = 'position:absolute;width:'+d+'px;height:'+d+'px;border-radius:50%;cursor:pointer;transform:translateX(-50%);';
        var color = ann.annType === 'main' ? '#DC143C' : '#0000FF';
        el.style.background = 'radial-gradient(circle, '+color+'80 0%, '+color+'40 50%, '+color+'00 100%)';
        var lt = [0,0];
        if (Array.isArray(ann.coords)) {
          lt = toClientOffsets(ann.coords);
        }
        el.style.left = lt[0] + 'px';
        el.style.top = (lt[1] - d/2) + 'px';
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

    window.RedPenEditor.onPreview = function(){
      try {
        var draft = (window.RedPenEditorPanel && window.RedPenEditorPanel.getDraft) ? window.RedPenEditorPanel.getDraft() : window.RedPenEditor.state.draft;
        var v = window.RedPenEditorPanel && typeof window.RedPenEditorPanel.validate === 'function' ? window.RedPenEditorPanel.validate(draft) : { valid: true, errors: {} };
        if (!v.valid) {
          if (window.RedPenEditorPanel && window.RedPenEditorPanel.showErrors) window.RedPenEditorPanel.showErrors(v.errors || {});
          return;
        }
        var id = (draft.id && String(draft.id).trim()) ? String(draft.id).trim() : undefined;
        var annType = draft.annType;
        var content = draft.content || '';
        var coords = Array.isArray(draft.coords) ? [draft.coords[0], draft.coords[1]] : undefined;
        if (annType === 'general') {
          if (!window.RedPenEditor.state.cache) window.RedPenEditor.state.cache = { general: null };
          window.RedPenEditor.state.cache.general = { id: id, content: content };
          try { var gc = document.getElementById('global-comment'); if (gc) gc.textContent = content; } catch(e){ /* noop */ }
          window.RedPenEditor.state.draft = { id: id, annType: 'general', content: content, coords: undefined };
          beginEditingExisting(window.RedPenEditor.state.draft);
          try { clearSelection(); } catch(e){ /* noop */ }
          if (window.RedPenEditorPanel && window.RedPenEditorPanel.setPreviewEnabled) window.RedPenEditorPanel.setPreviewEnabled(false);
          if (window.RedPenEditorPanel && window.RedPenEditorPanel.revalidate) window.RedPenEditorPanel.revalidate();
          try { window.alert('Сохранено'); } catch(e) {}
          return;
        }
        var st = window.RedPenEditor.state;
        st.page = st.page || { pageId: undefined, annotations: [] };
        var list = st.page.annotations || (st.page.annotations = []);
        if (!id) {
          for (var k=list.length-1;k>=0;k--) { if (!list[k].id) { list.splice(k,1); } }
          id = 'client-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2,6);
          list.push({ id: id, annType: annType, text: content, coords: coords });
          window.RedPenEditor.markers.upsert({ id: id, annType: annType, text: content, coords: coords });
        } else {
          var found = null;
          for (var i=0;i<list.length;i++){ if (list[i].id === id) { found = list[i]; break; } }
          if (!found) {
            list.push({ id: id, annType: annType, text: content, coords: coords });
            window.RedPenEditor.markers.upsert({ id: id, annType: annType, text: content, coords: coords });
          } else {
            found.annType = annType;
            found.text = content;
            if (Array.isArray(coords)) found.coords = coords;
            window.RedPenEditor.markers.upsert(found);
          }
        }
        st.ui.selectedAnnotationId = id;
        st.draft = { id: id, annType: annType, content: content, coords: coords };
        window.RedPenEditor.markers.selectById(id);
        beginEditingExisting({ id: id, annType: annType, content: content, coords: coords });
        if (window.RedPenEditorPanel && window.RedPenEditorPanel.setPreviewEnabled) window.RedPenEditorPanel.setPreviewEnabled(false);
        if (window.RedPenEditorPanel && window.RedPenEditorPanel.revalidate) window.RedPenEditorPanel.revalidate();
        try { window.alert('Сохранено'); } catch(e) { /* noop */ }
      } catch(e){ console.error('[RedPen Editor] Preview error:', e); }
    };

    window.RedPenEditor.onSave = function(){ try { window.RedPenEditor.onPreview(); } catch(e) {} };

    window.RedPenEditor.onSubmit = async function(){
      try {
        var st = window.RedPenEditor.state;
        var draft = (window.RedPenEditorPanel && window.RedPenEditorPanel.getDraft) ? window.RedPenEditorPanel.getDraft() : st.draft;
        var v = window.RedPenEditorPanel && typeof window.RedPenEditorPanel.validate === 'function' ? window.RedPenEditorPanel.validate(draft) : { valid: true, errors: {} };
        if (!v.valid) { if (window.RedPenEditorPanel && window.RedPenEditorPanel.showErrors) window.RedPenEditorPanel.showErrors(v.errors || {}); return; }
        try { if (!st.page.pageId && typeof window.currentPageId === 'string') st.page.pageId = window.currentPageId.split('_')[1]; } catch(e) { /* noop */ }
        if (!st.auth.isAuthenticated) { showLoginModal(); return; }
        await getCsrf();
        var result;
        try {
          result = await saveAnnotationToServer(draft);
        } catch(err) {
          if (err && err.code === 401) { showLoginModal('Сессия истекла, войдите заново'); return; }
          if (err && err.code === 409) {
            var agree = window.confirm('Кто-то изменил страницу. Обновить данные?');
            if (agree) { await fetchPageFromServer(st.page.pageId); }
            return;
          }
          window.alert('Не удалось отправить. Попробуйте ещё раз.');
          return;
        }
        var serverId = result && result.id ? String(result.id) : (draft.id || undefined);
        st.page.serverPageSha = result && result.serverPageSha ? result.serverPageSha : st.page.serverPageSha;
        var annType = draft.annType;
        var content = draft.content || '';
        var coords = Array.isArray(draft.coords) ? [draft.coords[0], draft.coords[1]] : undefined;
        if (annType === 'general') {
          st.cache.general = { id: serverId ? serverId : undefined, content: content };
          try { var gc = document.getElementById('global-comment'); if (gc) gc.textContent = content; } catch(e){}
          st.draft = { id: serverId, annType: 'general', content: content, coords: undefined };
          beginEditingExisting(st.draft);
          try { window.RedPenEditor.markers.clearSelection(); } catch(e){}
        } else {
          st.page.annotations = st.page.annotations || [];
          var oldId = draft.id && String(draft.id).trim() ? String(draft.id).trim() : undefined;
          var idToUse = serverId || oldId;
          if (!idToUse) idToUse = 'srv-'+Date.now().toString(36)+Math.random().toString(36).slice(2,6);
          var found = null;
          for (var i=0;i<st.page.annotations.length;i++){ if (st.page.annotations[i].id === (serverId || oldId)) { found = st.page.annotations[i]; break; } }
          if (!found) { st.page.annotations.push({ id: idToUse, annType: annType, text: content, coords: coords }); }
          else { found.id = idToUse; found.annType = annType; found.text = content; if (Array.isArray(coords)) found.coords = coords; }
          if (oldId && serverId && oldId !== serverId) { var oldEl = document.getElementById(oldId); if (oldEl && oldEl.parentNode) oldEl.parentNode.removeChild(oldEl); }
          window.RedPenEditor.markers.upsert({ id: idToUse, annType: annType, text: content, coords: coords });
          window.RedPenEditor.markers.selectById(idToUse);
          st.ui.selectedAnnotationId = idToUse;
          st.draft = { id: idToUse, annType: annType, content: content, coords: coords };
          beginEditingExisting(st.draft);
        }
        if (window.RedPenEditorPanel && window.RedPenEditorPanel.setPreviewEnabled) window.RedPenEditorPanel.setPreviewEnabled(false);
        if (window.RedPenEditorPanel && window.RedPenEditorPanel.setSubmitEnabled) window.RedPenEditorPanel.setSubmitEnabled(false);
        if (window.RedPenEditorPanel && window.RedPenEditorPanel.revalidate) window.RedPenEditorPanel.revalidate();
        try { window.alert('Отправлено'); } catch(e){}
      } catch(e){ console.error('[RedPen Editor] Submit error:', e); }
    };

    window.RedPenEditor.onCancel = function(){
      try {
        var state = window.RedPenEditor.state;
        var current = (window.RedPenEditorPanel && window.RedPenEditorPanel.getDraft) ? window.RedPenEditorPanel.getDraft() : state.draft;
        var base = state.baseline;
        if (!base) { if (window.RedPenEditorPanel && window.RedPenEditorPanel.revalidate) window.RedPenEditorPanel.revalidate(); return; }
        if (isDirty(current, base)) { if (!confirmLoseChanges()) return; }
        var revert = { id: base.id, annType: base.annType, content: base.content, coords: base.coords };
        state.draft = Object.assign({}, state.draft, revert);
        if (window.RedPenEditorPanel && window.RedPenEditorPanel.setDraft) window.RedPenEditorPanel.setDraft(state.draft);
        if (base.annType === 'general' || !base.id || (typeof base.id === 'undefined' && !base.coords)) {
          try { clearSelection(); } catch(e) { /* noop */ }
        }
        if (window.RedPenEditorPanel && window.RedPenEditorPanel.revalidate) window.RedPenEditorPanel.revalidate();
      } catch(e){ console.error('[RedPen Editor] Cancel error:', e); }
    };

    setTimeout(function(){ try { snapshotDomMarkersToState(); } catch(e){} }, 1000);
  }

  window.RedPenEditor = window.RedPenEditor || {};
  window.RedPenEditor.init = init;
  window.RedPenEditor.onAnnotationsLoaded = function(anns){
    try {
      if (!Array.isArray(anns)) return;
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
      var draft = (window.RedPenEditorPanel && window.RedPenEditorPanel.getDraft) ? window.RedPenEditorPanel.getDraft() : window.RedPenEditor.state.draft;
      var ta = (draft && typeof draft.content === 'string') ? draft.content : '';
      var prevAuto = window.RedPenEditor.state.ui && window.RedPenEditor.state.ui.lastAutoGeneralContent;
      if (gen && draft && draft.annType === 'general' && (ta.trim()==='' || ta===prevAuto)){
        var newDraft = { id: gen.id, annType: 'general', content: gen.content, coords: undefined };
        window.RedPenEditor.state.draft = Object.assign({}, window.RedPenEditor.state.draft, newDraft);
        if (window.RedPenEditorPanel && window.RedPenEditorPanel.setDraft) {
          window.RedPenEditorPanel.setDraft(window.RedPenEditor.state.draft);
          if (window.RedPenEditorPanel.revalidate) try { window.RedPenEditorPanel.revalidate(); } catch(e) {}
        }
        if (window.RedPenEditor.state && window.RedPenEditor.state.ui) window.RedPenEditor.state.ui.lastAutoGeneralContent = gen.content;
      }
      setTimeout(function(){ try { snapshotDomMarkersToState(); } catch(e){} }, 150);
      setTimeout(function(){ try { snapshotDomMarkersToState(); } catch(e){} }, 600);
    } catch(e){ console.error('[RedPen Editor] onAnnotationsLoaded error:', e); }
  };
  window.hasEditorFlag = hasEditorFlag;

  if (hasEditorFlag()) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function(){
        try { init(); } catch(e) { console.error('[RedPen Editor] Init error:', e); }
      }, { once: true });
    } else {
      try { init(); } catch(e) { console.error('[RedPen Editor] Init error:', e); }
    }
  }
})();