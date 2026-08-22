(function () {
  'use strict';

  /**
   * Карточка комментария: история правок, форма и живой просмотр рядом.
   *
   * Адрес: /app/#/ann/<docId>/<pageKey>/<annId>
   *
   * Просмотр — iframe на ту же страницу, которую видит читатель, с ?only=<id>.
   * Это намеренно: второй реализации рендера маркеров не будет, и «как оно
   * выглядит» означает ровно то, что увидит читатель, включая цвет категории.
   * Просмотрщик при этом остаётся полностью статическим — редактор к нему не
   * прикасается, только открывает по ссылке.
   */

  var auth = window.RedPenAuth;
  var cats = window.RedPenCategories;

  var state = {
    user: null,
    ref: null,        // {docId, pageKey, annId}
    annotation: null,
    manifest: {},     // docId -> {page_006: "6"}
    dirty: false
  };

  // --- мелочи -------------------------------------------------------------

  function el(id) { return document.getElementById(id); }

  function setStatus(text, isError) {
    var host = el('app-status');
    host.textContent = text || '';
    host.hidden = !text;
    host.classList.toggle('is-error', !!isError);
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function formatDay(value) {
    if (!value) return '';
    // Метки времени в журнале ревизий — с точностью до дня (см.
    // docs/anonymity-model.md); показываем ровно то, что есть.
    return String(value).slice(0, 10).split('-').reverse().join('.');
  }

  function apiUrl(path) {
    return (window.REDPEN_API_BASE || '') + path;
  }

  async function apiGet(path) {
    var res = await fetch(apiUrl(path), { credentials: 'include' });
    if (res.status === 401) { showLogin(); throw new Error('unauthorized'); }
    if (!res.ok) { setStatus('Ошибка запроса: ' + res.status, true); throw new Error('http ' + res.status); }
    return res.json();
  }

  async function apiMutate(method, path, body, isRetry) {
    var csrf = await auth.getCsrf(!!isRetry);
    var res = await fetch(apiUrl(path), {
      method: method,
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf.csrfToken },
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'include'
    });
    // Токен CSRF перевыписывается на каждый GET /api/auth/csrf, поэтому две
    // вкладки гасят друг друга; один тихий повтор это лечит.
    if (res.status === 403 && !isRetry) return apiMutate(method, path, body, true);
    if (res.status === 401) { showLogin(); throw new Error('unauthorized'); }
    if (res.status === 409) {
      setStatus('Страницу успели изменить в другом месте. Нажмите «Перечитать».', true);
      throw new Error('conflict');
    }
    if (!res.ok) {
      var detail = '';
      try { detail = (await res.json()).detail || ''; } catch (e) {}
      setStatus('Не сохранено: ' + (detail || res.status), true);
      throw new Error('http ' + res.status);
    }
    return res.json();
  }

  // --- адрес --------------------------------------------------------------

  function parseHash() {
    var m = (window.location.hash || '').match(/^#\/ann\/([^/]+)\/([^/]+)\/(.+)$/);
    if (!m) return null;
    return {
      docId: decodeURIComponent(m[1]),
      pageKey: decodeURIComponent(m[2]),
      annId: decodeURIComponent(m[3])
    };
  }

  async function pageLabel(docId, pageKey) {
    if (!state.manifest[docId]) {
      try {
        var res = await fetch('../' + encodeURIComponent(docId) + '/metadata.json');
        var meta = await res.json();
        var map = {};
        (meta.pages || []).forEach(function (p) { map[p.file] = String(p.label); });
        state.manifest[docId] = map;
      } catch (e) {
        state.manifest[docId] = {};
      }
    }
    return state.manifest[docId]['page_' + pageKey] || String(parseInt(pageKey, 10) || pageKey);
  }

  // --- вход ---------------------------------------------------------------

  function showLogin() {
    el('app-main').hidden = true;
    el('app-login').hidden = false;
    auth.renderGoogleButton(el('app-google-btn'), function (user, error) {
      var errEl = el('app-login-error');
      if (error && error.message === 'invite_required') {
        errEl.textContent = 'Нужен код приглашения. Получите его у администратора.';
        return;
      }
      if (error || !user) { errEl.textContent = 'Не удалось войти через Google'; return; }
      errEl.textContent = '';
      start();
    }, function () {
      var input = el('app-invite-input');
      return input ? (input.value || '').trim() : null;
    });
  }

  function renderProfile() {
    var u = state.user;
    if (!u) { el('app-profile').innerHTML = ''; return; }
    var roles = { viewer: 'читатель', editor: 'редактор', reviewer: 'приёмщик', admin: 'админ' };
    el('app-profile').innerHTML =
      '<span class="app-who">' + escapeHtml(u.displayName || u.username || '') + '</span>' +
      '<span class="app-role">' + escapeHtml(roles[u.role] || u.role) + '</span>' +
      '<button type="button" id="app-logout">Выйти</button>';
    el('app-logout').addEventListener('click', async function () {
      await auth.logout();
      state.user = null;
      showLogin();
    });
  }

  // --- форма --------------------------------------------------------------

  function fillCategories(selected) {
    var select = el('f-category');
    var order = (cats && cats.PRECEDENCE ? cats.PRECEDENCE.slice() : []).concat(['other']);
    select.innerHTML = order.map(function (slug) {
      var title = (cats && cats.TITLES && cats.TITLES[slug]) || slug;
      return '<option value="' + escapeHtml(slug) + '">' + escapeHtml(title) + '</option>';
    }).join('');
    select.value = selected || 'other';
  }

  function fillForm(ann) {
    el('f-text').value = ann.text || '';
    fillCategories(ann.category);
    el('f-anntype').value = ann.annType || 'comment';
    el('f-status').value = ann.status === 'published' ? 'published' : 'draft';
    // Зеркальный тег категории производный — в поле его не показываем, иначе
    // человек попробует его править (и получит 400).
    el('f-tags').value = (ann.tags || []).filter(function (t) {
      return t.indexOf('cat:') !== 0 && t !== 'draft';
    }).join(', ');
    el('f-summary').value = '';
    setDirty(false);
    updateHint(ann);
  }

  function updateHint(ann) {
    var hint = el('f-hint');
    if (!ann) { hint.textContent = ''; return; }
    var source = {
      'default': 'категорию никто не назначал',
      'tags-backfill': 'категория угадана по тегам, требует проверки',
      'agent': 'категорию предложил агент, ждёт приёмки',
      'human': 'категорию выбрал человек'
    }[ann.categorySource] || '';
    hint.textContent = source;
    hint.className = 'app-hint' + (ann.categorySource === 'human' ? ' is-ok' : ' is-todo');
  }

  function setDirty(value) {
    state.dirty = value;
    el('f-save').disabled = !value;
  }

  function collectForm() {
    var tags = el('f-tags').value.split(',').map(function (t) { return t.trim().toLowerCase(); })
      .filter(Boolean);
    var body = {
      annType: el('f-anntype').value,
      text: el('f-text').value,
      status: el('f-status').value,
      category: el('f-category').value,
      tags: tags
    };
    var summary = el('f-summary').value.trim();
    if (summary) body.summary = summary;
    var ann = state.annotation;
    if (ann && ann.coordX != null && ann.coordY != null) body.coords = [ann.coordX, ann.coordY];
    return body;
  }

  // --- история ------------------------------------------------------------

  var ACTIONS = {
    create: 'создан', update: 'правка', delete: 'удалён',
    revert: 'откат', import: 'импорт'
  };

  function renderRevisions(items) {
    var host = el('app-revisions');
    if (!items.length) { host.innerHTML = '<li class="app-empty">Правок пока нет.</li>'; return; }
    // Старые сверху: историю читают сверху вниз, как она происходила.
    host.innerHTML = items.slice().reverse().map(function (rev) {
      var who = rev.authorName || (rev.authorId ? 'Участник №' + rev.authorId : 'без автора');
      var agent = rev.agentRunId ? ' <span class="app-agent">прогон #' + rev.agentRunId + '</span>' : '';
      var snap = rev.snapshot || {};
      var cat = snap.category ? (cats && cats.TITLES && cats.TITLES[snap.category]) || snap.category : '';
      return '<li class="app-rev">' +
        '<div class="app-rev-head">' +
          '<span class="app-rev-no">№' + (rev.revNo || '?') + '</span>' +
          '<span class="app-rev-action">' + escapeHtml(ACTIONS[rev.action] || rev.action) + '</span>' +
          '<span class="app-rev-who">' + escapeHtml(who) + agent + '</span>' +
          '<span class="app-rev-day">' + escapeHtml(formatDay(rev.createdAt)) + '</span>' +
        '</div>' +
        (rev.summary ? '<div class="app-rev-summary">' + escapeHtml(rev.summary) + '</div>' : '') +
        (cat ? '<div class="app-rev-cat">категория: ' + escapeHtml(cat) + '</div>' : '') +
        '<div class="app-rev-text">' + escapeHtml((snap.text || '').slice(0, 200)) + '</div>' +
      '</li>';
    }).join('');
  }

  // --- просмотр -----------------------------------------------------------

  //: Ширина, в которую рисуется просмотр. Больше брейкпоинта просмотрщика
  //: (767px), иначе в редакторе показывался бы мобильный вид.
  var PREVIEW_WIDTH = 1200;

  function fitPreview() {
    var fit = el('app-preview-fit');
    var frame = el('app-preview');
    if (!fit || !frame) return;
    var available = fit.clientWidth;
    if (!available) return;
    var scale = Math.min(1, available / PREVIEW_WIDTH);
    frame.style.setProperty('--preview-width', PREVIEW_WIDTH + 'px');
    // Высота в собственных координатах iframe: то, что после сжатия займёт
    // всю панель. Без этого низ страницы обрезался бы.
    frame.style.setProperty('--preview-height', Math.round(fit.clientHeight / scale) + 'px');
    frame.style.transform = 'scale(' + scale + ')';
  }

  async function renderPreview(ref) {
    var label = await pageLabel(ref.docId, ref.pageKey);
    var url = '../' + encodeURIComponent(ref.docId) + '/pages/' +
              encodeURIComponent(label) + '/?only=' + encodeURIComponent(ref.annId);
    el('app-preview').src = url;
    el('app-preview-link').href = url;
    fitPreview();
    return label;
  }

  async function renderBreadcrumbs(ref, section, label) {
    el('app-breadcrumbs').innerHTML =
      '<span>' + escapeHtml(ref.docId) + '</span>' +
      (section ? '<span>' + escapeHtml(section.title) + '</span>' : '<span>вне параграфа</span>') +
      '<span>стр. ' + escapeHtml(label) + '</span>' +
      '<code>' + escapeHtml(ref.annId) + '</code>';
  }

  // --- загрузка карточки --------------------------------------------------

  async function loadCard(ref) {
    state.ref = ref;
    var path = '/api/annotations/' + encodeURIComponent(ref.docId) + '/' +
               encodeURIComponent(ref.pageKey) + '/' + encodeURIComponent(ref.annId);
    var data = await apiGet(path);
    state.annotation = data.annotation;
    fillForm(data.annotation);

    var label = await renderPreview(ref);
    await renderBreadcrumbs(ref, data.section, label);

    var hist = await apiGet('/api/history?docId=' + encodeURIComponent(ref.docId) +
                            '&pageKey=' + encodeURIComponent(ref.pageKey) +
                            '&annId=' + encodeURIComponent(ref.annId) + '&limit=50');
    renderRevisions(hist.items || []);
  }

  async function save() {
    var ref = state.ref;
    if (!ref) return;
    var body = collectForm();
    if (!body.text.trim()) { setStatus('Текст комментария пуст.', true); return; }
    await apiMutate('PUT', '/api/editor/' + encodeURIComponent(ref.docId) + '/' +
                    encodeURIComponent(ref.pageKey) + '/' + encodeURIComponent(ref.annId), body);
    setStatus('Сохранено.', false);
    await loadCard(ref);
    // Просмотр перечитываем принудительно: страница уже перерисована на сервере,
    // но у iframe тот же адрес, и сам он не обновится.
    var frame = el('app-preview');
    frame.src = frame.src;
  }

  // --- запуск -------------------------------------------------------------

  function wireForm() {
    ['f-text', 'f-category', 'f-anntype', 'f-status', 'f-tags', 'f-summary'].forEach(function (id) {
      el(id).addEventListener('input', function () { setDirty(true); });
      el(id).addEventListener('change', function () { setDirty(true); });
    });
    el('app-form').addEventListener('submit', function (event) {
      event.preventDefault();
      save().catch(function () {});
    });
    el('f-reload').addEventListener('click', function () {
      if (state.ref) loadCard(state.ref).catch(function () {});
    });
    window.addEventListener('beforeunload', function (event) {
      if (!state.dirty) return;
      event.preventDefault();
      event.returnValue = '';
    });
  }

  async function route() {
    var ref = parseHash();
    if (!ref) {
      setStatus('Адрес вида #/ann/<документ>/<страница>/<id> — например ' +
                '#/ann/medinsky11klass/006/ann-p006-1', false);
      el('app-main').hidden = true;
      return;
    }
    setStatus('');
    el('app-main').hidden = false;
    try { await loadCard(ref); } catch (e) { /* сообщение уже показано */ }
  }

  async function start() {
    try {
      state.user = await auth.me();
    } catch (e) {
      showLogin();
      return;
    }
    if (!state.user) { showLogin(); return; }
    el('app-login').hidden = true;
    renderProfile();
    if (state.user.role === 'viewer') {
      setStatus('У вас роль читателя — править нельзя.', true);
    }
    await route();
  }

  window.addEventListener('resize', fitPreview);
  window.addEventListener('hashchange', function () { route().catch(function () {}); });
  wireForm();
  start();
})();
