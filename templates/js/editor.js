// RedPen Editor Mode (MVP)
// This file is safe to include in viewer: it does nothing unless editor mode is explicitly enabled.
// Editor mode is enabled by query param ?editor=1 or global flag window.REDPEN_EDITOR === true.

(function(){
  // Avoid polluting global scope; expose under window.RedPenEditor
  const g = (typeof window !== 'undefined') ? window : {};

  function isEditorMode() {
    try {
      const search = g.location ? g.location.search : '';
      const hash = g.location ? g.location.hash : '';
      const usp = new URLSearchParams(search);
      const hsp = new URLSearchParams(hash.startsWith('#') ? hash.substring(1) : hash);
      const qp = usp.get('editor');
      const hp = hsp.get('editor');
      const on = (qp === '1' || qp === 'true') || (hp === '1' || hp === 'true');
      return Boolean(on || g.REDPEN_EDITOR === true);
    } catch(e) {
      return g.REDPEN_EDITOR === true;
    }
  }

  // Mock toggle: VITE_USE_MOCKS or window.REDPEN_MOCKS
  function isMockMode() {
    // Support build-time env or runtime window flag
    // eslint-disable-next-line no-undef
    const vite = (typeof VITE_USE_MOCKS !== 'undefined') ? VITE_USE_MOCKS : undefined;
    return g.REDPEN_MOCKS === true || vite === 'true';
  }

  const state = {
    editorMode: false,
    auth: { isAuthenticated: false, userId: null, username: null, csrfToken: null },
    page: { pageId: null, imageUrl: null, origW: null, origH: null, serverPageSha: null },
    ui: { selectedAnnotationId: null, draft: { id: null, annType: 'main', content: '', coords: null } },
  };

  // Minimal API client with optional mocks
  const api = {
    async getCsrf() {
      if (!state.editorMode) return null;
      if (isMockMode()) {
        return { csrfToken: 'mock-csrf-' + Math.random().toString(36).slice(2) };
      }
      const res = await fetch('/api/auth/csrf', { credentials: 'include' });
      if (res.status === 401) throw { status: 401 };
      return res.json();
    },
    async login(token) {
      if (isMockMode()) {
        if (!token || !token.trim()) throw new Error('empty token');
        // mark logged in
        state.auth.isAuthenticated = true;
        const username = 'mockuser_' + Math.random().toString(36).slice(2,6);
        state.auth.userId = 'mock-' + Math.random().toString(36).slice(2);
        state.auth.username = username;
        return { userId: state.auth.userId, username };
      }
      const { csrfToken } = await api.getCsrf();
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
        credentials: 'include',
        body: JSON.stringify({ token })
      });
      if (!res.ok) throw new Error('login failed');
      return api.me();
    },
    async me() {
      if (isMockMode()) {
        if (state.auth.isAuthenticated) {
          return { userId: state.auth.userId, username: state.auth.username };
        }
        throw { status: 401 };
      }
      const res = await fetch('/api/auth/me', { credentials: 'include' });
      if (res.status === 401) throw { status: 401 };
      return res.json();
    },
    async getPage(pageId) {
      if (isMockMode()) {
        // Use current image as source
        const img = document.getElementById('page-image');
        const anns = Array.isArray(g.allAnns) ? g.allAnns : [];
        return {
          pageId,
          imageUrl: img ? img.src : '',
          origW: img ? img.naturalWidth : 1000,
          origH: img ? img.naturalHeight : 1400,
          serverPageSha: state.page.serverPageSha || ('mock-sha-' + Math.random().toString(36).slice(2)),
          annotations: anns
        };
      }
      const res = await fetch(`/api/pages/${encodeURIComponent(pageId)}`, { credentials: 'include' });
      if (res.status === 401) throw { status: 401 };
      return res.json();
    },
    async saveAnnotation(pageId, draft) {
      const payload = { annType: draft.annType, text: draft.content, coords: draft.coords || undefined, clientPageSha: state.page.serverPageSha };
      if (isMockMode()) {
        // simulate assign id and new sha
        const isUpdate = !!draft.id;
        const id = draft.id || ('mock-ann-' + Math.random().toString(36).slice(2));
        const serverPageSha = 'mock-sha-' + Math.random().toString(36).slice(2);
        return { id, serverPageSha, isUpdate };
      }
      const { csrfToken } = await api.getCsrf();
      const headers = { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken };
      const url = draft.id ? `/api/pages/${encodeURIComponent(pageId)}/annotations/${encodeURIComponent(draft.id)}`
                           : `/api/pages/${encodeURIComponent(pageId)}/annotations`;
      const method = draft.id ? 'PUT' : 'POST';
      const res = await fetch(url, { method, headers, credentials: 'include', body: JSON.stringify(payload) });
      if (res.status === 401) throw { status: 401 };
      if (res.status === 409) throw { status: 409 };
      return res.json();
    }
  };

  function detectContainers(){
    return {
      rightCol: document.getElementById('comments-content'),
      imageContainer: document.getElementById('image-container'),
      overlay: document.getElementById('overlay-container'),
      img: document.getElementById('page-image')
    };
  }

  function renderEditorPanel() {
    const { rightCol } = detectContainers();
    if (!rightCol) return;

    // Hide existing viewer-only content inside right column (title + list) but only in editor mode
    Array.from(rightCol.children).forEach(ch => { ch.style.display = 'none'; });

    // Create editor container
    const host = document.createElement('div');
    host.className = 'redpen-editor';
    host.innerHTML = `
      <div class="redpen-editor__header">
        <div class="redpen-editor__title">Редактор аннотаций</div>
        <div class="redpen-editor__user" id="redpen-editor-user"></div>
      </div>
      <div class="redpen-editor__body">
        <label class="redpen-editor__label">Тип
          <select id="redpen-type" class="redpen-editor__select">
            <option value="main" selected>main</option>
            <option value="comment">comment</option>
            <option value="general">general</option>
          </select>
        </label>
        <label class="redpen-editor__label">Координата
          <input id="redpen-coord" class="redpen-editor__input" type="text" placeholder="кликните по странице" readonly />
        </label>
        <label class="redpen-editor__label">Содержимое
          <textarea id="redpen-content" class="redpen-editor__textarea" rows="6" placeholder="Текст комментария"></textarea>
        </label>
        <div class="redpen-editor__errors" id="redpen-errors"></div>
        <div class="redpen-editor__actions">
          <button id="redpen-save" class="redpen-editor__btn primary" disabled>Сохранить</button>
          <button id="redpen-cancel" class="redpen-editor__btn">Отмена</button>
        </div>
      </div>
      <div id="redpen-login" class="redpen-editor__login" style="display:none"></div>
    `;

    rightCol.appendChild(host);

    // Login UI container (shown on demand)
    renderLoginUI();

    // Hook up events
    host.querySelector('#redpen-type').addEventListener('change', onTypeChange);
    host.querySelector('#redpen-content').addEventListener('input', onContentInput);
    host.querySelector('#redpen-save').addEventListener('click', onSave);
    host.querySelector('#redpen-cancel').addEventListener('click', onCancel);

    // Initialize defaults
    updateFormFromDraft();
  }

  function renderLoginUI() {
    const loginHost = document.getElementById('redpen-login');
    if (!loginHost) return;
    loginHost.innerHTML = `
      <div class="redpen-login__modal">
        <div class="redpen-login__title">Войти</div>
        <div class="redpen-login__row">
          <input id="redpen-token" class="redpen-editor__input" type="password" placeholder="Личный токен" />
          <button id="redpen-do-login" class="redpen-editor__btn primary">Войти</button>
        </div>
        <div id="redpen-login-error" class="redpen-editor__errors"></div>
      </div>
    `;
    loginHost.querySelector('#redpen-do-login').addEventListener('click', async () => {
      const token = (loginHost.querySelector('#redpen-token').value || '').trim();
      const err = loginHost.querySelector('#redpen-login-error');
      err.textContent = '';
      try {
        const me = await api.login(token);
        state.auth.isAuthenticated = true;
        state.auth.userId = me.userId;
        state.auth.username = me.username;
        loginHost.style.display = 'none';
        updateUserBadge();
      } catch (e) {
        err.textContent = 'Ошибка входа. Проверьте токен.';
      }
    });
  }

  function showLogin() {
    const el = document.getElementById('redpen-login');
    if (el) el.style.display = 'block';
  }

  function updateUserBadge(){
    const u = document.getElementById('redpen-editor-user');
    if (!u) return;
    if (state.auth.isAuthenticated) {
      u.textContent = `Вы вошли как ${state.auth.username}`;
    } else {
      u.innerHTML = '<button class="redpen-editor__btn" id="redpen-open-login">Войти</button>';
      const b = document.getElementById('redpen-open-login');
      if (b) b.addEventListener('click', showLogin);
    }
  }

  function onTypeChange(e){
    state.ui.draft.annType = e.target.value;
    if (state.ui.draft.annType === 'general') {
      state.ui.draft.coords = null;
    }
    updateFormFromDraft();
  }

  function onContentInput(e){
    state.ui.draft.content = e.target.value;
    validateForm();
  }

  function onCancel(){
    state.ui.draft = { id: null, annType: 'main', content: '', coords: null };
    state.ui.selectedAnnotationId = null;
    clearSelectedMarker();
    updateFormFromDraft();
  }

  function validateForm(){
    const errs = [];
    const d = state.ui.draft;
    if (!d.annType || !['general','main','comment'].includes(d.annType)) errs.push('Некорректный тип');
    if (!d.content || d.content.trim().length === 0) errs.push('Требуется содержимое');
    if (d.annType === 'general' && d.coords) errs.push('Для general координаты запрещены');
    if ((d.annType === 'main' || d.annType === 'comment') && !d.coords) errs.push('Кликните по странице для выбора точки');
    const el = document.getElementById('redpen-errors');
    if (el) el.innerHTML = errs.map(e=>`<div class="redpen-error">${e}</div>`).join('');
    const saveBtn = document.getElementById('redpen-save');
    if (saveBtn) saveBtn.disabled = errs.length > 0;
    return errs.length === 0;
  }

  function updateFormFromDraft(){
    const d = state.ui.draft;
    const typeSel = document.getElementById('redpen-type');
    const coordInp = document.getElementById('redpen-coord');
    const contentTa = document.getElementById('redpen-content');
    if (typeSel) typeSel.value = d.annType || 'main';
    if (contentTa && contentTa.value !== d.content) contentTa.value = d.content || '';
    if (d.annType === 'general') {
      coordInp.parentElement.style.display = 'none';
    } else {
      coordInp.parentElement.style.display = '';
    }
    if (coordInp) coordInp.value = d.coords ? `[${d.coords[0]}, ${d.coords[1]}]` : '';
    validateForm();
    updateUserBadge();
  }

  function clearSelectedMarker(){
    document.querySelectorAll('#overlay-container .circle.is-selected').forEach(el => el.classList.remove('is-selected'));
  }

  function selectMarkerByAnnId(annId){
    clearSelectedMarker();
    if (!annId) return;
    const circle = document.querySelector(`#overlay-container .circle#circle-${CSS.escape(annId)}`);
    if (circle) circle.classList.add('is-selected');
  }

  function onOverlayClick(e){
    // Only handle in editor mode
    if (!state.editorMode) return;

    const target = e.target;
    if (target && target.classList && target.classList.contains('circle')) {
      // Clicking an existing marker: load annotation for editing
      e.stopPropagation();
      // Determine annotation id: circle id format 'circle-<id>'
      const annId = target.id ? target.id.replace(/^circle-/, '') : null;
      const ann = (g.allAnns || []).find(a => String(a.id) === String(annId));
      if (ann) {
        state.ui.selectedAnnotationId = ann.id || null;
        state.ui.draft = { id: ann.id || null, annType: ann.annType, content: ann.text || '', coords: ann.coords ? [Math.round(ann.coords[0]), Math.round(ann.coords[1])] : null };
        selectMarkerByAnnId(ann.id);
        updateFormFromDraft();
      }
      return;
    }

    // Click on empty space: set coordinates for main/comment draft
    const { img } = detectContainers();
    if (!img) return;
    const rect = img.getBoundingClientRect();
    const clientX = e.clientX;
    const clientY = e.clientY;
    // Compute if clicked within image area
    if (clientX < rect.left || clientX > rect.right || clientY < rect.top || clientY > rect.bottom) return;

    const relX = clientX - rect.left;
    const relY = clientY - rect.top;
    // Convert to original pixel coordinates
    const origW = img.naturalWidth || rect.width;
    const origH = img.naturalHeight || rect.height;
    const x = Math.round(relX * (origW / rect.width));
    const y = Math.round(relY * (origH / rect.height));

    state.ui.selectedAnnotationId = null;
    clearSelectedMarker();
    if (state.ui.draft.annType === 'general') {
      // ignore coords
      state.ui.draft.coords = null;
    } else {
      state.ui.draft.coords = [x, y];
    }
    updateFormFromDraft();
  }

  async function ensurePageInfoLoaded(){
    // Take current page id and image url from existing viewer
    const pageId = g.currentPageId; // e.g., 'page_007'
    if (!pageId) return;
    state.page.pageId = pageId;
    if (isMockMode()) {
      const p = await api.getPage(pageId);
      Object.assign(state.page, p);
      return;
    }
    try {
      const p = await api.getPage(pageId);
      Object.assign(state.page, p);
    } catch (e) {
      if (e && e.status === 401) {
        showLogin();
      }
    }
  }

  async function onSave(){
    if (!validateForm()) return;
    if (!state.auth.isAuthenticated && !isMockMode()) {
      // Try to get auth status first
      try {
        const me = await api.me();
        state.auth.isAuthenticated = true;
        state.auth.userId = me.userId;
        state.auth.username = me.username;
      } catch(e) {
        showLogin();
        return;
      }
    }

    const btn = document.getElementById('redpen-save');
    if (btn) btn.disabled = true;

    try {
      await ensurePageInfoLoaded();
      const res = await api.saveAnnotation(state.page.pageId, state.ui.draft);
      // Update local sha
      const serverPageSha = res.serverPageSha || state.page.serverPageSha;
      state.page.serverPageSha = serverPageSha;

      // Update local annotations array allAnns
      const updated = { id: res.id || state.ui.draft.id, annType: state.ui.draft.annType, text: state.ui.draft.content, coords: state.ui.draft.coords || undefined };
      const idx = (g.allAnns || []).findIndex(a => String(a.id) === String(updated.id));
      if (idx >= 0) {
        g.allAnns[idx] = Object.assign({}, g.allAnns[idx], updated);
      } else {
        // Add to end
        if (!Array.isArray(g.allAnns)) g.allAnns = [];
        g.allAnns.push(updated);
      }

      // Rerender markers
      if (typeof g.repositionAnnotations === 'function') {
        g.repositionAnnotations();
      }

      // Keep form open on edited annotation
      state.ui.selectedAnnotationId = updated.id;
      state.ui.draft.id = updated.id;
      selectMarkerByAnnId(updated.id);

      toast('Сохранено');
    } catch (e) {
      if (e && e.status === 401) {
        showLogin();
      } else if (e && e.status === 409) {
        alert('Конфликт версий. Обновите страницу для загрузки свежих данных.');
      } else {
        console.error('Save failed', e);
        alert('Ошибка сохранения');
      }
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function toast(msg){
    const el = document.createElement('div');
    el.className = 'redpen-editor__toast';
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(()=>{ el.classList.add('show'); }, 10);
    setTimeout(()=>{ el.classList.remove('show'); el.remove(); }, 2000);
  }

  function hookEvents(){
    const { overlay } = detectContainers();
    if (!overlay) return;
    overlay.addEventListener('click', onOverlayClick, true);
  }

  function observeOverlay(){
    const container = document.getElementById('image-container');
    if (!container) return;
    const mo = new MutationObserver(() => {
      // Re-hook when overlay changes
      hookEvents();
    });
    mo.observe(container, { childList: true, subtree: true });
  }

  async function init() {
    state.editorMode = isEditorMode();
    if (!state.editorMode) return; // Do nothing in viewer mode

    // Render editor UI
    renderEditorPanel();

    // Prepare page info
    await ensurePageInfoLoaded();

    // Hook delegated handlers
    hookEvents();
    observeOverlay();
  }

  // Expose init and state for debugging
  g.RedPenEditor = { init, state };

  // Auto-init after DOM ready but only if editor mode
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { if (isEditorMode()) init(); });
  } else {
    if (isEditorMode()) init();
  }
})();
