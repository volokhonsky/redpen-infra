(function () {
  'use strict';

  /**
   * admin.js — экраны, переехавшие из кабинета: журнал ревизий с откатом,
   * опрос (пул и сводка) и админские таблицы.
   *
   * Разграничение здесь ровно то же, что и везде: журнал и пул — редакторские,
   * сводка ответов, приглашения, участники, перепубликация и логи — админские.
   */

  var W = window.RedPenWork;
  var state = W.state;
  var el = W.el, escapeHtml = W.escapeHtml, formatDay = W.formatDay;
  var setStatus = W.setStatus, apiGet = W.apiGet, apiMutate = W.apiMutate;
  var pageLabel = W.pageLabel, preview = W.preview, qs = W.qs;

  //: Читательские номера страниц одной пачкой: манифест кэшируется в
  //: redpen-preview, поэтому цикл дорог только на первом документе.
  async function labelsFor(items) {
    var out = {};
    for (var i = 0; i < items.length; i++) {
      var key = items[i].docId + '/' + items[i].pageNum;
      if (!(key in out)) out[key] = await pageLabel(items[i].docId, items[i].pageNum);
    }
    return out;
  }

  function readerCell(item, labels) {
    var label = labels[item.docId + '/' + item.pageNum];
    var href = preview.remarkUrl(item.docId, label, item.remarkId);
    var card = '#/ann/' + encodeURIComponent(item.docId) + '/' +
               encodeURIComponent(item.pageNum) + '/' + encodeURIComponent(item.remarkId);
    return '<a href="' + card + '">Править</a>' +
           '<a href="' + escapeHtml(href) + '" target="_blank" rel="noopener">Открыть</a>';
  }

  // --- журнал ревизий -----------------------------------------------------

  //: Словарь действий живёт на сервере (scripts/api/remark_actions.py), оттуда
  //: же приходит готовый ярлык каждой ревизии — здесь только значения фильтра.
  var CHANGED_OPTIONS = [
    ['', 'Любое'], ['text', 'правка текста'], ['coords', 'перенос маркера'],
    ['kind', 'смена вида'], ['publish', 'публикация'],
    ['unpublish', 'возврат в черновики'], ['delete', 'удаление'],
    ['restore', 'восстановление'], ['category', 'смена категории'],
    ['tags', 'правка тегов'], ['revert', 'откат']
  ].map(function (pair) {
    return '<option value="' + pair[0] + '">' + pair[1] + '</option>';
  }).join('');

  function historyShell() {
    el('view-history').innerHTML =
      '<div class="app-listhead"><h2>История правок</h2></div>' +
      '<form id="hs-filters" class="app-filters">' +
        '<label>Документ<select name="docId">' + W.docOptions(state.hist.filters.docId) + '</select></label>' +
        '<label>Автор<select name="authorId" id="hs-author"><option value="">Все авторы</option></select></label>' +
        '<label>Что изменилось<select name="changed">' + CHANGED_OPTIONS + '</select></label>' +
        '<button type="submit">Применить</button>' +
        '<button type="button" class="app-btn-secondary" id="hs-reset">Сбросить</button>' +
      '</form>' +
      '<div id="hs-note" class="app-notice" hidden></div>' +
      '<div id="hs-list"></div>' +
      '<div id="hs-empty" hidden>История пуста.</div>' +
      '<button type="button" id="hs-more" class="app-load-more" hidden>Показать ещё</button>';

    el('hs-filters').addEventListener('submit', function (ev) {
      ev.preventDefault();
      var f = new FormData(ev.target);
      state.hist.filters = {
        docId: f.get('docId') || undefined,
        authorId: f.get('authorId') || undefined,
        // Фильтр по составу изменения, а не по происхождению записи: «покажи
        // только правки текста» — вопрос про changed, не про action.
        changed: f.get('changed') || undefined
      };
      loadHistory(true).catch(function () {});
    });
    el('hs-reset').addEventListener('click', function () {
      el('hs-filters').reset();
      state.hist.filters = {};
      loadHistory(true).catch(function () {});
    });
    el('hs-more').addEventListener('click', function () { loadHistory(false).catch(function () {}); });
  }

  async function loadHistory(reset) {
    if (!state.hist.ready) { historyShell(); state.hist.ready = true; reset = true; }
    if (reset) { state.hist.offset = 0; state.hist.items = []; }
    var params = Object.assign({}, state.hist.filters,
                               { limit: state.hist.limit, offset: state.hist.offset });
    var data = await apiGet('/api/history' + qs(params));
    state.hist.items = reset ? data.items : state.hist.items.concat(data.items);
    state.hist.hasMore = data.hasMore;
    state.hist.offset += data.items.length;

    var sel = el('hs-author');
    var seen = {};
    Array.prototype.forEach.call(sel.options, function (o) { if (o.value) seen[o.value] = true; });
    data.items.forEach(function (h) {
      if (h.authorId == null || seen[h.authorId]) return;
      seen[h.authorId] = true;
      sel.insertAdjacentHTML('beforeend', '<option value="' + escapeHtml(h.authorId) + '">' +
        escapeHtml(h.authorName || ('#' + h.authorId)) + '</option>');
    });

    await renderHistoryList();
  }

  async function renderHistoryList() {
    var note = el('hs-note');
    if (state.hist.filters.remarkId) {
      note.hidden = false;
      note.innerHTML = 'Фильтр по замечанию ' + escapeHtml(state.hist.filters.remarkId) +
        ' в документе ' + escapeHtml(state.hist.filters.docId || '') +
        ' <button type="button" class="app-btn-secondary" id="hs-clear">Сбросить фильтр</button>';
      el('hs-clear').addEventListener('click', function () {
        state.hist.filters = {};
        loadHistory(true).catch(function () {});
      });
    } else {
      note.hidden = true;
    }

    var labels = await labelsFor(state.hist.items);
    el('hs-list').innerHTML = state.hist.items.map(function (h) {
      var snap = h.snapshot || {};
      var card = '#/ann/' + encodeURIComponent(h.docId) + '/' +
                 encodeURIComponent(h.pageNum) + '/' + encodeURIComponent(h.remarkId);
      return '<div class="app-history-item">' +
        '<div class="app-history-meta">' + escapeHtml(formatDay(h.createdAt)) + ' · ' +
          escapeHtml(h.authorName || '—') + ' · ' + escapeHtml(h.actionLabel || h.action) +
          ' · ' + escapeHtml(h.docId) + ' / стр. ' +
          escapeHtml(labels[h.docId + '/' + h.pageNum]) + ' / ' +
          '<a href="' + card + '">' + escapeHtml(h.remarkId) + '</a>' +
        '</div>' +
        '<div class="app-history-text">' + escapeHtml((snap.text || '').slice(0, 200)) + '</div>' +
        '<button type="button" class="app-btn-secondary hs-revert" data-hist="' + h.id +
          '">Откатить к этому состоянию</button>' +
      '</div>';
    }).join('');

    el('hs-empty').hidden = !!state.hist.items.length;
    el('hs-more').hidden = !state.hist.hasMore;

    Array.prototype.forEach.call(el('hs-list').querySelectorAll('.hs-revert'), function (btn) {
      btn.addEventListener('click', function () { revert(btn.dataset.hist); });
    });
  }

  async function revert(histId) {
    if (!window.confirm('Откатить замечание к этому состоянию?')) return;
    try {
      await apiMutate('POST', '/api/history/' + encodeURIComponent(histId) + '/revert');
    } catch (e) { return; }
    setStatus('Откат выполнен.', false);
    state.search.ready = false;   // список замечаний устарел
    loadHistory(true).catch(function () {});
  }

  // --- опрос --------------------------------------------------------------
  //
  // Пул — список заданных вопросов, результаты — полученные ответы; смотрят на
  // них подряд, поэтому экран один. Но права разные: пул ведёт редактор,
  // сводку видит только админ. Оценки участников круга сюда не попадают:
  // это другой голос, и складывать их в одно среднее нельзя.

  function surveyShell() {
    el('view-survey').innerHTML =
      '<div class="app-listhead"><h2>Опрос</h2></div>' +
      '<div class="app-admin-section">' +
        '<form id="sv-filters" class="app-filters">' +
          '<label>Документ<select name="docId">' + W.docOptions() + '</select></label>' +
          '<button type="submit">Показать</button>' +
        '</form>' +
        '<p class="app-muted">Опросник: ' +
          '<a href="../survey/" target="_blank" rel="noopener">/survey/</a> — ' +
          'ссылка для тех, кого просят оценить. Входа не требует.</p>' +
      '</div>' +
      '<div class="app-admin-section">' +
        '<h3>Пул для оценки</h3>' +
        '<p class="app-muted">Из этого списка опросник выдаёт людям случайные ' +
          'замечания. Положить сюда — тумблером «В опрос» в списке замечаний ' +
          'или в карточке.</p>' +
        '<table class="app-table"><thead><tr>' +
          '<th>Документ</th><th>Стр.</th><th>Замечание</th><th class="num">Ответов</th><th></th>' +
        '</tr></thead><tbody id="sv-pool-rows"></tbody></table>' +
        '<p id="sv-pool-empty" class="app-muted">Пул пуст: опросу нечего показывать.</p>' +
      '</div>' +
      (W.isAdmin() ?
      '<div class="app-admin-section">' +
        '<h3>Результаты</h3>' +
        '<table class="app-table"><thead><tr>' +
          '<th>Документ</th><th>Стр.</th><th>Замечание</th><th class="num">Ответивших</th>' +
          '<th class="num">Интересность</th><th class="num">Важность</th>' +
          '<th>Публиковать</th><th></th>' +
        '</tr></thead><tbody id="sv-result-rows"></tbody></table>' +
        '<p id="sv-result-empty" class="app-muted">Ответов пока нет.</p>' +
        '<button type="button" id="sv-more" class="app-load-more" hidden>Ещё</button>' +
      '</div>' : '');

    el('sv-filters').addEventListener('submit', function (ev) {
      ev.preventDefault();
      state.survey.docId = ev.target.elements.docId.value || undefined;
      loadSurvey(true).catch(function () {});
    });
    if (el('sv-more')) {
      el('sv-more').addEventListener('click', function () {
        loadSurveyResults(false).catch(function () {});
      });
    }
  }

  async function loadSurvey(refresh) {
    if (!state.survey.ready || refresh) { surveyShell(); state.survey.ready = true; }
    await loadSurveyPool();
    // Сводка — админская: редактор кладёт вопросы, но не читает, как на них
    // ответили (docs/anonymity-model.md).
    if (W.isAdmin()) await loadSurveyResults(true);
  }

  async function loadSurveyPool() {
    var data = await apiGet('/api/survey/pool' + qs({ docId: state.survey.docId }));
    state.survey.pool = data.items || [];
    var labels = await labelsFor(state.survey.pool);
    el('sv-pool-rows').innerHTML = state.survey.pool.map(function (item) {
      return '<tr>' +
        '<td>' + escapeHtml(item.docId) + '</td>' +
        '<td class="app-nowrap">' + escapeHtml(labels[item.docId + '/' + item.pageNum]) + '</td>' +
        '<td>' + escapeHtml((item.text || item.remarkId).slice(0, 90)) + '</td>' +
        '<td class="num">' + (item.answers || 0) + '</td>' +
        '<td class="app-row-actions">' + readerCell(item, labels) +
          '<button type="button" class="app-btn-secondary sv-remove" data-doc="' +
            escapeHtml(item.docId) + '" data-page="' + escapeHtml(item.pageNum) +
            '" data-ann="' + escapeHtml(item.remarkId) + '">Убрать</button>' +
        '</td>' +
      '</tr>';
    }).join('');
    el('sv-pool-empty').hidden = !!state.survey.pool.length;

    Array.prototype.forEach.call(el('sv-pool-rows').querySelectorAll('.sv-remove'), function (btn) {
      btn.addEventListener('click', async function () {
        // Тем же переключателем, что и в списке замечаний, и в карточке.
        if (!(await W.togglePoolFor(btn.dataset.doc, btn.dataset.page, btn.dataset.ann, true))) return;
        state.search.ready = false;
        loadSurveyPool().catch(function () {});
      });
    });
  }

  async function loadSurveyResults(reset) {
    if (reset) { state.survey.resultsOffset = 0; state.survey.results = []; }
    var data = await apiGet('/api/survey/results' + qs({
      docId: state.survey.docId, limit: 100, offset: state.survey.resultsOffset
    }));
    state.survey.results = state.survey.results.concat(data.items || []);
    state.survey.resultsTotal = data.total || 0;
    state.survey.resultsOffset = state.survey.results.length;

    var labels = await labelsFor(state.survey.results);
    el('sv-result-rows').innerHTML = state.survey.results.map(function (item) {
      var yes = item.admissibility.yes, no = item.admissibility.no;
      // Расклад, а не среднее: среднее по «да или нет» ничего не сообщает.
      var verdict = (yes + no) ? (yes + ' да / ' + no + ' нет') : '—';
      var avg = function (s) {
        return s.average == null ? '—' : s.average + ' (' + s.count + ')';
      };
      return '<tr>' +
        '<td>' + escapeHtml(item.docId) + '</td>' +
        '<td class="app-nowrap">' + escapeHtml(labels[item.docId + '/' + item.pageNum]) + '</td>' +
        '<td>' + escapeHtml((item.text || item.remarkId).slice(0, 90)) + '</td>' +
        '<td class="num">' + item.raters + '</td>' +
        '<td class="num">' + avg(item.interest) + '</td>' +
        '<td class="num">' + avg(item.importance) + '</td>' +
        '<td>' + escapeHtml(verdict) + '</td>' +
        '<td class="app-row-actions">' + readerCell(item, labels) + '</td>' +
      '</tr>';
    }).join('');
    el('sv-result-empty').hidden = !!state.survey.results.length;
    el('sv-more').hidden = state.survey.results.length >= state.survey.resultsTotal;
  }

  // --- админка ------------------------------------------------------------

  var ROLE_LABELS = { viewer: 'читатель', editor: 'редактор', admin: 'админ' };

  async function loadAdmin() {
    if (!W.requireAdmin()) return;
    el('view-admin').innerHTML =
      '<div class="app-listhead"><h2>Администрирование</h2></div>' +
      '<div class="app-admin-section" id="ad-invites"><h3>Приглашения</h3></div>' +
      '<div class="app-admin-section" id="ad-users"><h3>Участники</h3></div>' +
      '<div class="app-admin-section"><h3>Публикация</h3>' +
        '<button type="button" id="ad-publish">Перепубликовать всё</button> ' +
        '<span id="ad-publish-result" class="app-hint"></span>' +
      '</div>' +
      '<div class="app-admin-section" id="ad-logs"><h3>Логи</h3>' +
        '<button type="button" id="ad-logs-refresh" class="app-btn-secondary">Обновить</button>' +
        '<pre id="ad-logs-pre"></pre>' +
      '</div>';

    el('ad-publish').addEventListener('click', async function () {
      try {
        var result = await apiMutate('POST', '/api/admin/publish-all');
        el('ad-publish-result').textContent =
          'Готово: ' + result.pages + ' стр., ошибок: ' + result.failed;
      } catch (e) { /* сообщение уже показано */ }
    });
    el('ad-logs-refresh').addEventListener('click', function () { loadLogs().catch(function () {}); });

    await Promise.all([loadInvites(), loadUsers(), loadLogs()]);
  }

  // Приглашение — одноразовый код, который админ передаёт человеку вне системы.
  // Ни email, ни имени приглашаемого система не знает: именной список — это и
  // есть то, чего мы не храним (docs/anonymity-model.md).
  async function loadInvites(keepCode) {
    var host = el('ad-invites');
    var data = await apiGet('/api/admin/invites');
    var rows = (data.invites || []).map(function (inv) {
      var used = inv.usedAt ? 'использовано ' + escapeHtml(formatDay(inv.usedAt)) : 'не использовано';
      return '<tr><td><code>' + escapeHtml(inv.codeHash.slice(0, 12)) + '…</code></td>' +
        '<td>' + escapeHtml(inv.role) + '</td>' +
        '<td>' + escapeHtml(inv.note || '') + '</td>' +
        '<td>' + escapeHtml(formatDay(inv.createdAt)) + '</td>' +
        '<td>' + used + '</td>' +
        '<td>' + (inv.usedAt ? '' :
          '<button type="button" class="app-btn-secondary ad-invite-del" data-hash="' +
          escapeHtml(inv.codeHash) + '">Отозвать</button>') + '</td></tr>';
    }).join('');
    host.innerHTML = '<h3>Приглашения</h3>' +
      '<p class="app-hint">Код показывается один раз при выписке — передайте его ' +
      'вне системы. Потерянный код не восстанавливается, выпишите новый.</p>' +
      '<table class="app-table"><thead><tr><th>Код (хеш)</th><th>Роль</th><th>Пометка</th>' +
      '<th>Выписано</th><th>Статус</th><th></th></tr></thead><tbody>' + rows + '</tbody></table>' +
      '<form id="ad-invite-form" class="app-admin-form">' +
        '<select name="role">' +
          '<option value="editor">редактор</option>' +
          '<option value="admin">админ</option>' +
          '<option value="viewer">читатель</option>' +
        '</select>' +
        '<input type="text" name="note" placeholder="пометка (без имён)" maxlength="200" />' +
        '<button type="submit">Выписать</button>' +
      '</form>' +
      '<div id="ad-invite-code">' + (keepCode || '') + '</div>';

    el('ad-invite-form').addEventListener('submit', async function (ev) {
      ev.preventDefault();
      var f = new FormData(ev.target);
      var result;
      try {
        result = await apiMutate('POST', '/api/admin/invites',
                                 { role: f.get('role'), note: f.get('note') || null });
      } catch (e) { return; }
      setStatus('Приглашение выписано.', false);
      // Код переживает перерисовку списка: второй раз он не покажется.
      var code = '<p class="app-invite-code">Код: <code>' + escapeHtml(result.code) + '</code><br>' +
                 'Скопируйте сейчас — второй раз он не покажется.</p>';
      loadInvites(code).catch(function () {});
    });
    Array.prototype.forEach.call(host.querySelectorAll('.ad-invite-del'), function (btn) {
      btn.addEventListener('click', async function () {
        try {
          await apiMutate('DELETE', '/api/admin/invites/' + encodeURIComponent(btn.dataset.hash));
        } catch (e) { return; }
        loadInvites().catch(function () {});
      });
    });
  }

  //: Роль и отставка правятся прямо здесь: ручки для этого были с самого
  //: начала (POST /api/admin/users/{id}/role и /retire), а интерфейса к ним
  //: не было — таблица участников была только для чтения.
  async function loadUsers() {
    var host = el('ad-users');
    var data = await apiGet('/api/admin/users');
    var rows = (data.users || []).map(function (u) {
      var options = Object.keys(ROLE_LABELS).map(function (r) {
        return '<option value="' + r + '"' + (u.role === r ? ' selected' : '') + '>' +
          escapeHtml(ROLE_LABELS[r]) + '</option>';
      }).join('');
      var self = state.user && state.user.userId === u.id;
      return '<tr><td>' + escapeHtml(u.displayName || ('Участник №' + u.id)) + '</td>' +
        '<td>' + escapeHtml(u.kind === 'agent' ? 'агент' : 'человек') + '</td>' +
        '<td><select class="ad-role" data-user="' + u.id + '"' + (self ? ' disabled' : '') + '>' +
          options + '</select></td>' +
        '<td>' + escapeHtml(formatDay(u.createdAt)) + '</td>' +
        '<td>' + escapeHtml(formatDay(u.lastLoginAt)) + '</td>' +
        '<td>' + (self ? '' :
          '<button type="button" class="app-btn-secondary ad-retire" data-user="' + u.id +
          '">Отвязать</button>') + '</td></tr>';
    }).join('');
    host.innerHTML = '<h3>Участники</h3>' +
      '<p class="app-hint">Отставка отвязывает учётную запись и гасит сессии; ' +
      'написанное человеком остаётся, подпись — тоже. Свою роль сменить нельзя: ' +
      'иначе последний админ мог бы разжаловать сам себя.</p>' +
      '<table class="app-table"><thead><tr><th>Псевдоним</th><th>Вид</th><th>Роль</th>' +
      '<th>Создан</th><th>Последний вход</th><th></th></tr></thead><tbody>' + rows + '</tbody></table>';

    Array.prototype.forEach.call(host.querySelectorAll('.ad-role'), function (sel) {
      sel.addEventListener('change', async function () {
        try {
          await apiMutate('POST', '/api/admin/users/' + encodeURIComponent(sel.dataset.user) + '/role',
                          { role: sel.value });
        } catch (e) { loadUsers().catch(function () {}); return; }
        setStatus('Роль изменена.', false);
      });
    });
    Array.prototype.forEach.call(host.querySelectorAll('.ad-retire'), function (btn) {
      btn.addEventListener('click', async function () {
        if (!window.confirm('Отвязать участника? Он потеряет доступ, написанное останется.')) return;
        try {
          await apiMutate('POST', '/api/admin/users/' + encodeURIComponent(btn.dataset.user) + '/retire');
        } catch (e) { return; }
        setStatus('Участник отвязан.', false);
        loadUsers().catch(function () {});
      });
    });
  }

  async function loadLogs() {
    var data = await apiGet('/api/logs?lines=200');
    el('ad-logs-pre').textContent = (data.logs || []).map(function (l) {
      return l.timestamp + ' | ' + l.level + ' | ' + l.message;
    }).join('\n');
  }

  W.loadHistory = loadHistory;
  W.loadSurvey = loadSurvey;
  W.loadAdmin = loadAdmin;
})();
