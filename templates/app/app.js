/*
 * ВНИМАНИЕ (переименование сущности, 2026-08-29). Этот файл — клиент API, и он
 * ещё говорит прежними именами: путь `/api/annotations`, поля `annId`/`annType`
 * со значениями `main`/`comment`. Так и задумано: статика выкладывается раньше
 * API, а на новые имена клиенты редактора переводятся отдельной выкладкой —
 * уже после того, как API начнёт их понимать (он принимает и отдаёт оба
 * набора). Читательская часть переведена сразу, см. page-view.js.
 */
(function () {
  'use strict';

  /**
   * Карточка замечания: история правок, форма и живой просмотр рядом.
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
    section: null,    // текущий параграф, чтобы перечитывать его по фильтрам
    queue: { items: [], index: 0, skipped: {} },
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

  //: Все экраны приложения. Показываем ровно один — так не приходится
  //: помнить, что спрятать при каждом переходе.
  var VIEWS = ['view-sections', 'view-section', 'view-queue', 'app-main'];

  function showView(id) {
    VIEWS.forEach(function (name) { el(name).hidden = name !== id; });
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
        annId: decodeURIComponent(ann[3])
      };
    }
    if (/^#\/queue\b/.test(hash)) return { view: 'queue' };
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

  function fillCategories(selected) {
    fillCategorySelect(el('f-category'), selected);
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
    hint.textContent = SOURCE_LABELS[ann.categorySource] || '';
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

  function fitFrame(fitId, frameId) {
    var fit = el(fitId);
    var frame = el(frameId);
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

  function fitPreview() {
    fitFrame('app-preview-fit', 'app-preview');
    fitFrame('q-preview-fit', 'q-preview');
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
    var sectionLink = section
      ? '<a href="#/section/' + encodeURIComponent(ref.docId) + '/' +
        encodeURIComponent(section.sectionId) + '">' + escapeHtml(section.title) + '</a>'
      : 'вне параграфа';
    el('app-breadcrumbs').innerHTML =
      '<span><a href="#/">параграфы</a></span>' +
      '<span>' + escapeHtml(ref.docId) + '</span>' +
      '<span>' + sectionLink + '</span>' +
      '<span>стр. ' + escapeHtml(label) + '</span>' +
      '<code>' + escapeHtml(ref.annId) + '</code>';
  }

  // --- список параграфов --------------------------------------------------

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
    select.addEventListener('change', function () { loadSections().catch(function () {}); });
    return select.value;
  }

  async function loadSections() {
    var docId = await loadDocs();
    var data = await apiGet('/api/sections?docId=' + encodeURIComponent(docId));
    var rows = (data.sections || []).map(function (s) {
      var c = s.counts || {};
      // «Не разобрано» — главный столбец этого экрана: он показывает, где
      // работа, а не сколько её сделано.
      var todo = c.unclassified || 0;
      return '<tr>' +
        '<td><a href="#/section/' + encodeURIComponent(docId) + '/' +
          encodeURIComponent(s.sectionId) + '">§' + escapeHtml(s.sectionId) + '</a></td>' +
        '<td>' + escapeHtml(s.title) + '</td>' +
        '<td class="app-nowrap">' + escapeHtml(s.pageStart + '–' + s.pageEnd) + '</td>' +
        '<td class="num">' + (c.total || 0) + '</td>' +
        '<td class="num">' + (c.published || 0) + '</td>' +
        '<td class="num">' + (c.draft || 0) + '</td>' +
        '<td class="num' + (todo ? ' is-todo' : '') + '">' + todo + '</td>' +
      '</tr>';
    }).join('');
    el('s-rows').innerHTML = rows || '<tr><td colspan="7">Параграфов нет. ' +
      'Залейте их из манифеста: scripts/api/import_sections.py</td></tr>';
  }

  // --- один параграф ------------------------------------------------------

  function fillCategoryFilter() {
    var select = el('sec-category');
    if (select.options.length > 1) return;
    var order = (cats && cats.PRECEDENCE ? cats.PRECEDENCE.slice() : []).concat(['other']);
    select.innerHTML = '<option value="">любая</option>' + order.map(function (slug) {
      return '<option value="' + escapeHtml(slug) + '">' + escapeHtml(catTitle(slug)) + '</option>';
    }).join('');
  }

  async function loadSection(ref) {
    fillCategoryFilter();
    var params = ['docId=' + encodeURIComponent(ref.docId),
                  'section=' + encodeURIComponent(ref.sectionId),
                  'limit=200'];
    var status = el('sec-status').value;
    var category = el('sec-category').value;
    var source = el('sec-source').value;
    if (status) params.push('status=' + encodeURIComponent(status));
    if (category) params.push('category=' + encodeURIComponent(category));
    if (source) params.push('categorySource=' + encodeURIComponent(source));

    var data = await apiGet('/api/annotations?' + params.join('&'));
    var items = data.items || [];
    el('sec-count').textContent = 'показано ' + items.length + ' из ' + (data.total || 0);

    // Сортируем по странице: параграф читают подряд, а не по времени правки.
    items.sort(function (a, b) {
      if (a.pageNum === b.pageNum) return String(a.annId).localeCompare(String(b.annId));
      return String(a.pageNum).localeCompare(String(b.pageNum));
    });

    el('sec-rows').innerHTML = items.map(function (a) {
      var href = '#/ann/' + encodeURIComponent(a.docId) + '/' +
                 encodeURIComponent(a.pageNum) + '/' + encodeURIComponent(a.annId);
      var todo = a.categorySource === 'default' || a.categorySource === 'tags-backfill';
      return '<tr>' +
        '<td class="app-nowrap"><a href="' + href + '">' + escapeHtml(a.pageNum) + '</a></td>' +
        '<td class="app-nowrap' + (todo ? ' is-todo' : '') + '">' +
          catDot(a.category) + escapeHtml(catTitle(a.category)) + '</td>' +
        '<td>' + (a.status === 'draft' ? 'черновик' : 'опубликован') + '</td>' +
        '<td><a href="' + href + '">' + escapeHtml((a.text || '').slice(0, 120)) + '</a></td>' +
        '<td class="app-nowrap">' + escapeHtml(formatDay(a.updatedAt)) + '</td>' +
      '</tr>';
    }).join('') || '<tr><td colspan="5">В этом параграфе замечаний нет.</td></tr>';

    var section = (await apiGet('/api/sections?docId=' + encodeURIComponent(ref.docId))).sections
      .filter(function (s) { return s.sectionId === ref.sectionId; })[0];
    el('sec-title').textContent = section ? section.title : ('§' + ref.sectionId);
  }

  // --- очередь приёмки ----------------------------------------------------
  //
  // Разбирать приходится две разные вещи, и смешивать их нельзя:
  //   черновики  — решение «годится ли это читателю» (принять / отклонить);
  //   категории  — решение «каким приёмом это сделано» (подтвердить / сменить).
  // Поэтому режим выбирается явно, а не угадывается.

  function queueParams(mode, sectionId) {
    var params = ['docId=' + encodeURIComponent(el('s-doc').value || 'medinsky11klass'),
                  'limit=100'];
    if (sectionId) params.push('section=' + encodeURIComponent(sectionId));
    if (mode === 'drafts') {
      params.push('status=draft');
    } else {
      // Всё, чего человек ещё не подтверждал: дефолт, догадка по тегам и
      // предложение агента. Три запроса вместо одного — фильтр по источнику
      // принимает ровно одно значение, и усложнять его ради этого экрана
      // не стоит.
      params.push('status=published');
    }
    return params;
  }

  async function fetchQueueItems(mode, sectionId) {
    if (mode === 'drafts') {
      var data = await apiGet('/api/annotations?' + queueParams(mode, sectionId).join('&'));
      return data.items || [];
    }
    var sources = ['default', 'tags-backfill', 'agent'];
    var batches = await Promise.all(sources.map(function (source) {
      var params = queueParams(mode, sectionId).concat(['categorySource=' + source]);
      return apiGet('/api/annotations?' + params.join('&'));
    }));
    var seen = {};
    var items = [];
    batches.forEach(function (batch) {
      (batch.items || []).forEach(function (item) {
        var key = item.docId + '/' + item.pageNum + '/' + item.annId;
        if (seen[key]) return;
        seen[key] = true;
        items.push(item);
      });
    });
    return items;
  }

  async function fillSectionFilter(docId) {
    var select = el('q-section');
    if (select.options.length > 1) return;
    var data = await apiGet('/api/sections?docId=' + encodeURIComponent(docId));
    select.innerHTML = '<option value="">все</option>' + (data.sections || []).map(function (s) {
      return '<option value="' + escapeHtml(s.sectionId) + '">§' + escapeHtml(s.sectionId) +
             ' — ' + escapeHtml(s.title.slice(0, 40)) + '</option>';
    }).join('');
  }

  async function loadQueue() {
    var docId = await loadDocs();
    await fillSectionFilter(docId);
    var mode = el('q-mode').value;
    var sectionId = el('q-section').value;
    var items = await fetchQueueItems(mode, sectionId);
    // Отложенные уходят в конец, а не исчезают: «отложить» — это «не сейчас».
    var skipped = state.queue.skipped;
    items.sort(function (a, b) {
      var sa = skipped[a.annId] ? 1 : 0, sb = skipped[b.annId] ? 1 : 0;
      if (sa !== sb) return sa - sb;
      return String(a.pageNum).localeCompare(String(b.pageNum));
    });
    state.queue.items = items;
    state.queue.index = 0;
    renderQueueItem();
  }

  function renderQueueItem() {
    var queue = state.queue;
    var item = queue.items[queue.index];
    el('q-empty').hidden = !!item;
    el('q-body').hidden = !item;
    el('q-progress').textContent = queue.items.length
      ? (queue.index + 1) + ' из ' + queue.items.length
      : '';
    if (!item) return;

    el('q-text').textContent = item.text || '';
    fillCategorySelect(el('q-category'), item.category);
    el('q-status').value = item.status === 'published' ? 'published' : 'draft';
    el('q-source').textContent = SOURCE_LABELS[item.categorySource] || '';
    el('q-source').className = 'app-hint ' +
      (item.categorySource === 'human' ? 'is-ok' : 'is-todo');

    pageLabel(item.docId, item.pageNum).then(function (label) {
      var url = '../' + encodeURIComponent(item.docId) + '/pages/' +
                encodeURIComponent(label) + '/?only=' + encodeURIComponent(item.annId);
      el('q-preview').src = url;
      el('q-preview-link').href = url;
      fitFrame('q-preview-fit', 'q-preview');
      el('q-where').innerHTML = '<span>' + escapeHtml(item.docId) + '</span>' +
        '<span>стр. ' + escapeHtml(label) + '</span>' +
        '<code>' + escapeHtml(item.annId) + '</code>';
    });
  }

  function advance() {
    state.queue.index += 1;
    if (state.queue.index >= state.queue.items.length) {
      // Дошли до конца — перечитываем: за время разбора часть могла уйти.
      loadQueue().catch(function () {});
      return;
    }
    renderQueueItem();
  }

  async function queueAccept() {
    var item = state.queue.items[state.queue.index];
    if (!item) return;
    var body = {
      annType: item.annType,
      text: item.text,
      status: el('q-status').value,
      category: el('q-category').value
    };
    if (item.coordX != null && item.coordY != null) body.coords = [item.coordX, item.coordY];
    // Резюме проставляется само: приёмка — действие однотипное, и заставлять
    // писать его руками на каждое замечание значит не разобрать очередь.
    body.summary = el('q-mode').value === 'drafts' ? 'приёмка черновика' : 'категория подтверждена';
    await apiMutate('PUT', '/api/editor/' + encodeURIComponent(item.docId) + '/' +
                    encodeURIComponent(item.pageNum) + '/' + encodeURIComponent(item.annId), body);
    setStatus('Принято: ' + item.annId, false);
    advance();
  }

  async function queueReject() {
    var item = state.queue.items[state.queue.index];
    if (!item) return;
    if (!window.confirm('Отклонить замечание ' + item.annId + '? Оно будет удалено (мягко).')) return;
    await apiMutate('DELETE', '/api/editor/' + encodeURIComponent(item.docId) + '/' +
                    encodeURIComponent(item.pageNum) + '/' + encodeURIComponent(item.annId));
    setStatus('Отклонён: ' + item.annId, false);
    advance();
  }

  function wireQueue() {
    // Масштаб считается по размеру панели, а она к моменту первой отрисовки
    // может быть ещё не разложена. Пересчитываем, когда кадр загрузился.
    el('q-preview').addEventListener('load', function () {
      fitFrame('q-preview-fit', 'q-preview');
    });
    el('q-mode').addEventListener('change', function () { loadQueue().catch(function () {}); });
    el('q-section').addEventListener('change', function () { loadQueue().catch(function () {}); });
    el('q-accept').addEventListener('click', function () { queueAccept().catch(function () {}); });
    el('q-reject').addEventListener('click', function () { queueReject().catch(function () {}); });
    el('q-skip').addEventListener('click', function () {
      var item = state.queue.items[state.queue.index];
      if (item) state.queue.skipped[item.annId] = true;
      advance();
    });
    el('q-edit').addEventListener('click', function () {
      var item = state.queue.items[state.queue.index];
      if (!item) return;
      window.location.hash = '#/ann/' + encodeURIComponent(item.docId) + '/' +
        encodeURIComponent(item.pageNum) + '/' + encodeURIComponent(item.annId);
    });
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
    if (!body.text.trim()) { setStatus('Текст замечания пуст.', true); return; }
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

  function wireFilters() {
    ['sec-status', 'sec-category', 'sec-source'].forEach(function (id) {
      el(id).addEventListener('change', function () {
        if (state.section) loadSection(state.section).catch(function () {});
      });
    });
  }

  function wireForm() {
    el('app-preview').addEventListener('load', function () {
      fitFrame('app-preview-fit', 'app-preview');
    });
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
    setStatus('');
    try {
      if (ref.view === 'ann') {
        showView('app-main');
        await loadCard(ref);
      } else if (ref.view === 'queue') {
        showView('view-queue');
        await loadQueue();
      } else if (ref.view === 'section') {
        showView('view-section');
        state.section = ref;
        await loadSection(ref);
      } else {
        showView('view-sections');
        await loadSections();
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
    if (state.user.role === 'viewer') {
      setStatus('У вас роль читателя — править нельзя.', true);
    }
    await route();
  }

  wireFilters();
  wireQueue();
  window.addEventListener('resize', fitPreview);
  window.addEventListener('hashchange', function () { route().catch(function () {}); });
  wireForm();
  start();
})();
