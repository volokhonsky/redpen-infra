(function(){
  var CABINET_VERSION = '1.0.0';
  console.log('[RedPen Cabinet] v' + CABINET_VERSION + ' loading');

  var state = {
    user: null,
    docs: [],
    manifestCache: {}, // docId -> {fileKey: label} | null (null = no manifest / offline)
    ann: { items: [], total: 0, limit: 50, offset: 0, filters: {} },
    hist: { items: [], hasMore: false, limit: 50, offset: 0, filters: {} },
    admin: { loaded: false },
    survey: { loaded: false, docId: '', results: [], total: 0, limit: 50, offset: 0, pool: [] }
  };

  function escapeHtml(s){
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function formatDate(iso){
    if (!iso) return '—';
    try {
      var hasTz = /Z$|[+-]\d\d:?\d\d$/.test(iso);
      var d = new Date(hasTz ? iso : iso + 'Z');
      if (isNaN(d.getTime())) return iso;
      return d.toLocaleString('ru-RU');
    } catch (e) { return iso; }
  }

  function qs(params){
    var parts = [];
    Object.keys(params || {}).forEach(function(k){
      var v = params[k];
      if (v === undefined || v === null || v === '') return;
      parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(v));
    });
    return parts.length ? ('?' + parts.join('&')) : '';
  }

  function apiBase(path){ return window.RedPenAuth.apiBase(path); }

  // ===== status / offline banner =====

  var _statusTimer = null;
  function setStatus(message, isError){
    var el = document.getElementById('cab-status');
    el.textContent = message;
    el.className = 'cab-status' + (isError ? ' is-error' : '');
    el.style.display = '';
    if (_statusTimer) clearTimeout(_statusTimer);
    _statusTimer = setTimeout(function(){ el.style.display = 'none'; }, isError ? 6000 : 3000);
  }

  function showOffline(isOffline){
    var el = document.getElementById('cab-offline-notice');
    if (el) el.style.display = isOffline ? '' : 'none';
  }

  // ===== fetch helpers =====

  async function safeDetail(res){
    try { var d = await res.json(); return d && d.detail; } catch (e) { return null; }
  }

  async function apiGet(path){
    var res;
    try {
      res = await fetch(apiBase(path), { credentials: 'include' });
    } catch (e) {
      showOffline(true);
      throw e;
    }
    showOffline(false);
    if (res.status === 401) { showLogin(); throw new Error('unauthorized'); }
    if (!res.ok) {
      var detail = await safeDetail(res);
      setStatus(detail || ('Ошибка запроса: ' + res.status), true);
      throw new Error('http_' + res.status);
    }
    return res.json();
  }

  async function apiMutate(method, path, body){
    var csrf;
    try { csrf = (await window.RedPenAuth.getCsrf()).csrfToken; }
    catch (e) { showOffline(true); throw e; }

    function doFetch(token){
      return fetch(apiBase(path), {
        method: method,
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token },
        body: body !== undefined ? JSON.stringify(body) : undefined,
        credentials: 'include'
      });
    }

    var res;
    try { res = await doFetch(csrf); }
    catch (e) { showOffline(true); throw e; }

    if (res.status === 403) {
      // Stale CSRF token: refresh once and retry, same convention as the editor.
      try {
        csrf = (await window.RedPenAuth.getCsrf(true)).csrfToken;
        res = await doFetch(csrf);
      } catch (e) { showOffline(true); throw e; }
    }

    showOffline(false);
    if (res.status === 401) { showLogin(); throw new Error('unauthorized'); }
    if (!res.ok) {
      var detail = await safeDetail(res);
      setStatus(detail || ('Ошибка запроса: ' + res.status), true);
      throw new Error('http_' + res.status);
    }
    if (res.status === 204) return null;
    try { return await res.json(); } catch (e) { return null; }
  }

  // ===== C.8: page manifest lookup / editor deep link =====

  async function fetchManifest(docId){
    if (Object.prototype.hasOwnProperty.call(state.manifestCache, docId)) return state.manifestCache[docId];
    var map = null;
    try {
      var res = await fetch('../' + encodeURIComponent(docId) + '/metadata.json');
      if (res.ok) {
        var data = await res.json();
        if (Array.isArray(data.pages)) {
          map = {};
          data.pages.forEach(function(p){ if (p && p.file) map[p.file] = p.label; });
        }
      }
    } catch (e) { /* offline copy or missing doc -- treat as no manifest */ }
    state.manifestCache[docId] = map;
    return map;
  }

  async function prefetchManifests(docIds){
    var unique = docIds.filter(function(id, i){ return docIds.indexOf(id) === i; });
    await Promise.all(unique.map(fetchManifest));
  }

  function pageDisplay(docId, pageNum){
    var map = state.manifestCache[docId];
    return (map && map['page_' + pageNum]) || pageNum;
  }

  // Ссылка на одно замечание: страница читателя со сканом и единственным
  // выделенным маркером (?only=<id>).
  //
  // Раньше здесь строился адрес `<doc>/?p=<label>&editor=1&ann=<id>` — то есть
  // оглавление, где редакторских скриптов нет вовсе, и кнопка просто не
  // работала. Правка теперь живёт в /app/ (см. editorLink ниже), а эта ссылка
  // ведёт туда, где видно результат: на страницу читателя.
  function annotationLink(docId, pageNum, remarkId){
    var map = state.manifestCache[docId];
    var label = map && map['page_' + pageNum];
    if (!label) {
      var n = parseInt(pageNum, 10);
      if (isNaN(n) || n <= 0) return null;
      label = String(n);
    }
    return '../' + encodeURIComponent(docId) + '/pages/' + encodeURIComponent(label) +
           '/?only=' + encodeURIComponent(remarkId);
  }

  // Ссылка на правку: карточка замечания в редакторе /app/. Ключ страницы там
  // файловый, как в API, — переводить его в читательскую метку не нужно.
  function editorLink(docId, pageNum, remarkId){
    return '../app/#/ann/' + encodeURIComponent(docId) + '/' +
           encodeURIComponent(pageNum) + '/' + encodeURIComponent(remarkId);
  }

  // ===== login / profile =====

  function hasTokenAuthFlag(){
    try { return new URLSearchParams(window.location.search).get('auth') === 'token'; }
    catch (e) { return false; }
  }

  function showLogin(){
    document.getElementById('cab-app').style.display = 'none';
    var host = document.getElementById('cab-login-screen');
    host.style.display = '';
    if (hasTokenAuthFlag()) document.getElementById('cab-token-login').style.display = '';
    window.RedPenAuth.renderGoogleButton(
      document.getElementById('cab-google-btn'),
      function(user, error){
        var errEl = document.getElementById('cab-login-error');
        if (error && error.message === 'invite_required') {
          // Отдельное сообщение: это не сбой входа, а отсутствие доступа.
          errEl.textContent = 'Нужен код приглашения. Получите его у администратора ' +
                              'и введите в поле ниже.';
          return;
        }
        if (error || !user) { errEl.textContent = 'Не удалось войти через Google'; return; }
        errEl.textContent = '';
        refreshUserAndRender();
      },
      function(){
        var el = document.getElementById('cab-invite-input');
        return el ? (el.value || '').trim() : null;
      });
  }

  function bindLoginForm(){
    document.getElementById('cab-token-submit').addEventListener('click', async function(){
      var input = document.getElementById('cab-token-input');
      var errEl = document.getElementById('cab-login-error');
      var token = (input.value || '').trim();
      if (!token) { errEl.textContent = 'Введите токен'; return; }
      try {
        await window.RedPenAuth.loginWithToken(token);
        errEl.textContent = '';
        await refreshUserAndRender();
      } catch (e) {
        errEl.textContent = 'Проверьте токен';
      }
    });
  }

  var ROLE_LABELS = { viewer: 'читатель', editor: 'редактор', reviewer: 'приёмщик', admin: 'админ' };

  function renderProfile(){
    var host = document.getElementById('cab-profile');
    var u = state.user;
    var html = [];
    // Ни аватара, ни имени из Google: их в системе нет (docs/anonymity-model.md).
    // Участника представляет выбранный им псевдоним.
    html.push('<span class="cab-profile-name">' + escapeHtml(u.displayName || u.username || '') + '</span>');
    html.push('<span class="cab-profile-role">' + escapeHtml(ROLE_LABELS[u.role] || u.role) + '</span>');
    html.push('<button type="button" id="cab-logout-btn">Выйти</button>');
    host.innerHTML = html.join('');
    document.getElementById('cab-logout-btn').addEventListener('click', async function(){
      await window.RedPenAuth.logout();
      state.user = null;
      showLogin();
    });
  }

  async function refreshUserAndRender(){
    try {
      state.user = await window.RedPenAuth.me();
    } catch (e) {
      showOffline(true);
      showLogin();
      return;
    }
    showOffline(false);
    if (!state.user) { showLogin(); return; }
    showApp();
  }

  function showApp(){
    document.getElementById('cab-login-screen').style.display = 'none';
    document.getElementById('cab-app').style.display = '';
    renderProfile();

    var canEdit = state.user.role === 'editor' || state.user.role === 'admin';
    document.getElementById('cab-tabs').style.display = canEdit ? '' : 'none';
    document.getElementById('cab-viewer-notice').style.display = canEdit ? 'none' : '';
    document.getElementById('cab-tab-admin-btn').style.display = state.user.role === 'admin' ? '' : 'none';
    // Опрос — админская вкладка: пул решает, что показывают людям снаружи.
    document.getElementById('cab-tab-survey-btn').style.display = state.user.role === 'admin' ? '' : 'none';

    if (!canEdit) {
      document.getElementById('cab-tab-remarks').style.display = 'none';
      document.getElementById('cab-tab-history').style.display = 'none';
      document.getElementById('cab-tab-survey').style.display = 'none';
      document.getElementById('cab-tab-admin').style.display = 'none';
      return;
    }

    loadStats().then(function(){
      renderAnnotationsShell();
      renderHistoryShell();
      loadAnnotations(true);
    });
  }

  // ===== tabs =====

  function bindTabs(){
    Array.prototype.forEach.call(document.querySelectorAll('.cab-tab-btn'), function(btn){
      btn.addEventListener('click', function(){ switchTab(btn.getAttribute('data-tab')); });
    });
  }

  function switchTab(tab){
    Array.prototype.forEach.call(document.querySelectorAll('.cab-tab-btn'), function(btn){
      btn.classList.toggle('is-active', btn.getAttribute('data-tab') === tab);
    });
    ['remarks', 'history', 'survey', 'admin'].forEach(function(t){
      document.getElementById('cab-tab-' + t).style.display = (t === tab) ? '' : 'none';
    });
    if (tab === 'admin' && state.user.role === 'admin' && !state.admin.loaded) loadAdmin();
    if (tab === 'survey' && state.user.role === 'admin' && !state.survey.loaded) loadSurvey();
  }

  // ===== stats (doc filter options) =====

  async function loadStats(){
    try {
      var stats = await apiGet('/api/stats');
      state.docs = stats.docs || [];
    } catch (e) { /* status already shown */ }
  }

  function docOptionsHtml(){
    return ['<option value="">Все документы</option>'].concat(
      state.docs.map(function(d){
        return '<option value="' + escapeHtml(d.docId) + '">' + escapeHtml(d.docId) +
          ' (' + d.published + ' опубл. / ' + d.draft + ' черн.)</option>';
      })
    ).join('');
  }

  function updateAuthorOptions(selectEl, items, idKey, labelFn){
    if (!selectEl) return;
    var seen = {};
    Array.prototype.forEach.call(selectEl.options, function(o){ if (o.value) seen[o.value] = true; });
    items.forEach(function(item){
      var id = item[idKey];
      if (id == null || seen[id]) return;
      seen[id] = true;
      var opt = document.createElement('option');
      opt.value = String(id);
      opt.textContent = labelFn(item) || ('#' + id);
      selectEl.appendChild(opt);
    });
  }

  // ===== remarks tab =====
  function renderAnnotationsShell(){
    var host = document.getElementById('cab-tab-remarks');
    host.innerHTML =
      '<form id="cab-remark-filters" class="cab-filters">' +
        '<label>Документ<select name="docId">' + docOptionsHtml() + '</select></label>' +
        '<label>Страница<input type="text" name="pageKey" placeholder="напр. 6" /></label>' +
        '<label>Вид<select name="kind"><option value="">Любой</option><option value="major">major</option><option value="minor">minor</option></select></label>' +
        '<label>Статус<select name="status"><option value="">Любой</option><option value="published">published</option><option value="draft">draft</option><option value="deleted">deleted</option></select></label>' +
        '<label>Автор<select name="authorId" id="cab-remark-author-filter"><option value="">Все авторы</option></select></label>' +
        '<label>Тег<select name="tag" id="cab-remark-tag-filter"><option value="">Любой</option></select></label>' +
        '<label>Поиск<input type="text" name="q" placeholder="текст" /></label>' +
        '<button type="submit">Применить</button>' +
        '<button type="button" class="cab-btn-secondary" id="cab-remark-reset">Сбросить</button>' +
      '</form>' +
      '<table class="cab-table"><thead><tr>' +
        '<th>Документ</th><th>Страница</th><th>Тип</th><th>Статус</th><th>Автор</th><th>Изменено</th><th>Текст</th><th>Действия</th>' +
      '</tr></thead><tbody id="cab-remark-tbody"></tbody></table>' +
      '<div id="cab-remark-empty" style="display:none;">Ничего не найдено.</div>' +
      '<button type="button" id="cab-remark-more" class="cab-load-more" style="display:none;">Показать ещё</button>';

    document.getElementById('cab-remark-filters').addEventListener('submit', function(ev){
      ev.preventDefault();
      var f = new FormData(ev.target);
      state.ann.filters = {
        docId: f.get('docId') || undefined,
        pageKey: (f.get('pageKey') || '').trim() || undefined,
        kind: f.get('kind') || undefined,
        status: f.get('status') || undefined,
        authorId: f.get('authorId') || undefined,
        tag: f.get('tag') || undefined,
        q: (f.get('q') || '').trim() || undefined
      };
      loadAnnotations(true);
    });
    document.getElementById('cab-remark-reset').addEventListener('click', function(){
      document.getElementById('cab-remark-filters').reset();
      state.ann.filters = {};
      loadAnnotations(true);
    });
    document.getElementById('cab-remark-more').addEventListener('click', function(){ loadAnnotations(false); });
    loadTagOptions();
  }

  /** Fill the tag filter from the vocabulary actually in use, most used first. */
  async function loadTagOptions(){
    var sel = document.getElementById('cab-remark-tag-filter');
    if (!sel) return;
    var data;
    try { data = await apiGet('/api/tags'); }
    catch (e) { return; }
    var current = sel.value;
    sel.innerHTML = '<option value="">Любой</option>' + (data.tags || []).map(function(t){
      return '<option value="' + escapeHtml(t.tag) + '">' + escapeHtml(t.tag) + ' (' + t.count + ')</option>';
    }).join('');
    sel.value = current;
  }

  async function loadAnnotations(reset){
    if (reset) { state.ann.offset = 0; state.ann.items = []; }
    var params = Object.assign({}, state.ann.filters, { limit: state.ann.limit, offset: state.ann.offset });
    var data;
    try { data = await apiGet('/api/remarks' + qs(params)); }
    catch (e) { return; }
    state.ann.items = reset ? data.items : state.ann.items.concat(data.items);
    state.ann.total = data.total;
    state.ann.offset = state.ann.offset + data.items.length;

    updateAuthorOptions(
      document.getElementById('cab-remark-author-filter'), data.items, 'authorId',
      function(a){ return a.authorName; }
    );
    await prefetchManifests(data.items.map(function(a){ return a.docId; }));
    renderAnnotationsRows();
  }

  function renderAnnotationsRows(){
    var tbody = document.getElementById('cab-remark-tbody');
    tbody.innerHTML = state.ann.items.map(function(a){
      var link = annotationLink(a.docId, a.pageNum, a.remarkId);
      var openBtn = link
        ? '<a href="' + escapeHtml(link) + '" target="_blank" rel="noopener">Открыть</a>'
        : '<span class="cab-muted" title="страница вне легаси-нумерации">Открыть</span>';
      var editBtn = '<a href="' + escapeHtml(editorLink(a.docId, a.pageNum, a.remarkId)) +
        '" target="_blank" rel="noopener">Править</a>';
      var toggleLabel = a.status === 'draft' ? 'Опубликовать' : (a.status === 'published' ? 'В черновик' : '');
      var toggleBtn = toggleLabel
        ? '<button type="button" class="cab-remark-toggle" data-ann="' + escapeHtml(a.remarkId) + '" data-doc="' + escapeHtml(a.docId) +
          '" data-page="' + escapeHtml(a.pageNum) + '" data-newstatus="' + (a.status === 'draft' ? 'published' : 'draft') + '">' + toggleLabel + '</button>'
        : '';
      var delBtn = a.status !== 'deleted'
        ? '<button type="button" class="cab-btn-secondary cab-remark-delete" data-ann="' + escapeHtml(a.remarkId) + '" data-doc="' + escapeHtml(a.docId) +
          '" data-page="' + escapeHtml(a.pageNum) + '">Удалить</button>'
        : '';
      var histBtn = '<button type="button" class="cab-btn-secondary cab-remark-history" data-ann="' + escapeHtml(a.remarkId) + '" data-doc="' + escapeHtml(a.docId) + '">История</button>';
      // Вынести на оценку может только админ: пул решает, что показывают
      // людям снаружи.
      var poolBtn = state.user && state.user.role === 'admin'
        ? '<button type="button" class="cab-btn-secondary cab-remark-pool" data-ann="' + escapeHtml(a.remarkId) +
          '" data-doc="' + escapeHtml(a.docId) + '" data-page="' + escapeHtml(a.pageNum) + '">В опрос</button>'
        : '';
      var badgeClass = 'cab-badge-' + a.status;
      return '<tr>' +
        '<td>' + escapeHtml(a.docId) + '</td>' +
        '<td>' + escapeHtml(pageDisplay(a.docId, a.pageNum)) + '</td>' +
        '<td>' + escapeHtml(a.kind) + '</td>' +
        '<td><span class="cab-badge ' + badgeClass + '">' + escapeHtml(a.status) + '</span></td>' +
        '<td>' + escapeHtml(a.authorName || '—') + '</td>' +
        '<td>' + escapeHtml(formatDate(a.updatedAt)) + '</td>' +
        '<td>' + escapeHtml((a.text || '').slice(0, 80)) + '</td>' +
        '<td class="cab-row-actions">' + openBtn + editBtn + toggleBtn + delBtn + histBtn + poolBtn + '</td>' +
      '</tr>';
    }).join('');

    document.getElementById('cab-remark-empty').style.display = state.ann.items.length ? 'none' : '';
    document.getElementById('cab-remark-more').style.display = state.ann.items.length < state.ann.total ? '' : 'none';

    Array.prototype.forEach.call(tbody.querySelectorAll('.cab-remark-toggle'), function(btn){
      btn.addEventListener('click', function(){
        toggleAnnotationStatus(btn.dataset.doc, btn.dataset.page, btn.dataset.ann, btn.dataset.newstatus);
      });
    });
    Array.prototype.forEach.call(tbody.querySelectorAll('.cab-remark-delete'), function(btn){
      btn.addEventListener('click', function(){ deleteAnnotation(btn.dataset.doc, btn.dataset.page, btn.dataset.ann); });
    });
    Array.prototype.forEach.call(tbody.querySelectorAll('.cab-remark-pool'), function(btn){
      btn.addEventListener('click', function(){
        addToPool(btn.dataset.doc, btn.dataset.page, btn.dataset.ann);
      });
    });
    Array.prototype.forEach.call(tbody.querySelectorAll('.cab-remark-history'), function(btn){
      btn.addEventListener('click', function(){
        state.hist.filters = { docId: btn.dataset.doc, remarkId: btn.dataset.ann };
        switchTab('history');
        var docSel = document.querySelector('#cab-hist-filters select[name="docId"]');
        if (docSel) docSel.value = btn.dataset.doc;
        var actSel = document.querySelector('#cab-hist-filters select[name="action"]');
        if (actSel) actSel.value = '';
        loadHistory(true);
      });
    });
  }

  async function toggleAnnotationStatus(docId, pageKey, remarkId, newStatus){
    var item = state.ann.items.find(function(a){ return a.docId === docId && a.pageNum === pageKey && a.remarkId === remarkId; });
    if (!item) return;
    var body = { kind: item.kind, text: item.text, status: newStatus };
    if (item.coordX != null && item.coordY != null) body.coords = [item.coordX, item.coordY];
    try {
      await apiMutate('PUT', '/api/editor/' + encodeURIComponent(docId) + '/' + encodeURIComponent(pageKey) + '/' + encodeURIComponent(remarkId), body);
      setStatus('Статус обновлён', false);
      loadAnnotations(true);
    } catch (e) { /* status already shown */ }
  }

  async function deleteAnnotation(docId, pageKey, remarkId){
    if (!window.confirm('Удалить замечание?')) return;
    try {
      await apiMutate('DELETE', '/api/editor/' + encodeURIComponent(docId) + '/' + encodeURIComponent(pageKey) + '/' + encodeURIComponent(remarkId));
      setStatus('Замечание удалено', false);
      loadAnnotations(true);
    } catch (e) { /* status already shown */ }
  }

  // ===== history tab =====

  function renderHistoryShell(){
    var host = document.getElementById('cab-tab-history');
    host.innerHTML =
      '<form id="cab-hist-filters" class="cab-filters">' +
        '<label>Документ<select name="docId">' + docOptionsHtml() + '</select></label>' +
        '<label>Автор<select name="authorId" id="cab-hist-author-filter"><option value="">Все авторы</option></select></label>' +
        '<label>Что изменилось<select name="changed">' + CHANGED_OPTIONS + '</select></label>' +
        '<button type="submit">Применить</button>' +
        '<button type="button" class="cab-btn-secondary" id="cab-hist-reset">Сбросить</button>' +
      '</form>' +
      '<div id="cab-hist-remark-filter-note" class="cab-notice" style="display:none;"></div>' +
      '<div id="cab-hist-list"></div>' +
      '<div id="cab-hist-empty" style="display:none;">История пуста.</div>' +
      '<button type="button" id="cab-hist-more" class="cab-load-more" style="display:none;">Показать ещё</button>';

    document.getElementById('cab-hist-filters').addEventListener('submit', function(ev){
      ev.preventDefault();
      var f = new FormData(ev.target);
      state.hist.filters = {
        docId: f.get('docId') || undefined,
        authorId: f.get('authorId') || undefined,
        // Фильтр по составу изменения, а не по происхождению записи: «покажи
        // только правки текста» — вопрос про changed, не про action.
        changed: f.get('changed') || undefined
      };
      loadHistory(true);
    });
    document.getElementById('cab-hist-reset').addEventListener('click', function(){
      document.getElementById('cab-hist-filters').reset();
      state.hist.filters = {};
      loadHistory(true);
    });
    document.getElementById('cab-hist-more').addEventListener('click', function(){ loadHistory(false); });
  }

  async function loadHistory(reset){
    if (reset) { state.hist.offset = 0; state.hist.items = []; }
    var params = Object.assign({}, state.hist.filters, { limit: state.hist.limit, offset: state.hist.offset });
    var data;
    try { data = await apiGet('/api/history' + qs(params)); }
    catch (e) { return; }
    state.hist.items = reset ? data.items : state.hist.items.concat(data.items);
    state.hist.hasMore = data.hasMore;
    state.hist.offset = state.hist.offset + data.items.length;

    updateAuthorOptions(
      document.getElementById('cab-hist-author-filter'), data.items, 'authorId',
      function(h){ return h.authorName; }
    );
    await prefetchManifests(data.items.map(function(h){ return h.docId; }));
    renderHistoryList();
  }

  //: Словарь действий живёт на сервере (scripts/api/remark_actions.py), оттуда
  //: же приходит готовый ярлык каждой ревизии — здесь только значения фильтра.
  var CHANGED_OPTIONS = [
    ['', 'Любое'],
    ['text', 'правка текста'],
    ['coords', 'перенос маркера'],
    ['kind', 'смена вида'],
    ['publish', 'публикация'],
    ['unpublish', 'возврат в черновики'],
    ['delete', 'удаление'],
    ['restore', 'восстановление'],
    ['category', 'смена категории'],
    ['tags', 'правка тегов'],
    ['revert', 'откат']
  ].map(function(pair){
    return '<option value="' + pair[0] + '">' + pair[1] + '</option>';
  }).join('');

  function renderHistoryList(){
    var note = document.getElementById('cab-hist-remark-filter-note');
    if (state.hist.filters.remarkId) {
      note.style.display = '';
      note.innerHTML = 'Фильтр по замечанию ' + escapeHtml(state.hist.filters.remarkId) +
        ' в документе ' + escapeHtml(state.hist.filters.docId || '') +
        ' <button type="button" class="cab-btn-secondary" id="cab-hist-clear-remark">Сбросить фильтр</button>';
      document.getElementById('cab-hist-clear-remark').addEventListener('click', function(){
        state.hist.filters = {};
        loadHistory(true);
      });
    } else {
      note.style.display = 'none';
    }

    var host = document.getElementById('cab-hist-list');
    host.innerHTML = state.hist.items.map(function(h){
      var snap = h.snapshot || {};
      return '<div class="cab-history-item">' +
        '<div class="cab-history-meta">' + escapeHtml(formatDate(h.createdAt)) + ' · ' + escapeHtml(h.authorName || '—') + ' · ' +
          escapeHtml(h.actionLabel || h.action) + ' · ' + escapeHtml(h.docId) + ' / ' +
          escapeHtml(pageDisplay(h.docId, h.pageNum)) + ' / ' + escapeHtml(h.remarkId) +
        '</div>' +
        '<div class="cab-history-text">' + escapeHtml((snap.text || '').slice(0, 200)) + '</div>' +
        '<button type="button" class="cab-hist-revert" data-hist="' + h.id + '">Откатить к этому состоянию</button>' +
      '</div>';
    }).join('');

    document.getElementById('cab-hist-empty').style.display = state.hist.items.length ? 'none' : '';
    document.getElementById('cab-hist-more').style.display = state.hist.hasMore ? '' : 'none';

    Array.prototype.forEach.call(host.querySelectorAll('.cab-hist-revert'), function(btn){
      btn.addEventListener('click', function(){ revertHistory(btn.dataset.hist); });
    });
  }

  async function revertHistory(histId){
    if (!window.confirm('Откатить замечание к этому состоянию?')) return;
    try {
      await apiMutate('POST', '/api/history/' + encodeURIComponent(histId) + '/revert');
      setStatus('Откат выполнен', false);
      loadHistory(true);
      loadAnnotations(true);
    } catch (e) { /* status already shown */ }
  }

  // ===== admin tab =====

  async function loadAdmin(){
    state.admin.loaded = true;
    var host = document.getElementById('cab-tab-admin');
    host.innerHTML =
      '<div class="cab-admin-section" id="cab-admin-invites"><h3>Приглашения</h3></div>' +
      '<div class="cab-admin-section" id="cab-admin-users"><h3>Пользователи</h3></div>' +
      '<div class="cab-admin-section" id="cab-admin-publish"><h3>Публикация</h3>' +
        '<button type="button" id="cab-admin-publish-all-btn">Перепубликовать всё</button> ' +
        '<span id="cab-admin-publish-result"></span>' +
      '</div>' +
      '<div class="cab-admin-section" id="cab-admin-logs"><h3>Логи</h3>' +
        '<button type="button" id="cab-admin-logs-refresh" class="cab-btn-secondary">Обновить</button>' +
        '<pre id="cab-admin-logs-pre"></pre>' +
      '</div>';

    document.getElementById('cab-admin-publish-all-btn').addEventListener('click', async function(){
      try {
        var result = await apiMutate('POST', '/api/admin/publish-all');
        document.getElementById('cab-admin-publish-result').textContent =
          'Готово: ' + result.pages + ' стр., ошибок: ' + result.failed;
      } catch (e) { /* status already shown */ }
    });
    document.getElementById('cab-admin-logs-refresh').addEventListener('click', loadAdminLogs);

    await Promise.all([loadInvites(), loadAdminUsers(), loadAdminLogs()]);
  }

  // Приглашение — одноразовый код, который админ передаёт человеку вне системы.
  // Ни email, ни имени приглашаемого система не знает: именной список — это и
  // есть то, чего мы не храним (docs/anonymity-model.md).
  async function loadInvites(){
    var host = document.getElementById('cab-admin-invites');
    var data;
    try { data = await apiGet('/api/admin/invites'); }
    catch (e) { return; }
    var rows = (data.invites || []).map(function(inv){
      var used = inv.usedAt
        ? 'использовано ' + escapeHtml(formatDate(inv.usedAt))
        : 'не использовано';
      return '<tr><td><code>' + escapeHtml(inv.codeHash.slice(0, 12)) + '…</code></td>' +
        '<td>' + escapeHtml(inv.role) + '</td>' +
        '<td>' + escapeHtml(inv.note || '') + '</td>' +
        '<td>' + escapeHtml(formatDate(inv.createdAt)) + '</td>' +
        '<td>' + used + '</td>' +
        '<td>' + (inv.usedAt ? '' :
          '<button type="button" class="cab-btn-secondary cab-invite-del" data-hash="' +
          escapeHtml(inv.codeHash) + '">Отозвать</button>') + '</td></tr>';
    }).join('');
    host.innerHTML = '<h3>Приглашения</h3>' +
      '<p class="cab-hint">Код показывается один раз при выписке — передайте его ' +
      'вне системы. Потерянный код не восстанавливается, выпишите новый.</p>' +
      '<table class="cab-table"><thead><tr><th>Код (хеш)</th><th>Роль</th><th>Пометка</th>' +
      '<th>Выписано</th><th>Статус</th><th></th></tr></thead><tbody>' + rows + '</tbody></table>' +
      '<form id="cab-invite-form" class="cab-admin-form">' +
        '<select name="role">' +
          '<option value="editor">editor</option>' +
          '<option value="reviewer">reviewer</option>' +
          '<option value="admin">admin</option>' +
          '<option value="viewer">viewer</option>' +
        '</select>' +
        '<input type="text" name="note" placeholder="пометка (без имён)" maxlength="200" />' +
        '<button type="submit">Выписать</button>' +
      '</form>' +
      '<div id="cab-invite-code"></div>';

    document.getElementById('cab-invite-form').addEventListener('submit', async function(ev){
      ev.preventDefault();
      var f = new FormData(ev.target);
      try {
        var result = await apiMutate('POST', '/api/admin/invites',
                                     { role: f.get('role'), note: f.get('note') || null });
        document.getElementById('cab-invite-code').innerHTML =
          '<p class="cab-invite-code">Код: <code>' + escapeHtml(result.code) + '</code><br>' +
          'Скопируйте сейчас — второй раз он не покажется.</p>';
        setStatus('Приглашение выписано', false);
        // Список перерисовывается отдельно, чтобы код остался на экране.
        loadInvites().then(function(){
          document.getElementById('cab-invite-code').innerHTML =
            '<p class="cab-invite-code">Код: <code>' + escapeHtml(result.code) + '</code><br>' +
            'Скопируйте сейчас — второй раз он не покажется.</p>';
        });
      } catch (e) { /* status already shown */ }
    });
    Array.prototype.forEach.call(host.querySelectorAll('.cab-invite-del'), function(btn){
      btn.addEventListener('click', async function(){
        try {
          await apiMutate('DELETE', '/api/admin/invites/' + encodeURIComponent(btn.dataset.hash));
          loadInvites();
        } catch (e) { /* status already shown */ }
      });
    });
  }

  async function loadAdminUsers(){
    var host = document.getElementById('cab-admin-users');
    var data;
    try { data = await apiGet('/api/admin/users'); }
    catch (e) { return; }
    var rows = (data.users || []).map(function(u){
      return '<tr><td>' + escapeHtml(u.displayName || ('Участник №' + u.id)) + '</td>' +
        '<td>' + escapeHtml(u.kind === 'agent' ? 'агент' : 'человек') + '</td>' +
        '<td>' + escapeHtml(ROLE_LABELS[u.role] || u.role) + '</td>' +
        '<td>' + escapeHtml(formatDate(u.createdAt)) + '</td>' +
        '<td>' + escapeHtml(formatDate(u.lastLoginAt)) + '</td></tr>';
    }).join('');
    host.innerHTML = '<h3>Участники</h3>' +
      '<table class="cab-table"><thead><tr><th>Псевдоним</th><th>Вид</th><th>Роль</th>' +
      '<th>Создан</th><th>Последний вход</th></tr></thead><tbody>' + rows + '</tbody></table>';
  }

  async function loadAdminLogs(){
    var pre = document.getElementById('cab-admin-logs-pre');
    var data;
    try { data = await apiGet('/api/logs?lines=200'); }
    catch (e) { return; }
    pre.textContent = (data.logs || []).map(function(l){ return l.timestamp + ' | ' + l.level + ' | ' + l.message; }).join('\n');
  }

  // ===== опрос =====
  //
  // Две вещи на одной вкладке, и разделять их не стоит: пул — это список
  // заданных вопросов, результаты — полученные на них ответы, и смотрят на
  // них подряд. Оценки, поставленные участниками круга в /app/, сюда не
  // попадают: это другой голос, и складывать их в одно среднее нельзя.

  function renderSurveyShell(){
    document.getElementById('cab-tab-survey').innerHTML =
      '<div class="cab-admin-section">' +
        '<form id="cab-survey-filters" class="cab-filters">' +
          '<label>Документ <select name="docId">' + docOptionsHtml() + '</select></label>' +
          '<button type="submit">Показать</button>' +
        '</form>' +
        '<p class="cab-muted">Опросник: ' +
          '<a href="../survey/" target="_blank" rel="noopener">/survey/</a> — ' +
          'ссылка для тех, кого просят оценить. Входа не требует.</p>' +
      '</div>' +
      '<div class="cab-admin-section">' +
        '<h3>Результаты</h3>' +
        '<table class="cab-table"><thead><tr>' +
          '<th>Документ</th><th>Стр.</th><th>Замечание</th><th>Ответивших</th>' +
          '<th>Интересность</th><th>Важность</th><th>Публиковать</th><th></th>' +
        '</tr></thead><tbody id="cab-survey-tbody"></tbody></table>' +
        '<p id="cab-survey-empty" class="cab-muted">Ответов пока нет.</p>' +
        '<button type="button" id="cab-survey-more" class="cab-load-more" style="display:none;">Ещё</button>' +
      '</div>' +
      '<div class="cab-admin-section">' +
        '<h3>Пул для оценки</h3>' +
        '<p class="cab-muted">Из этого списка опросник выдаёт людям случайные ' +
          'замечания. Добавить — кнопкой «В опрос» на вкладке «Замечания».</p>' +
        '<table class="cab-table"><thead><tr>' +
          '<th>Документ</th><th>Стр.</th><th>Замечание</th><th>Ответов</th><th></th>' +
        '</tr></thead><tbody id="cab-pool-tbody"></tbody></table>' +
        '<p id="cab-pool-empty" class="cab-muted">Пул пуст: опросу нечего показывать.</p>' +
      '</div>';

    document.getElementById('cab-survey-filters').addEventListener('submit', function(ev){
      ev.preventDefault();
      state.survey.docId = ev.target.elements.docId.value;
      loadSurvey(true);
    });
    document.getElementById('cab-survey-more').addEventListener('click', function(){
      loadSurveyResults(false);
    });
  }

  function surveyRowLink(item){
    var link = annotationLink(item.docId, item.pageNum, item.remarkId);
    return link
      ? '<a href="' + escapeHtml(link) + '" target="_blank" rel="noopener">Открыть</a>'
      : '<span class="cab-muted" title="страница вне легаси-нумерации">Открыть</span>';
  }

  function renderSurveyResults(){
    var tbody = document.getElementById('cab-survey-tbody');
    tbody.innerHTML = state.survey.results.map(function(item){
      var yes = item.admissibility.yes, no = item.admissibility.no;
      // Расклад, а не среднее: среднее по «да или нет» ничего не сообщает.
      var verdict = (yes + no) ? (yes + ' да / ' + no + ' нет') : '—';
      return '<tr>' +
        '<td>' + escapeHtml(item.docId) + '</td>' +
        '<td>' + escapeHtml(pageDisplay(item.docId, item.pageNum)) + '</td>' +
        '<td>' + escapeHtml((item.text || item.remarkId).slice(0, 90)) + '</td>' +
        '<td>' + item.raters + '</td>' +
        '<td>' + (item.interest.average == null ? '—' : item.interest.average + ' (' + item.interest.count + ')') + '</td>' +
        '<td>' + (item.importance.average == null ? '—' : item.importance.average + ' (' + item.importance.count + ')') + '</td>' +
        '<td>' + escapeHtml(verdict) + '</td>' +
        '<td class="cab-row-actions">' + surveyRowLink(item) + '</td>' +
      '</tr>';
    }).join('');
    document.getElementById('cab-survey-empty').style.display = state.survey.results.length ? 'none' : '';
    document.getElementById('cab-survey-more').style.display =
      state.survey.results.length < state.survey.total ? '' : 'none';
  }

  function renderSurveyPool(){
    var tbody = document.getElementById('cab-pool-tbody');
    tbody.innerHTML = state.survey.pool.map(function(item){
      return '<tr>' +
        '<td>' + escapeHtml(item.docId) + '</td>' +
        '<td>' + escapeHtml(pageDisplay(item.docId, item.pageNum)) + '</td>' +
        '<td>' + escapeHtml((item.text || item.remarkId).slice(0, 90)) + '</td>' +
        '<td>' + (item.answers || 0) + '</td>' +
        '<td class="cab-row-actions">' + surveyRowLink(item) +
          '<button type="button" class="cab-btn-secondary cab-pool-remove" data-doc="' +
            escapeHtml(item.docId) + '" data-page="' + escapeHtml(item.pageNum) +
            '" data-ann="' + escapeHtml(item.remarkId) + '">Убрать</button>' +
        '</td>' +
      '</tr>';
    }).join('');
    document.getElementById('cab-pool-empty').style.display = state.survey.pool.length ? 'none' : '';
    Array.prototype.forEach.call(tbody.querySelectorAll('.cab-pool-remove'), function(btn){
      btn.addEventListener('click', function(){
        removeFromPool(btn.dataset.doc, btn.dataset.page, btn.dataset.ann);
      });
    });
  }

  async function loadSurveyResults(reset){
    if (reset) state.survey.offset = 0;
    var data;
    try {
      data = await apiGet('/api/survey/results' + qs({
        docId: state.survey.docId, limit: state.survey.limit, offset: state.survey.offset
      }));
    } catch (e) { return; }
    state.survey.results = reset ? (data.items || []) : state.survey.results.concat(data.items || []);
    state.survey.total = data.total || 0;
    state.survey.offset = state.survey.results.length;
    await prefetchManifests(state.survey.results.map(function(i){ return i.docId; }));
    renderSurveyResults();
  }

  async function loadSurveyPool(){
    var data;
    try { data = await apiGet('/api/survey/pool' + qs({ docId: state.survey.docId })); }
    catch (e) { return; }
    state.survey.pool = data.items || [];
    await prefetchManifests(state.survey.pool.map(function(i){ return i.docId; }));
    renderSurveyPool();
  }

  async function loadSurvey(refresh){
    if (!state.survey.loaded || refresh) {
      if (!state.survey.loaded) renderSurveyShell();
      state.survey.loaded = true;
    }
    await Promise.all([loadSurveyResults(true), loadSurveyPool()]);
  }

  async function addToPool(docId, pageKey, remarkId){
    try {
      await apiMutate('POST', '/api/survey/pool',
                      { docId: docId, pageKey: pageKey, remarkId: remarkId });
      setStatus('Вынесено на оценку: ' + remarkId, false);
      if (state.survey.loaded) loadSurveyPool();
    } catch (e) { /* сообщение уже показано */ }
  }

  async function removeFromPool(docId, pageKey, remarkId){
    // Ответы при этом остаются: снять вопрос с раздачи и стереть ответы —
    // разные действия, и второе здесь не подразумевается.
    try {
      await apiMutate('DELETE', '/api/survey/pool/' + encodeURIComponent(docId) + '/' +
                      encodeURIComponent(pageKey) + '/' + encodeURIComponent(remarkId));
      setStatus('Убрано из опроса: ' + remarkId, false);
      loadSurveyPool();
    } catch (e) { /* сообщение уже показано */ }
  }

  // ===== init =====

  async function init(){
    bindTabs();
    bindLoginForm();
    await refreshUserAndRender();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function(){ init(); }, { once: true });
  } else {
    init();
  }
})();
