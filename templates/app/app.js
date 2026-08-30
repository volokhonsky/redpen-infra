(function () {
  'use strict';

  /**
   * Карточка замечания: история правок, форма и живой просмотр рядом.
   *
   * Адрес: /app/#/ann/<docId>/<pageKey>/<remarkId>
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
    ref: null,        // {docId, pageKey, remarkId}
    remark: null,
    manifest: {},     // docId -> {page_006: "6"}
    section: null,    // текущий параграф, чтобы перечитывать его по фильтрам
    queue: { items: [], index: 0, skipped: {} },
    dirty: false,
    scales: [],       // описание шкал оценки, приходит с сервера
    scaleTitles: {},  // name -> заголовок, для подписей в ленте
    timeline: [],     // ревизии, оценки и комментарии одним списком
    textOnly: false,  // фильтр ленты «только правки текста»
    cardSha: null,    // serverPageSha страницы открытой карточки
    notes: [],
    replyTo: null,    // id корневого комментария, если пишем ответ
    // Экран страницы: скан с маркерами. sha — вход оптимистической блокировки,
    // приходит из GET /api/editor/... и уезжает обратно в каждой мутации.
    page: { docId: null, pageKey: null, remarks: [], sha: null,
            placing: false, pendingCoords: null, selectedId: null }
  };

  var markers = window.RedPenMarkers;

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
  var VIEWS = ['view-sections', 'view-section', 'view-queue', 'view-page', 'app-main'];

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
        remarkId: decodeURIComponent(ann[3])
      };
    }
    if (/^#\/queue\b/.test(hash)) return { view: 'queue' };
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

  //: Теги, которые человеку показывают и разрешают править. Зеркальный тег
  //: категории и `draft` производные — если дать их в поле, их попробуют
  //: поправить и получат 400.
  function visibleTags(ann) {
    return (ann.tags || []).filter(function (t) {
      return t.indexOf('cat:') !== 0 && t !== 'draft';
    });
  }

  function fillForm(ann) {
    el('f-text').value = ann.text || '';
    fillCategories(ann.category);
    el('f-kind').value = ann.kind || 'minor';
    el('f-status').value = ann.status === 'published' ? 'published' : 'draft';
    el('f-tags').value = visibleTags(ann).join(', ');
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
      kind: el('f-kind').value,
      text: el('f-text').value,
      status: el('f-status').value,
      category: el('f-category').value,
      tags: tags
    };
    var summary = el('f-summary').value.trim();
    if (summary) body.summary = summary;
    var ann = state.remark;
    if (ann && ann.coordX != null && ann.coordY != null) body.coords = [ann.coordX, ann.coordY];
    // Оптимистическая блокировка: без неё сервер принимает правку молча, и две
    // одновременные сессии затирают друг друга (сервер это логирует как
    // «clientPageSha missing»). Хеш читается вместе с карточкой.
    if (state.cardSha) body.clientPageSha = state.cardSha;
    return body;
  }

  // --- лента событий ------------------------------------------------------
  //
  // Ярлык действия приходит с сервера (см. scripts/api/remark_actions.py):
  // держать здесь второй словарь значило бы синхронизировать его руками, как
  // это уже приходится делать с категориями.

  //: Действия, означающие правку содержания. Совпадает с
  //: remark_actions.CONTENT_ACTIONS — единственное, что здесь продублировано,
  //: и только ради фильтра на клиенте.
  var CONTENT_ACTIONS = ['text', 'coords', 'kind'];

  function isContentEdit(actions) {
    if (!actions || !actions.length) return false;
    for (var i = 0; i < actions.length; i++) {
      if (CONTENT_ACTIONS.indexOf(actions[i]) !== -1) return true;
    }
    return false;
  }

  function actorName(item) {
    return item.actorName || (item.actorId ? 'Участник №' + item.actorId : 'без автора');
  }

  function timelineHead(item) {
    var agent = item.agentRunId
      ? ' <span class="app-agent">прогон #' + item.agentRunId + '</span>' : '';
    return '<div class="app-rev-head">' +
      (item.revNo ? '<span class="app-rev-no">№' + item.revNo + '</span>' : '') +
      '<span class="app-rev-action">' + escapeHtml(item.actionLabel || '') + '</span>' +
      '<span class="app-rev-who">' + escapeHtml(actorName(item)) + agent + '</span>' +
      '<span class="app-rev-day">' + escapeHtml(formatDay(item.createdAt)) + '</span>' +
    '</div>';
  }

  function renderTimelineItem(item) {
    if (item.kind === 'rating') {
      var scale = state.scaleTitles[item.scale] || item.scale;
      return '<li class="app-rev app-rev--rating">' + timelineHead(item) +
        '<div class="app-rev-text">' + escapeHtml(scale) + ': ' + item.value + ' из 5' +
        (item.note ? ' — ' + escapeHtml(item.note) : '') + '</div>' +
      '</li>';
    }
    if (item.kind === 'note') {
      return '<li class="app-rev app-rev--note">' + timelineHead(item) +
        '<div class="app-rev-text">' + escapeHtml((item.body || '').slice(0, 200)) + '</div>' +
      '</li>';
    }
    return '<li class="app-rev">' + timelineHead(item) +
      (item.summary ? '<div class="app-rev-summary">' + escapeHtml(item.summary) + '</div>' : '') +
      '<div class="app-rev-text">' + escapeHtml((item.text || '').slice(0, 200)) + '</div>' +
    '</li>';
  }

  function renderTimeline() {
    var host = el('app-revisions');
    var items = state.timeline || [];
    if (state.textOnly) {
      items = items.filter(function (item) {
        return item.kind === 'revision' && isContentEdit(item.actions);
      });
    }
    if (!items.length) {
      host.innerHTML = '<li class="app-empty">' +
        (state.textOnly ? 'Правок текста пока не было.' : 'Событий пока нет.') + '</li>';
      return;
    }
    // Старые сверху: историю читают сверху вниз, как она происходила.
    host.innerHTML = items.slice().reverse().map(renderTimelineItem).join('');
  }

  // --- оценки -------------------------------------------------------------

  function renderRatings(summary) {
    var host = el('app-rating-scales');
    if (!state.scales.length) { host.innerHTML = ''; return; }
    host.innerHTML = state.scales.map(function (scale) {
      var row = (summary && summary[scale.name]) || {};
      var buttons = '';
      for (var v = scale.min; v <= scale.max; v++) {
        buttons += '<button type="button" class="app-rating-btn' +
          (row.mine === v ? ' is-mine' : '') + '" data-scale="' +
          escapeHtml(scale.name) + '" data-value="' + v + '">' + v + '</button>';
      }
      var others = row.count
        ? 'среднее ' + row.average + ' по ' + row.count
        : 'ещё никто не оценил';
      return '<div class="app-rating">' +
        '<div class="app-rating-title" title="' + escapeHtml(scale.hint) + '">' +
          escapeHtml(scale.title) + '</div>' +
        '<div class="app-rating-scale">' + buttons +
          (row.mine != null
            ? '<button type="button" class="app-rating-clear" data-scale="' +
              escapeHtml(scale.name) + '">снять</button>'
            : '') +
        '</div>' +
        '<div class="app-rating-others">' + escapeHtml(others) + '</div>' +
      '</div>';
    }).join('');
  }

  function ratingsUrl(ref, scale) {
    return '/api/remarks/' + encodeURIComponent(ref.docId) + '/' +
      encodeURIComponent(ref.pageKey) + '/' + encodeURIComponent(ref.remarkId) +
      '/ratings' + (scale ? '/' + encodeURIComponent(scale) : '');
  }

  async function rate(scale, value) {
    var ref = state.ref;
    if (!ref) return;
    var data = await apiMutate('PUT', ratingsUrl(ref, scale), { value: value });
    renderRatings(data.summary);
    await loadTimeline(ref);
  }

  async function unrate(scale) {
    var ref = state.ref;
    if (!ref) return;
    var data = await apiMutate('DELETE', ratingsUrl(ref, scale));
    renderRatings(data.summary);
    await loadTimeline(ref);
  }

  // --- комментарии --------------------------------------------------------

  function notesUrl(ref) {
    return '/api/remarks/' + encodeURIComponent(ref.docId) + '/' +
      encodeURIComponent(ref.pageKey) + '/' + encodeURIComponent(ref.remarkId) + '/notes';
  }

  function renderNoteItem(note, replies) {
    var head = '<div class="app-note-head">' +
      '<span class="app-note-who">' +
        escapeHtml(note.authorName || ('Участник №' + note.authorId)) + '</span>' +
      '<span class="app-note-day">' + escapeHtml(formatDay(note.createdAt)) + '</span>' +
      (note.resolved ? '<span class="app-note-done">решено</span>' : '') +
    '</div>';
    var body = '<div class="app-note-body">' + escapeHtml(note.body) + '</div>';
    var actions = '<div class="app-note-actions">' +
      '<button type="button" class="app-note-reply" data-id="' + note.id + '">ответить</button>' +
      '<button type="button" class="app-note-resolve" data-id="' + note.id +
        '" data-resolved="' + (note.resolved ? '1' : '0') + '">' +
        (note.resolved ? 'открыть снова' : 'решено') + '</button>' +
      '<button type="button" class="app-note-delete" data-id="' + note.id + '">удалить</button>' +
    '</div>';
    var children = replies.map(function (reply) {
      return '<li class="app-note app-note--reply">' +
        '<div class="app-note-head">' +
          '<span class="app-note-who">' +
            escapeHtml(reply.authorName || ('Участник №' + reply.authorId)) + '</span>' +
          '<span class="app-note-day">' + escapeHtml(formatDay(reply.createdAt)) + '</span>' +
        '</div>' +
        '<div class="app-note-body">' + escapeHtml(reply.body) + '</div>' +
        '<div class="app-note-actions">' +
          '<button type="button" class="app-note-delete" data-id="' + reply.id + '">удалить</button>' +
        '</div>' +
      '</li>';
    }).join('');
    return '<li class="app-note' + (note.resolved ? ' is-resolved' : '') + '">' +
      head + body + actions +
      (children ? '<ol class="app-note-replies">' + children + '</ol>' : '') +
    '</li>';
  }

  function renderNotes(items) {
    var host = el('app-note-list');
    var roots = items.filter(function (n) { return n.parentId == null; });
    if (!roots.length) {
      host.innerHTML = '<li class="app-empty">Обсуждения пока нет.</li>';
      return;
    }
    host.innerHTML = roots.map(function (root) {
      var replies = items.filter(function (n) { return n.parentId === root.id; });
      return renderNoteItem(root, replies);
    }).join('');
  }

  async function loadNotes(ref) {
    var data = await apiGet(notesUrl(ref));
    state.notes = data.items || [];
    renderNotes(state.notes);
  }

  async function sendNote() {
    var ref = state.ref;
    if (!ref) return;
    var body = el('n-body').value.trim();
    if (!body) return;
    var payload = { body: body };
    if (state.replyTo) payload.parentId = state.replyTo;
    await apiMutate('POST', notesUrl(ref), payload);
    el('n-body').value = '';
    setReplyTo(null);
    await loadNotes(ref);
    await loadTimeline(ref);
  }

  function setReplyTo(id) {
    state.replyTo = id;
    var field = el('n-body');
    field.placeholder = id
      ? 'Ответ в треде — Esc, чтобы отменить'
      : 'Что смущает в этом замечании?';
    if (id) field.focus();
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
              encodeURIComponent(label) + '/?only=' + encodeURIComponent(ref.remarkId);
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

    var data = await apiGet('/api/remarks?' + params.join('&'));
    var items = data.items || [];
    el('sec-count').textContent = 'показано ' + items.length + ' из ' + (data.total || 0);

    // Сортируем по странице: параграф читают подряд, а не по времени правки.
    items.sort(function (a, b) {
      if (a.pageNum === b.pageNum) return String(a.remarkId).localeCompare(String(b.remarkId));
      return String(a.pageNum).localeCompare(String(b.pageNum));
    });

    el('sec-rows').innerHTML = items.map(function (a) {
      var href = '#/ann/' + encodeURIComponent(a.docId) + '/' +
                 encodeURIComponent(a.pageNum) + '/' + encodeURIComponent(a.remarkId);
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
      var data = await apiGet('/api/remarks?' + queueParams(mode, sectionId).join('&'));
      return data.items || [];
    }
    var sources = ['default', 'tags-backfill', 'agent'];
    var batches = await Promise.all(sources.map(function (source) {
      var params = queueParams(mode, sectionId).concat(['categorySource=' + source]);
      return apiGet('/api/remarks?' + params.join('&'));
    }));
    var seen = {};
    var items = [];
    batches.forEach(function (batch) {
      (batch.items || []).forEach(function (item) {
        var key = item.docId + '/' + item.pageNum + '/' + item.remarkId;
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
      var sa = skipped[a.remarkId] ? 1 : 0, sb = skipped[b.remarkId] ? 1 : 0;
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
                encodeURIComponent(label) + '/?only=' + encodeURIComponent(item.remarkId);
      el('q-preview').src = url;
      el('q-preview-link').href = url;
      fitFrame('q-preview-fit', 'q-preview');
      el('q-where').innerHTML = '<span>' + escapeHtml(item.docId) + '</span>' +
        '<span>стр. ' + escapeHtml(label) + '</span>' +
        '<code>' + escapeHtml(item.remarkId) + '</code>';
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
    var path = '/api/editor/' + encodeURIComponent(item.docId) + '/' +
               encodeURIComponent(item.pageNum) + '/' + encodeURIComponent(item.remarkId);
    // Резюме проставляется само: приёмка — действие однотипное, и заставлять
    // писать его руками на каждое замечание значит не разобрать очередь.
    var summary = el('q-mode').value === 'drafts' ? 'приёмка черновика' : 'категория подтверждена';
    var status = el('q-status').value;
    var category = el('q-category').value;

    // Узкие операции вместо полного PUT: очередь не правит текст, и слать его
    // целиком означало бы записывать в журнал правку, которой не было.
    //
    // Категория пишется и тогда, когда значение не изменилось: подтверждение
    // догадки — это и есть работа очереди, оно переводит category_source в
    // 'human'. Без этого подтверждённое замечание всплывало бы в очереди снова.
    if (category !== item.category || item.categorySource !== 'human') {
      await apiMutate('PATCH', path + '/category', { category: category, summary: summary });
    }
    if (status !== item.status) {
      await apiMutate('PATCH', path + '/status', { status: status, summary: summary });
    }
    setStatus('Принято: ' + item.remarkId, false);
    advance();
  }

  async function queueReject() {
    var item = state.queue.items[state.queue.index];
    if (!item) return;
    if (!window.confirm('Отклонить замечание ' + item.remarkId + '? Оно будет удалено (мягко).')) return;
    await apiMutate('DELETE', '/api/editor/' + encodeURIComponent(item.docId) + '/' +
                    encodeURIComponent(item.pageNum) + '/' + encodeURIComponent(item.remarkId));
    setStatus('Отклонён: ' + item.remarkId, false);
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
      if (item) state.queue.skipped[item.remarkId] = true;
      advance();
    });
    el('q-edit').addEventListener('click', function () {
      var item = state.queue.items[state.queue.index];
      if (!item) return;
      window.location.hash = '#/ann/' + encodeURIComponent(item.docId) + '/' +
        encodeURIComponent(item.pageNum) + '/' + encodeURIComponent(item.remarkId);
    });
  }

  // --- загрузка карточки --------------------------------------------------

  async function loadScales() {
    if (state.scales.length) return;
    var data = await apiGet('/api/rating-scales');
    state.scales = data.scales || [];
    state.scaleTitles = {};
    state.scales.forEach(function (scale) { state.scaleTitles[scale.name] = scale.title; });
  }

  async function loadTimeline(ref) {
    var data = await apiGet('/api/remarks/' + encodeURIComponent(ref.docId) + '/' +
                            encodeURIComponent(ref.pageKey) + '/' +
                            encodeURIComponent(ref.remarkId) + '/timeline?limit=100');
    state.timeline = data.items || [];
    renderTimeline();
  }

  async function loadCard(ref) {
    state.ref = ref;
    setReplyTo(null);
    state.cardSha = null;
    var path = '/api/remarks/' + encodeURIComponent(ref.docId) + '/' +
               encodeURIComponent(ref.pageKey) + '/' + encodeURIComponent(ref.remarkId);
    var data = await apiGet(path);
    state.remark = data.remark;
    fillForm(data.remark);

    // Хеш страницы для оптимистической блокировки. Отдельный запрос: карточка
    // отдаёт одно замечание, а блокировка — про страницу целиком.
    try {
      var page = await apiGet('/api/editor/' + encodeURIComponent(ref.docId) + '/' +
                              encodeURIComponent(ref.pageKey));
      state.cardSha = page.serverPageSha || null;
    } catch (e) {
      state.cardSha = null;
    }

    var label = await renderPreview(ref);
    await renderBreadcrumbs(ref, data.section, label);

    await loadScales();
    var ratings = await apiGet(ratingsUrl(ref));
    renderRatings(ratings.summary);
    await loadNotes(ref);
    await loadTimeline(ref);
  }

  //: Что из формы едет узкой операцией, а что — общим PUT. Текст и координаты
  //: правит только PUT: у них оптимистическая блокировка по sha страницы.
  //: Остальное — отдельные действия, и в журнале они видны по отдельности.
  async function saveSideChanges(ref, before, body) {
    var editorPath = '/api/editor/' + encodeURIComponent(ref.docId) + '/' +
                     encodeURIComponent(ref.pageKey) + '/' + encodeURIComponent(ref.remarkId);
    var summary = body.summary;

    if (body.status !== (before.status === 'published' ? 'published' : 'draft')) {
      await apiMutate('PATCH', editorPath + '/status',
                      { status: body.status, summary: summary });
    }
    if (body.category !== before.category) {
      await apiMutate('PATCH', editorPath + '/category',
                      { category: body.category, summary: summary });
    }
    if (!sameTags(body.tags, visibleTags(before))) {
      await apiMutate('PATCH', editorPath + '/tags',
                      { tags: body.tags, summary: summary });
    }
  }

  function sameTags(a, b) {
    if (a.length !== b.length) return false;
    var left = a.slice().sort().join('\u0000');
    var right = b.slice().sort().join('\u0000');
    return left === right;
  }

  async function save() {
    var ref = state.ref;
    if (!ref) return;
    var body = collectForm();
    if (!body.text.trim()) { setStatus('Текст замечания пуст.', true); return; }
    var before = state.remark || {};
    var textChanged = body.text !== (before.text || '') ||
                      body.kind !== (before.kind || 'minor');

    if (textChanged) {
      // Полный PUT: он несёт текст и координаты, а вместе с ними и всё
      // остальное — узкие операции после него были бы холостыми.
      await apiMutate('PUT', '/api/editor/' + encodeURIComponent(ref.docId) + '/' +
                      encodeURIComponent(ref.pageKey) + '/' + encodeURIComponent(ref.remarkId), body);
    } else {
      await saveSideChanges(ref, before, body);
    }
    setStatus('Сохранено.', false);
    await loadCard(ref);
    // Просмотр перечитываем принудительно: страница уже перерисована на сервере,
    // но у iframe тот же адрес, и сам он не обновится.
    var frame = el('app-preview');
    frame.src = frame.src;
  }

  // --- экран страницы: скан с маркерами -----------------------------------
  //
  // До 2026-08-30 создать замечание и поставить маркер можно было только в
  // старом SPA (document_index.html?editor=1) — редактор и просмотрщик там
  // склеены одним DOM. Здесь то же самое, но данные берутся из API, а рисует
  // маркеры общий с просмотрщиком redpen-markers.js, поэтому кружок в
  // редакторе и кружок у читателя — это буквально один код.

  function editorPageUrl(docId, pageKey) {
    return '/api/editor/' + encodeURIComponent(docId) + '/' + encodeURIComponent(pageKey);
  }

  //: Тело PUT для существующего замечания. Отсутствие `tags` значит «не
  //: трогать» — теги здесь не редактируются, и затирать их нельзя.
  function remarkBody(ann, extra) {
    var body = {
      kind: markers.kindOf(ann),
      text: ann.text || '',
      status: ann.draft ? 'draft' : 'published',
      category: ann.category || 'other',
      coords: ann.coords,
      clientPageSha: state.page.sha
    };
    Object.keys(extra || {}).forEach(function (k) { body[k] = extra[k]; });
    return body;
  }

  async function loadPage(docId, pageKey) {
    state.page.docId = docId;
    state.page.pageKey = pageKey;
    cancelPlacing();

    var data = await apiGet(editorPageUrl(docId, pageKey));
    state.page.remarks = data.remarks || [];
    state.page.sha = data.serverPageSha || null;

    var label = await pageLabel(docId, pageKey);
    el('pg-where').innerHTML =
      '<span><a href="#/">параграфы</a></span>' +
      '<span>' + escapeHtml(docId) + '</span>' +
      '<span>стр. ' + escapeHtml(label) + '</span>';
    var withCoords = state.page.remarks.filter(markers.hasCoords).length;
    el('pg-count').textContent = state.page.remarks.length + ' замечаний, ' +
      withCoords + ' с маркером';

    var image = el('pg-image');
    var src = '../' + encodeURIComponent(docId) + '/images/page_' +
              encodeURIComponent(pageKey) + '.png';
    if (image.getAttribute('src') !== src) {
      image.setAttribute('src', src);
    } else {
      drawPageMarkers();
    }
    renderPageList();
  }

  //: Порядок нумерации тот же, что у читателя: замечания идут как пришли, а
  //: номер получают только те, у кого есть координата.
  function numberedRemarks() {
    return state.page.remarks.filter(markers.hasCoords);
  }

  function drawPageMarkers() {
    var image = el('pg-image');
    var overlay = el('pg-overlay');
    if (!image.complete || !image.naturalWidth) return;

    var scale = markers.scaleOf(image);
    markers.fitOverlay(overlay, image);
    overlay.innerHTML = '';

    numberedRemarks().forEach(function (ann, index) {
      var circle = markers.createCircle(ann, index + 1, scale);
      if (ann.id === state.page.selectedId) circle.classList.add('is-selected');
      circle.title = (ann.text || '').slice(0, 120);
      wireMarkerDrag(circle, ann, image);
      overlay.appendChild(circle);
    });

    if (state.page.pendingCoords) {
      var ghost = markers.createCircle(
        { id: '__new__', coords: state.page.pendingCoords, kind: el('pg-kind').value,
          category: el('pg-category').value, draft: true },
        '+', scale);
      ghost.classList.add('is-selected');
      overlay.appendChild(ghost);
    }
  }

  /**
   * Перетаскивание маркера. Правка уезжает только на mouseup и только если
   * координата действительно изменилась: иначе обычный клик по маркеру писал
   * бы в журнал ревизию «перенос маркера» без переноса.
   */
  function wireMarkerDrag(circle, ann, image) {
    var dragging = false;
    var moved = false;
    var coords = null;

    function onMove(event) {
      if (!dragging) return;
      var point = markers.pointToCoords(image, event.clientX, event.clientY);
      if (!point) return;
      moved = true;
      coords = point;
      var scale = markers.scaleOf(image);
      var g = markers.geometry({ coords: coords, kind: markers.kindOf(ann) }, scale);
      circle.style.left = g.cx + 'px';
      circle.style.top = (g.cy - g.diameter / 2) + 'px';
    }

    async function onUp() {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      if (!dragging) return;
      dragging = false;
      circle.classList.remove('is-dragging');
      if (!moved || !coords) {
        // Не перенос, а клик: открываем карточку.
        openCard(ann.id);
        return;
      }
      if (coords[0] === ann.coords[0] && coords[1] === ann.coords[1]) {
        drawPageMarkers();
        return;
      }
      try {
        await saveCoords(ann, coords);
      } catch (e) {
        await loadPage(state.page.docId, state.page.pageKey);
      }
    }

    circle.addEventListener('mousedown', function (event) {
      event.preventDefault();
      event.stopPropagation();
      // В режиме выбора точки маркер не должен перехватывать клик: он лежит
      // поверх скана, и попытка поставить новое замечание рядом с существующим
      // иначе открывала бы чужую карточку вместо постановки.
      if (state.page.placing) { onScanClick(event); return; }
      dragging = true;
      moved = false;
      coords = null;
      state.page.selectedId = ann.id;
      circle.classList.add('is-dragging');
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  async function saveCoords(ann, coords) {
    var path = editorPageUrl(state.page.docId, state.page.pageKey) + '/' +
               encodeURIComponent(ann.id);
    var res = await apiMutate('PUT', path, remarkBody(ann, {
      coords: coords,
      summary: 'перенос маркера'
    }));
    state.page.sha = res.serverPageSha || state.page.sha;
    setStatus('Маркер перенесён.', false);
    await loadPage(state.page.docId, state.page.pageKey);
  }

  function openCard(remarkId) {
    window.location.hash = '#/ann/' + encodeURIComponent(state.page.docId) + '/' +
      encodeURIComponent(state.page.pageKey) + '/' + encodeURIComponent(remarkId);
  }

  function renderPageList() {
    var numbered = numberedRemarks();
    var numberById = {};
    numbered.forEach(function (ann, index) { numberById[ann.id] = index + 1; });

    var html = state.page.remarks.map(function (ann) {
      var num = numberById[ann.id];
      var head = num
        ? '<span class="app-page-num">' + num + '</span>'
        : '<span class="app-page-nocoords">без маркера</span>';
      return '<li class="app-page-item' + (ann.draft ? ' is-draft' : '') + '" data-id="' +
        escapeHtml(ann.id) + '">' +
        '<div class="app-page-item-head">' + head +
        catDot(ann.category || 'other') +
        '<span>' + escapeHtml(catTitle(ann.category || 'other')) + '</span>' +
        '<span>' + (ann.draft ? 'черновик' : 'опубликовано') + '</span>' +
        '</div>' +
        '<div>' + escapeHtml((ann.text || '').slice(0, 200)) + '</div>' +
        '</li>';
    }).join('');
    el('pg-list').innerHTML = html || '<li class="app-empty">На странице пока нет замечаний.</li>';
  }

  // --- создание замечания -------------------------------------------------

  function cancelPlacing() {
    state.page.placing = false;
    state.page.pendingCoords = null;
    el('pg-scan').classList.remove('is-placing');
    el('pg-form').hidden = true;
    el('pg-hint').textContent = '';
  }

  function startPlacing() {
    state.page.placing = true;
    state.page.pendingCoords = null;
    el('pg-scan').classList.add('is-placing');
    el('pg-hint').textContent = 'Щёлкните по скану там, где должен стоять маркер.';
  }

  function onScanClick(event) {
    if (!state.page.placing) return;
    var image = el('pg-image');
    var coords = markers.pointToCoords(image, event.clientX, event.clientY);
    if (!coords) return;
    state.page.pendingCoords = coords;
    el('pg-coords').textContent = 'Координаты: ' + coords[0] + ', ' + coords[1] +
      ' (щёлкните ещё раз, чтобы передвинуть)';
    el('pg-form').hidden = false;
    el('pg-hint').textContent = '';
    fillCategorySelect(el('pg-category'), 'other');
    drawPageMarkers();
    el('pg-text').focus();
  }

  async function createRemark() {
    var coords = state.page.pendingCoords;
    if (!coords) { setStatus('Сначала выберите точку на скане.', true); return; }
    var text = el('pg-text').value.trim();
    if (!text) { setStatus('Текст замечания пуст.', true); return; }

    var res = await apiMutate('POST', editorPageUrl(state.page.docId, state.page.pageKey), {
      kind: el('pg-kind').value,
      text: text,
      // Новое замечание всегда черновик: публикация — отдельное решение, и
      // делается оно в карточке или в очереди приёмки.
      status: 'draft',
      category: el('pg-category').value,
      coords: coords,
      clientPageSha: state.page.sha,
      summary: 'создано в редакторе'
    });
    state.page.sha = res.serverPageSha || state.page.sha;
    el('pg-text').value = '';
    cancelPlacing();
    setStatus('Черновик создан.', false);
    await loadPage(state.page.docId, state.page.pageKey);
    if (res.id) openCard(res.id);
  }

  function wirePage() {
    el('pg-image').addEventListener('load', drawPageMarkers);
    el('pg-image').addEventListener('click', onScanClick);
    el('pg-new').addEventListener('click', function () {
      if (state.page.placing) { cancelPlacing(); return; }
      startPlacing();
    });
    el('pg-cancel').addEventListener('click', function () {
      el('pg-text').value = '';
      cancelPlacing();
      drawPageMarkers();
    });
    el('pg-reload').addEventListener('click', function () {
      if (state.page.docId) loadPage(state.page.docId, state.page.pageKey).catch(function () {});
    });
    el('pg-form').addEventListener('submit', function (event) {
      event.preventDefault();
      createRemark().catch(function () {});
    });
    ['pg-kind', 'pg-category'].forEach(function (id) {
      el(id).addEventListener('change', drawPageMarkers);
    });
    el('pg-list').addEventListener('click', function (event) {
      var item = event.target.closest ? event.target.closest('.app-page-item') : null;
      if (item && item.dataset.id) openCard(item.dataset.id);
    });
    window.addEventListener('resize', function () {
      if (!el('view-page').hidden) drawPageMarkers();
    });
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
    ['f-text', 'f-category', 'f-kind', 'f-status', 'f-tags', 'f-summary'].forEach(function (id) {
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

  function wireRatings() {
    // Делегирование: кнопки шкал перерисовываются на каждую оценку, и вешать
    // обработчики на каждую заново значило бы плодить их.
    el('app-rating-scales').addEventListener('click', function (event) {
      var target = event.target;
      if (target.classList.contains('app-rating-btn')) {
        rate(target.dataset.scale, parseInt(target.dataset.value, 10)).catch(function () {});
      } else if (target.classList.contains('app-rating-clear')) {
        unrate(target.dataset.scale).catch(function () {});
      }
    });
  }

  function wireNotes() {
    el('app-note-form').addEventListener('submit', function (event) {
      event.preventDefault();
      sendNote().catch(function () {});
    });
    el('n-body').addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && state.replyTo) setReplyTo(null);
    });
    el('app-note-list').addEventListener('click', function (event) {
      var target = event.target;
      var id = parseInt(target.dataset.id, 10);
      if (!id) return;
      if (target.classList.contains('app-note-reply')) {
        setReplyTo(id);
      } else if (target.classList.contains('app-note-resolve')) {
        var resolved = target.dataset.resolved === '1';
        apiMutate('PATCH', '/api/notes/' + id, { resolved: !resolved })
          .then(function () { return loadNotes(state.ref); })
          .then(function () { return loadTimeline(state.ref); })
          .catch(function () {});
      } else if (target.classList.contains('app-note-delete')) {
        if (!window.confirm('Удалить комментарий?')) return;
        apiMutate('DELETE', '/api/notes/' + id)
          .then(function () { return loadNotes(state.ref); })
          .then(function () { return loadTimeline(state.ref); })
          .catch(function () {});
      }
    });
  }

  function wireTimeline() {
    el('tl-text-only').addEventListener('change', function (event) {
      state.textOnly = event.target.checked;
      renderTimeline();
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
      } else if (ref.view === 'page') {
        showView('view-page');
        await loadPage(ref.docId, ref.pageKey);
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
  wireRatings();
  wireNotes();
  wireTimeline();
  wireQueue();
  wirePage();
  window.addEventListener('resize', fitPreview);
  window.addEventListener('hashchange', function () { route().catch(function () {}); });
  wireForm();
  start();
})();
