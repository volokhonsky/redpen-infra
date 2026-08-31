(function () {
  'use strict';

  /**
   * core.js — каркас рабочего места: состояние, роутер, вход, роли.
   *
   * Рабочее место одно (`/work/`), и разграничение в нём проходит по ролям, а
   * не по экранам. До 2026-08-31 то же самое было разложено на два
   * приложения — `/app/` (разбор) и `/cabinet/` (списки и админка), — которые
   * делали одно и то же по-разному: список замечаний был в обоих и ни в одном
   * не был полным, смена статуса шла узким PATCH в одном и полным PUT в
   * другом, а экран очереди приёмки не имел ссылки ниоткуда.
   *
   * Экраны разложены по файлам: pages.js — параграфы и страница со сканом,
   * remarks.js — карточка, очередь и сквозной поиск, admin.js — история,
   * опрос и админские таблицы. Общее — здесь, в `window.RedPenWork`.
   */

  var W = { state: null };
  window.RedPenWork = W;

  var auth = window.RedPenAuth;
  var cats = window.RedPenCategories;
  var api = window.RedPenApi;
  var markers = window.RedPenMarkers;
  var preview = window.RedPenPreview;

  var state = {
    user: null,
    ref: null,        // {docId, pageKey, remarkId}
    remark: null,
    section: null,    // текущий параграф, чтобы перечитывать его по фильтрам
    queue: { items: [], index: 0, skipped: {} },
    dirty: false,
    scales: [],       // описание шкал оценки, приходит с сервера
    scaleByName: {},  // name -> описание шкалы, для подписей в ленте
    timeline: [],     // ревизии, оценки и комментарии одним списком
    textOnly: false,  // фильтр ленты «только правки текста»
    cardSha: null,    // serverPageSha страницы открытой карточки
    notes: [],
    replyTo: null,    // id корневого комментария, если пишем ответ
    // Экран страницы: скан с маркерами. sha — вход оптимистической блокировки,
    // приходит из GET /api/editor/... и уезжает обратно в каждой мутации.
    page: { docId: null, pageKey: null, remarks: [], sha: null,
            placing: false, pendingCoords: null, selectedId: null },
    // Сквозной поиск, история, опрос и админка — состояние экранов,
    // переехавших из кабинета.
    search: { filters: {}, items: [], total: 0, offset: 0, limit: 50, ready: false },
    hist: { filters: {}, items: [], offset: 0, limit: 50, ready: false },
    survey: { results: [], resultsTotal: 0, resultsOffset: 0, pool: [], ready: false },
    admin: { ready: false },
    docs: [],
    manifests: {}
  };
  W.state = state;


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


  //: Все экраны приложения. Показываем ровно один — так не приходится
  //: помнить, что спрятать при каждом переходе.
  var VIEWS = ['view-sections', 'view-section', 'view-queue', 'view-page', 'app-main'];

  function showView(id) {
    VIEWS.forEach(function (name) { el(name).hidden = name !== id; });
  }

  function apiGet(path) { return api.get(path); }

  function apiMutate(method, path, body) { return api.mutate(method, path, body); }


  //: Все экраны приложения. Показываем ровно один — так не приходится
  //: помнить, что спрятать при каждом переходе.
  var VIEWS = ['view-sections', 'view-section', 'view-queue', 'view-page',
               'app-main', 'view-remarks', 'view-history', 'view-survey',
               'view-admin'];

  function showView(id) {
    VIEWS.forEach(function (name) { el(name).hidden = name !== id; });
    var nav = el('app-nav');
    if (nav) {
      Array.prototype.forEach.call(nav.querySelectorAll('.app-nav-btn'), function (a) {
        a.classList.toggle('is-active', a.getAttribute('data-route') === W.navRoute);
      });
    }
  }

  // --- адрес --------------------------------------------------------------

  function parseHash() {
    var hash = window.location.hash || '';
    var ann = hash.match(/^#\/ann\/([^/]+)\/([^/]+)\/(.+)$/);
    if (ann) {
      return {
        view: 'ann',
        docId: decodeURIComponent(ann[1]),
        pageKey: decodeURIComponent(ann[2]),
        remarkId: decodeURIComponent(ann[3])
      };
    }
    if (/^#\/queue\b/.test(hash)) return { view: 'queue' };
    if (/^#\/remarks\b/.test(hash)) return { view: 'remarks' };
    if (/^#\/history\b/.test(hash)) return { view: 'history' };
    if (/^#\/survey\b/.test(hash)) return { view: 'survey' };
    if (/^#\/admin\b/.test(hash)) return { view: 'admin' };
    var page = hash.match(/^#\/page\/([^/]+)\/([^/]+)$/);
    if (page) {
      return {
        view: 'page',
        docId: decodeURIComponent(page[1]),
        pageKey: decodeURIComponent(page[2])
      };
    }
    var section = hash.match(/^#\/section\/([^/]+)\/(.+)$/);
    if (section) {
      return {
        view: 'section',
        docId: decodeURIComponent(section[1]),
        sectionId: decodeURIComponent(section[2])
      };
    }
    return { view: 'sections' };
  }

  function pageLabel(docId, pageKey) {
    return preview.pageLabel(docId, pageKey);
  }

  // --- вход ---------------------------------------------------------------

  function hasTokenAuthFlag() {
    try { return new URLSearchParams(window.location.search).get('auth') === 'token'; }
    catch (e) { return false; }
  }

  function showLogin() {
    showView(null);
    el('app-nav').hidden = true;
    el('app-viewer-notice').hidden = true;
    el('app-login').hidden = false;
    auth.renderGoogleButton(el('app-google-btn'), function (user, error) {
      var errEl = el('app-login-error');
      if (error && error.message === 'invite_required') {
        errEl.textContent = 'Нужен код приглашения. Получите его у администратора.';
        return;
      }
      if (error || !user) { errEl.textContent = 'Не удалось войти через Google'; return; }
      errEl.textContent = '';
      W.start();
    }, function () {
      var input = el('app-invite-input');
      return input ? (input.value || '').trim() : null;
    });

    if (hasTokenAuthFlag()) {
      el('app-token-login').hidden = false;
      el('app-token-submit').onclick = async function () {
        var token = (el('app-token-input').value || '').trim();
        if (!token) { el('app-login-error').textContent = 'Введите токен'; return; }
        try {
          await auth.loginWithToken(token);
        } catch (e) {
          el('app-login-error').textContent = 'Проверьте токен';
          return;
        }
        el('app-login-error').textContent = '';
        W.start();
      };
    }
  }

  function renderProfile() {
    var u = state.user;
    if (!u) { el('app-profile').innerHTML = ''; return; }
    var roles = { viewer: 'читатель', editor: 'редактор', admin: 'админ' };
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

  // --- просмотр -----------------------------------------------------------

  function fitFrame(fitId, frameId) {
    preview.fitFrame(fitId, frameId);
  }

  function fitPreview() {
    fitFrame('app-preview-fit', 'app-preview');
    fitFrame('q-preview-fit', 'q-preview');
  }

  async function renderPreview(ref) {
    var label = await pageLabel(ref.docId, ref.pageKey);
    var url = preview.remarkUrl(ref.docId, label, ref.remarkId);
    el('app-preview').src = url;
    el('app-preview-link').href = url;
    fitPreview();
    return label;
  }

  async function renderBreadcrumbs(ref, section, label) {
    var sectionLink = section
      ? '<a href="#/section/' + encodeURIComponent(ref.docId) + '/' +
        encodeURIComponent(section.sectionId) + '">' + escapeHtml(section.title) + '</a>'
      : 'вне параграфа';
    el('app-breadcrumbs').innerHTML =
      '<span><a href="#/">параграфы</a></span>' +
      '<span>' + escapeHtml(ref.docId) + '</span>' +
      '<span>' + sectionLink + '</span>' +
      '<span><a href="#/page/' + encodeURIComponent(ref.docId) + '/' +
        encodeURIComponent(ref.pageKey) + '">стр. ' + escapeHtml(label) + '</a></span>' +
      '<code>' + escapeHtml(ref.remarkId) + '</code>';
  }

  // --- категории и список документов --------------------------------------

  function catTitle(slug) {
    return (cats && cats.TITLES && cats.TITLES[slug]) || slug || '';
  }

  function catDot(slug) {
    var color = (cats && cats.COLORS && cats.COLORS[slug]) || '#546E7A';
    return '<span class="app-dot" style="background:' + color + '"></span>';
  }

  async function loadDocs() {
    var select = el('s-doc');
    if (select.options.length) return select.value;
    var stats = await apiGet('/api/stats');
    var docs = (stats.docs || []).map(function (d) { return d.docId; });
    if (!docs.length) docs = ['medinsky11klass'];
    select.innerHTML = docs.map(function (d) {
      return '<option value="' + escapeHtml(d) + '">' + escapeHtml(d) + '</option>';
    }).join('');
    select.addEventListener('change', function () { W.loadSections().catch(function () {}); });
    return select.value;
  }
  //: Список документов, единожды на сеанс: им пользуются и доска параграфов,
  //: и фильтры сквозного поиска, истории и опроса.
  async function docList() {
    if (state.docs.length) return state.docs;
    var stats = await apiGet('/api/stats');
    state.docs = (stats.docs || []).map(function (d) { return d.docId; });
    if (!state.docs.length) state.docs = ['medinsky11klass'];
    return state.docs;
  }

  function docOptions(selected, anyLabel) {
    return '<option value="">' + escapeHtml(anyLabel || 'Все документы') + '</option>' +
      state.docs.map(function (d) {
        return '<option value="' + escapeHtml(d) + '"' +
          (d === selected ? ' selected' : '') + '>' + escapeHtml(d) + '</option>';
      }).join('');
  }

  //: Ссылка на читательскую страницу с одним замечанием. Номер для читателя
  //: берётся из манифеста книги, а не из файлового ключа страницы.
  async function readerLink(docId, pageKey, remarkId) {
    var label = await pageLabel(docId, pageKey);
    return preview.remarkUrl(docId, label, remarkId);
  }

  function qs(params) {
    var pairs = [];
    Object.keys(params).forEach(function (k) {
      var v = params[k];
      if (v === undefined || v === null || v === '') return;
      pairs.push(encodeURIComponent(k) + '=' + encodeURIComponent(v));
    });
    return pairs.length ? '?' + pairs.join('&') : '';
  }

  //: Подпись «откуда взялась категория» — одна на все экраны.
  var SOURCE_LABELS = {
    'default': 'категорию никто не назначал',
    'tags-backfill': 'категория угадана по тегам, требует проверки',
    'agent': 'категорию предложил агент, ждёт приёмки',
    'human': 'категорию выбрал человек'
  };

  function fillCategorySelect(select, selected) {
    var order = (cats && cats.PRECEDENCE ? cats.PRECEDENCE.slice() : []).concat(['other']);
    select.innerHTML = order.map(function (slug) {
      var title = (cats && cats.TITLES && cats.TITLES[slug]) || slug;
      return '<option value="' + escapeHtml(slug) + '">' + escapeHtml(title) + '</option>';
    }).join('');
    select.value = selected || 'other';
  }

  // --- роли ---------------------------------------------------------------
  //
  // Единственная ось разграничения. `viewer` — приглашён, права ещё не
  // выданы: ему видны только профиль и предупреждение. `editor` ведёт весь
  // разбор, включая пул опроса. `admin` — плюс приглашения, участники,
  // перепубликация, логи и сводка ответов опроса.

  function isEditor() {
    return !!state.user && (state.user.role === 'editor' || state.user.role === 'admin');
  }

  function isAdmin() {
    return !!state.user && state.user.role === 'admin';
  }

  function requireAdmin() {
    if (isAdmin()) return true;
    setStatus('Это может только администратор.', true);
    return false;
  }

  // --- маршрутизация ------------------------------------------------------

  async function route() {
    var ref = parseHash();
    setStatus('');
    if (!isEditor()) {
      // Читателю показывать нечего: все данные разбора — редакторские.
      showView(null);
      return;
    }
    W.navRoute = ref.view === 'ann' || ref.view === 'section' ? 'sections' : ref.view;
    try {
      if (ref.view === 'ann') {
        showView('app-main');
        await W.loadCard(ref);
      } else if (ref.view === 'queue') {
        showView('view-queue');
        await W.loadQueue();
      } else if (ref.view === 'page') {
        showView('view-page');
        await W.loadPage(ref.docId, ref.pageKey);
      } else if (ref.view === 'section') {
        showView('view-section');
        state.section = ref;
        await W.loadSection(ref);
      } else if (ref.view === 'remarks') {
        showView('view-remarks');
        await W.loadSearch();
      } else if (ref.view === 'history') {
        showView('view-history');
        await W.loadHistory(true);
      } else if (ref.view === 'survey') {
        showView('view-survey');
        await W.loadSurvey();
      } else if (ref.view === 'admin') {
        if (!requireAdmin()) { window.location.hash = '#/'; return; }
        showView('view-admin');
        await W.loadAdmin();
      } else {
        showView('view-sections');
        await W.loadSections();
      }
    } catch (e) { /* сообщение уже показано */ }
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

    var editor = isEditor();
    el('app-nav').hidden = !editor;
    el('app-viewer-notice').hidden = editor;
    el('app-nav-admin').hidden = !isAdmin();
    if (!editor) { showView(null); return; }

    await docList();
    await route();
  }

  // --- сборка -------------------------------------------------------------

  W.el = el;
  W.setStatus = setStatus;
  W.escapeHtml = escapeHtml;
  W.formatDay = formatDay;
  W.apiGet = apiGet;
  W.apiMutate = apiMutate;
  W.showView = showView;
  W.parseHash = parseHash;
  W.pageLabel = pageLabel;
  W.showLogin = showLogin;
  W.renderProfile = renderProfile;
  W.fitFrame = fitFrame;
  W.fitPreview = fitPreview;
  W.renderPreview = renderPreview;
  W.renderBreadcrumbs = renderBreadcrumbs;
  W.catTitle = catTitle;
  W.fillCategorySelect = fillCategorySelect;
  W.SOURCE_LABELS = SOURCE_LABELS;
  W.catDot = catDot;
  W.loadDocs = loadDocs;
  W.docList = docList;
  W.docOptions = docOptions;
  W.readerLink = readerLink;
  W.qs = qs;
  W.isEditor = isEditor;
  W.isAdmin = isAdmin;
  W.requireAdmin = requireAdmin;
  W.route = route;
  W.auth = auth;
  W.cats = cats;
  W.markers = markers;
  W.preview = preview;

  //: Точка входа зовётся из index.html последней — когда все файлы экранов
  //: успели зарегистрировать себя в W.
  W.start = function () {
    W.wire.forEach(function (fn) { fn(); });
    window.addEventListener('resize', fitPreview);
    window.addEventListener('hashchange', function () { route().catch(function () {}); });
    start();
  };

  //: Каждый файл экранов кладёт сюда свою привязку обработчиков — порядок
  //: подключения тогда перестаёт быть тонкостью, о которой надо помнить.
  W.wire = [];
})();
