(function () {
  'use strict';

  /**
   * remarks.js — всё, что делается с отдельным замечанием: карточка (правка,
   * оценки, тред, лента событий, пул опроса), очередь приёмки и сквозной
   * поиск по всем книгам.
   *
   * Сквозной поиск — слияние двух списков, которые до 2026-08-31 жили в
   * разных приложениях и ни один не был полным: фильтры по документу,
   * странице, виду, автору, тегу и тексту были только в кабинете, а по
   * категории, источнику категории и параграфу — только в редакторе.
   */

  var W = window.RedPenWork;
  var state = W.state;
  var el = W.el, escapeHtml = W.escapeHtml, formatDay = W.formatDay;
  var setStatus = W.setStatus, apiGet = W.apiGet, apiMutate = W.apiMutate;
  var catTitle = W.catTitle, catDot = W.catDot, pageLabel = W.pageLabel;
  var cats = W.cats, preview = W.preview;
  var fillCategorySelect = W.fillCategorySelect, SOURCE_LABELS = W.SOURCE_LABELS;
  var fitFrame = W.fitFrame, renderPreview = W.renderPreview;
  var renderBreadcrumbs = W.renderBreadcrumbs, qs = W.qs;

  // --- форма --------------------------------------------------------------

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
    // Архивное замечание правке не подлежит: форма — только на чтение, пока
    // его не вернут из архива кнопкой «Восстановить».
    var archived = ann && ann.status === 'archived';
    ['f-text', 'f-category', 'f-kind', 'f-status', 'f-tags', 'f-summary'].forEach(function (id) {
      el(id).disabled = !!archived;
    });
    setDirty(false);
    updateHint(ann);
    renderArchiveActions(ann);
  }

  //: Убрать в архив / вернуть / стереть навсегда. Что показать — по статусу и
  //: роли: «В архив» у живого, «Восстановить» у архивного, «Удалить навсегда»
  //: только админу и только для того, что уже в архиве.
  function renderArchiveActions(ann) {
    var archived = !!(ann && ann.status === 'archived');
    el('f-archive').hidden = archived;
    el('f-restore').hidden = !archived;
    el('f-purge').hidden = !(archived && W.isAdmin());
    el('f-archive-note').textContent = archived
      ? 'В архиве: со страницы читателя убрано, правка заблокирована.'
      : '';
  }

  function updateHint(ann) {
    var hint = el('f-hint');
    if (!ann) { hint.textContent = ''; return; }
    hint.textContent = SOURCE_LABELS[ann.categorySource] || '';
    hint.className = 'app-hint' + (ann.categorySource === 'human' ? ' is-ok' : ' is-todo');
  }

  function setDirty(value) {
    state.dirty = value;
    var archived = state.remark && state.remark.status === 'archived';
    el('f-save').disabled = !value || !!archived;
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
      var meta = state.scaleByName[item.scale];
      var scale = (meta && meta.title) || item.scale;
      // Подписанная шкала показывается подписью: «1 из 2» под вопросом
      // «можно ли публиковать» читателю ленты ничего не говорит.
      var answer = item.value + ' из ' + (meta ? meta.max : 5);
      if (meta && meta.options) {
        meta.options.forEach(function (choice) {
          if (choice.value === item.value) answer = choice.label;
        });
      }
      return '<li class="app-rev app-rev--rating' +
        (item.source === 'survey' ? ' app-rev--survey' : '') + '">' + timelineHead(item) +
        '<div class="app-rev-text">' + escapeHtml(scale) + ': ' + escapeHtml(answer) +
        (item.source === 'survey' ? ' <span class="app-agent">опрос</span>' : '') +
        (item.note ? ' — ' + escapeHtml(item.note) : '') + '</div>' +
      '</li>';
    }
    if (item.kind === 'note') {
      // Ответ с улицы (открытый ответ в опросе) не должен путаться с рабочим
      // тредом — тот же модификатор, что и у оценок опроса.
      return '<li class="app-rev app-rev--note' +
        (item.source === 'survey' ? ' app-rev--survey' : '') + '">' + timelineHead(item) +
        '<div class="app-rev-text">' + escapeHtml((item.body || '').slice(0, 200)) +
        (item.source === 'survey' ? ' <span class="app-agent">опрос</span>' : '') +
      '</div>' +
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
      // Подписи вместо цифр там, где шкала — вопрос о решении, а не о мере:
      // «2» под вопросом «можно ли публиковать» не значит ничего.
      var choices = scale.options || [];
      if (!choices.length) {
        for (var v = scale.min; v <= scale.max; v++) choices.push({ value: v, label: String(v) });
      }
      var buttons = choices.map(function (choice) {
        return '<button type="button" class="app-rating-btn' +
          (row.mine === choice.value ? ' is-mine' : '') + '" data-scale="' +
          escapeHtml(scale.name) + '" data-value="' + choice.value + '">' +
          escapeHtml(choice.label) + '</button>';
      }).join('');
      var others = row.count
        ? (scale.options
            // Среднее по «да или нет» ничего не сообщает: нужен расклад.
            ? 'оценили: ' + row.count
            : 'среднее ' + row.average + ' по ' + row.count)
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
    var docId = await W.loadDocs();
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
      var url = preview.remarkUrl(item.docId, label, item.remarkId);
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
    if (!window.confirm('Отклонить замечание ' + item.remarkId +
                        '? Оно будет отправлено в архив.')) return;
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
    state.scaleByName = {};
    state.scales.forEach(function (scale) { state.scaleByName[scale.name] = scale; });
  }

  async function loadTimeline(ref) {
    var data = await apiGet('/api/remarks/' + encodeURIComponent(ref.docId) + '/' +
                            encodeURIComponent(ref.pageKey) + '/' +
                            encodeURIComponent(ref.remarkId) + '/timeline?limit=100');
    state.timeline = data.items || [];
    renderTimeline();
  }

  //: Пул опроса — это «показать ли замечание человеку снаружи закрытого
  //: круга». Решение принимается при чтении замечания, поэтому тумблер стоит
  //: здесь, рядом с оценками, а не только в общем списке.
  function renderPool(remark) {
    var btn = el('f-pool');
    var note = el('f-pool-note');
    if (!btn) return;
    var pooled = !!(remark && remark.inPool);
    btn.textContent = pooled ? 'Убрать из опроса' : 'Вынести на опрос';
    btn.classList.toggle('is-pooled', pooled);
    btn.dataset.pooled = pooled ? '1' : '';
    var answers = (remark && remark.poolAnswers) || 0;
    note.textContent = pooled
      ? (answers ? 'В опросе, ответов: ' + answers : 'В опросе, ответов пока нет')
      : (answers ? 'Не в опросе; полученных ранее ответов: ' + answers : '');
  }

  //: Один переключатель на карточку и на строку списка. Возвращает, удалось
  //: ли: вызывающему остаётся только поправить у себя признак.
  async function togglePoolFor(docId, pageKey, remarkId, pooled) {
    try {
      if (pooled) {
        // Ответы при этом остаются: снять вопрос с раздачи и стереть ответы —
        // разные действия.
        await apiMutate('DELETE', '/api/survey/pool/' + encodeURIComponent(docId) + '/' +
                        encodeURIComponent(pageKey) + '/' + encodeURIComponent(remarkId));
        setStatus('Убрано из опроса.', false);
      } else {
        await apiMutate('POST', '/api/survey/pool',
                        { docId: docId, pageKey: pageKey, remarkId: remarkId });
        setStatus('Вынесено на опрос.', false);
      }
    } catch (e) { return false; }
    state.survey.ready = false;   // таблицу пула перечитаем при следующем заходе
    return true;
  }

  async function togglePool() {
    var ref = state.ref;
    var remark = state.remark;
    if (!ref || !remark) return;
    if (!(await togglePoolFor(ref.docId, ref.pageKey, ref.remarkId, remark.inPool))) return;
    remark.inPool = !remark.inPool;
    renderPool(remark);
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
    renderPool(data.remark);

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

  //: Перерисовать читательский фрейм: адрес тот же, сам он не обновится.
  function reloadPreviewFrame() {
    var frame = el('app-preview');
    if (frame) frame.src = frame.src;
  }

  async function archiveCard() {
    var ref = state.ref;
    if (!ref) return;
    if (!window.confirm('Убрать замечание ' + ref.remarkId + ' в архив? Со страницы ' +
        'читателя оно исчезнет; вернуть можно кнопкой «Восстановить».')) return;
    try {
      await apiMutate('DELETE', '/api/editor/' + encodeURIComponent(ref.docId) + '/' +
        encodeURIComponent(ref.pageKey) + '/' + encodeURIComponent(ref.remarkId));
    } catch (e) { return; }
    state.archive.ready = false;
    setStatus('Убрано в архив.', false);
    await loadCard(ref);
    reloadPreviewFrame();
  }

  async function restoreCard() {
    var ref = state.ref;
    if (!ref) return;
    try {
      await apiMutate('PATCH', '/api/editor/' + encodeURIComponent(ref.docId) + '/' +
        encodeURIComponent(ref.pageKey) + '/' + encodeURIComponent(ref.remarkId) + '/status',
        { status: 'draft', summary: 'возврат из архива' });
    } catch (e) { return; }
    state.archive.ready = false;
    setStatus('Восстановлено в черновики.', false);
    await loadCard(ref);
    reloadPreviewFrame();
  }

  async function purgeCard() {
    var ref = state.ref;
    if (!ref || !W.isAdmin()) return;
    if (!window.confirm('Удалить замечание ' + ref.remarkId + ' НАВСЕГДА?\n\n' +
        'Будут стёрты сам текст, все оценки, рабочие комментарии и вся история ' +
        'правок. Это необратимо.')) return;
    try {
      await apiMutate('DELETE', '/api/editor/' + encodeURIComponent(ref.docId) + '/' +
        encodeURIComponent(ref.pageKey) + '/' + encodeURIComponent(ref.remarkId) + '/purge');
    } catch (e) { return; }
    state.archive.ready = false;
    setStatus('Замечание удалено навсегда.', false);
    window.location.hash = '#/archive';
  }

  // --- сквозной поиск -----------------------------------------------------
  //
  // Один список вместо двух. До слияния фильтры были поделены между
  // приложениями по случайности истории: документ, страница, вид, автор, тег и
  // текст — в кабинете, категория, источник категории и параграф — в
  // редакторе. Ни один из двух списков не был полным.

  function searchShell() {
    el('view-remarks').innerHTML =
      '<div class="app-listhead"><h2>Замечания</h2></div>' +
      '<form id="sr-filters" class="app-filters">' +
        '<label>Документ<select name="docId">' + W.docOptions() + '</select></label>' +
        '<label>Страница<input type="text" name="pageKey" placeholder="напр. 6" /></label>' +
        '<label>Вид<select name="kind"><option value="">Любой</option>' +
          '<option value="major">major</option><option value="minor">minor</option></select></label>' +
        '<label>Статус<select name="status"><option value="">Любой</option>' +
          '<option value="published">опубликован</option><option value="draft">черновик</option>' +
          '</select></label>' +
        '<label>Категория<select name="category" id="sr-category"></select></label>' +
        '<label>Разметка<select name="categorySource"><option value="">любая</option>' +
          '<option value="default">не разобрано</option>' +
          '<option value="tags-backfill">угадана по тегам</option>' +
          '<option value="agent">предложил агент</option>' +
          '<option value="human">выбрал человек</option></select></label>' +
        '<label>Автор<select name="authorId" id="sr-author"><option value="">Все авторы</option></select></label>' +
        '<label>Тег<select name="tag" id="sr-tag"><option value="">Любой</option></select></label>' +
        '<label>Опрос<select name="inPool"><option value="">Любые</option>' +
          '<option value="true">В опросе</option><option value="false">Не в опросе</option></select></label>' +
        '<label>Поиск<input type="text" name="q" placeholder="текст" /></label>' +
        '<button type="submit">Применить</button>' +
        '<button type="button" class="app-btn-secondary" id="sr-reset">Сбросить</button>' +
      '</form>' +
      '<table class="app-table"><thead><tr>' +
        '<th>Документ</th><th>Стр.</th><th>Вид</th><th>Статус</th><th>Категория</th>' +
        '<th>Автор</th><th>Изменено</th><th>Текст</th><th>Действия</th>' +
      '</tr></thead><tbody id="sr-rows"></tbody></table>' +
      '<div id="sr-empty" hidden>Ничего не найдено.</div>' +
      '<button type="button" id="sr-more" class="app-load-more" hidden>Показать ещё</button>';

    fillCategorySelect(el('sr-category'), '');
    el('sr-category').insertAdjacentHTML('afterbegin', '<option value="" selected>любая</option>');
    el('sr-category').value = '';

    el('sr-filters').addEventListener('submit', function (ev) {
      ev.preventDefault();
      var f = new FormData(ev.target);
      var filters = {};
      ['docId', 'pageKey', 'kind', 'status', 'category', 'categorySource',
       'authorId', 'tag', 'inPool', 'q'].forEach(function (name) {
        var v = (f.get(name) || '').trim();
        if (v) filters[name] = v;
      });
      state.search.filters = filters;
      loadSearch(true).catch(function () {});
    });
    el('sr-reset').addEventListener('click', function () {
      el('sr-filters').reset();
      state.search.filters = {};
      loadSearch(true).catch(function () {});
    });
    el('sr-more').addEventListener('click', function () { loadSearch(false).catch(function () {}); });
    loadTagOptions().catch(function () {});
  }

  //: Словарь тегов — тот, что реально в ходу, частые сверху.
  async function loadTagOptions() {
    var data = await apiGet('/api/tags');
    var sel = el('sr-tag');
    if (!sel) return;
    var current = sel.value;
    sel.innerHTML = '<option value="">Любой</option>' + (data.tags || []).map(function (t) {
      return '<option value="' + escapeHtml(t.tag) + '">' + escapeHtml(t.tag) +
             ' (' + t.count + ')</option>';
    }).join('');
    sel.value = current;
  }

  //: Автор — псевдоним, и список авторов набирается из того, что пришло:
  //: отдельной ручки «кто вообще писал» нет и заводить её не нужно.
  function updateAuthorOptions(items) {
    var sel = el('sr-author');
    if (!sel) return;
    var seen = {};
    Array.prototype.forEach.call(sel.options, function (o) { if (o.value) seen[o.value] = true; });
    items.forEach(function (a) {
      if (a.authorId == null || seen[a.authorId]) return;
      seen[a.authorId] = true;
      sel.insertAdjacentHTML('beforeend', '<option value="' + escapeHtml(a.authorId) + '">' +
        escapeHtml(a.authorName || ('#' + a.authorId)) + '</option>');
    });
  }

  async function loadSearch(reset) {
    if (!state.search.ready) { searchShell(); state.search.ready = true; reset = true; }
    if (reset) { state.search.offset = 0; state.search.items = []; }
    var params = Object.assign({}, state.search.filters,
                               { limit: state.search.limit, offset: state.search.offset });
    var data = await apiGet('/api/remarks' + qs(params));
    state.search.items = reset ? data.items : state.search.items.concat(data.items);
    state.search.total = data.total;
    state.search.offset += data.items.length;
    updateAuthorOptions(data.items);
    await renderSearchRows();
  }

  async function renderSearchRows() {
    var items = state.search.items;
    // Читательские номера страниц берутся из манифеста книги; он кэшируется
    // в redpen-preview, поэтому цикл дорог только на первом документе.
    var labels = {};
    for (var i = 0; i < items.length; i++) {
      var key = items[i].docId + '/' + items[i].pageNum;
      if (!(key in labels)) labels[key] = await pageLabel(items[i].docId, items[i].pageNum);
    }

    el('sr-rows').innerHTML = items.map(function (a) {
      var card = '#/ann/' + encodeURIComponent(a.docId) + '/' +
                 encodeURIComponent(a.pageNum) + '/' + encodeURIComponent(a.remarkId);
      var label = labels[a.docId + '/' + a.pageNum];
      var reader = preview.remarkUrl(a.docId, label, a.remarkId);
      var poolLabel = a.inPool
        ? ('В опросе' + (a.poolAnswers ? ' (' + a.poolAnswers + ')' : '') + ' — убрать')
        : 'В опрос';
      var attrs = ' data-doc="' + escapeHtml(a.docId) + '" data-page="' +
                  escapeHtml(a.pageNum) + '" data-ann="' + escapeHtml(a.remarkId) + '"';
      return '<tr>' +
        '<td>' + escapeHtml(a.docId) + '</td>' +
        '<td class="app-nowrap">' + escapeHtml(label) + '</td>' +
        '<td>' + escapeHtml(a.kind) + '</td>' +
        '<td><span class="app-badge app-badge-' + escapeHtml(a.status) + '">' +
          escapeHtml(a.status) + '</span></td>' +
        '<td class="app-nowrap">' + catDot(a.category) + escapeHtml(catTitle(a.category)) + '</td>' +
        '<td>' + escapeHtml(a.authorName || '—') + '</td>' +
        '<td class="app-nowrap">' + escapeHtml(formatDay(a.updatedAt)) + '</td>' +
        '<td>' + escapeHtml((a.text || '').slice(0, 80)) + '</td>' +
        '<td class="app-row-actions">' +
          '<a href="' + card + '">Править</a>' +
          '<a href="' + escapeHtml(reader) + '" target="_blank" rel="noopener">Открыть</a>' +
          (a.status === 'archived' ? '' :
            '<button type="button" class="sr-status"' + attrs + ' data-newstatus="' +
            (a.status === 'draft' ? 'published' : 'draft') + '">' +
            (a.status === 'draft' ? 'Опубликовать' : 'В черновик') + '</button>') +
          (a.status === 'archived' ? '' :
            '<button type="button" class="app-btn-secondary sr-delete"' + attrs + '>В архив</button>') +
          '<button type="button" class="app-btn-secondary sr-history"' + attrs + '>История</button>' +
          '<button type="button" class="app-btn-secondary sr-pool' + (a.inPool ? ' is-pooled' : '') +
            '"' + attrs + ' data-pooled="' + (a.inPool ? '1' : '') + '">' + poolLabel + '</button>' +
        '</td>' +
      '</tr>';
    }).join('');

    el('sr-empty').hidden = !!items.length;
    el('sr-more').hidden = items.length >= state.search.total;
    wireSearchRows();
  }

  function searchRow(btn) {
    return state.search.items.filter(function (a) {
      return a.docId === btn.dataset.doc && a.pageNum === btn.dataset.page &&
             a.remarkId === btn.dataset.ann;
    })[0];
  }

  function wireSearchRows() {
    var rows = el('sr-rows');
    function each(sel, fn) {
      Array.prototype.forEach.call(rows.querySelectorAll(sel), function (btn) {
        btn.addEventListener('click', function () { fn(btn); });
      });
    }
    // Статус меняется узким PATCH. Кабинет для этого пересылал замечание
    // целиком (PUT с kind+text+coords): в журнал попадала ревизия «переписали
    // объект», а не «опубликовал», и уходила она без резюме правки.
    each('.sr-status', async function (btn) {
      try {
        await apiMutate('PATCH', editorPath(btn) + '/status',
                        { status: btn.dataset.newstatus, summary: 'из списка замечаний' });
      } catch (e) { return; }
      var item = searchRow(btn);
      if (item) item.status = btn.dataset.newstatus;
      setStatus('Статус обновлён.', false);
      renderSearchRows().catch(function () {});
    });
    each('.sr-delete', async function (btn) {
      if (!window.confirm('Убрать замечание в архив? Со страницы читателя оно ' +
                          'исчезнет; вернуть можно на вкладке «Архив».')) return;
      try { await apiMutate('DELETE', editorPath(btn)); } catch (e) { return; }
      var item = searchRow(btn);
      if (item) item.status = 'archived';
      state.archive.ready = false;
      setStatus('Убрано в архив.', false);
      renderSearchRows().catch(function () {});
    });
    each('.sr-history', function (btn) {
      state.hist.filters = { docId: btn.dataset.doc, remarkId: btn.dataset.ann };
      state.hist.ready = false;
      window.location.hash = '#/history';
    });
    each('.sr-pool', async function (btn) {
      var item = searchRow(btn);
      if (!(await togglePoolFor(btn.dataset.doc, btn.dataset.page, btn.dataset.ann,
                                !!btn.dataset.pooled))) return;
      if (item) item.inPool = !btn.dataset.pooled;
      renderSearchRows().catch(function () {});
    });
  }

  function editorPath(btn) {
    return '/api/editor/' + encodeURIComponent(btn.dataset.doc) + '/' +
           encodeURIComponent(btn.dataset.page) + '/' + encodeURIComponent(btn.dataset.ann);
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
    el('f-pool').addEventListener('click', function () {
      togglePool().catch(function () {});
    });
    el('f-archive').addEventListener('click', function () { archiveCard().catch(function () {}); });
    el('f-restore').addEventListener('click', function () { restoreCard().catch(function () {}); });
    el('f-purge').addEventListener('click', function () { purgeCard().catch(function () {}); });
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

  // --- архив ------------------------------------------------------------------
  //
  // Своя вкладка и своё состояние (state.archive), чтобы фильтры поиска и
  // архива не перетирали друг друга. status=archived здесь зашит; в остальном
  // экран собран по образцу сквозного поиска.

  function archiveShell() {
    el('view-archive').innerHTML =
      '<div class="app-listhead"><h2>Архив</h2></div>' +
      '<p class="app-note">Замечания, убранные из работы. На странице читателя ' +
      'их нет. «Восстановить» возвращает в черновики; «Удалить навсегда» ' +
      '(только админ) стирает замечание со всей историей и оценками.</p>' +
      '<form id="ar-filters" class="app-filters">' +
        '<label>Документ<select name="docId">' + W.docOptions() + '</select></label>' +
        '<label>Страница<input type="text" name="pageKey" placeholder="напр. 6" /></label>' +
        '<label>Тег<select name="tag" id="ar-tag"><option value="">Любой</option></select></label>' +
        '<label>Поиск<input type="text" name="q" placeholder="текст" /></label>' +
        '<button type="submit">Применить</button>' +
        '<button type="button" class="app-btn-secondary" id="ar-reset">Сбросить</button>' +
      '</form>' +
      '<table class="app-table"><thead><tr>' +
        '<th>Документ</th><th>Стр.</th><th>Категория</th><th>Изменено</th>' +
        '<th>Текст</th><th>Действия</th>' +
      '</tr></thead><tbody id="ar-rows"></tbody></table>' +
      '<div id="ar-empty" hidden>Архив пуст.</div>' +
      '<button type="button" id="ar-more" class="app-load-more" hidden>Показать ещё</button>';

    el('ar-filters').addEventListener('submit', function (ev) {
      ev.preventDefault();
      var f = new FormData(ev.target);
      var filters = {};
      ['docId', 'pageKey', 'tag', 'q'].forEach(function (name) {
        var v = (f.get(name) || '').trim();
        if (v) filters[name] = v;
      });
      state.archive.filters = filters;
      loadArchive(true).catch(function () {});
    });
    el('ar-reset').addEventListener('click', function () {
      el('ar-filters').reset();
      state.archive.filters = {};
      loadArchive(true).catch(function () {});
    });
    el('ar-more').addEventListener('click', function () { loadArchive(false).catch(function () {}); });

    apiGet('/api/tags').then(function (data) {
      var sel = el('ar-tag');
      if (!sel) return;
      sel.innerHTML = '<option value="">Любой</option>' + (data.tags || []).map(function (t) {
        return '<option value="' + escapeHtml(t.tag) + '">' + escapeHtml(t.tag) +
               ' (' + t.count + ')</option>';
      }).join('');
    }).catch(function () {});
  }

  async function loadArchive(reset) {
    if (!state.archive.ready) { archiveShell(); state.archive.ready = true; reset = true; }
    if (reset === undefined) reset = true;
    if (reset) { state.archive.offset = 0; state.archive.items = []; }
    var params = Object.assign({ status: 'archived' }, state.archive.filters,
                               { limit: state.archive.limit, offset: state.archive.offset });
    var data = await apiGet('/api/remarks' + qs(params));
    state.archive.items = reset ? data.items : state.archive.items.concat(data.items);
    state.archive.total = data.total;
    state.archive.offset += data.items.length;
    await renderArchiveRows();
  }

  async function renderArchiveRows() {
    var items = state.archive.items;
    var labels = {};
    for (var i = 0; i < items.length; i++) {
      var key = items[i].docId + '/' + items[i].pageNum;
      if (!(key in labels)) labels[key] = await pageLabel(items[i].docId, items[i].pageNum);
    }
    var admin = W.isAdmin();
    el('ar-rows').innerHTML = items.map(function (a) {
      var card = '#/ann/' + encodeURIComponent(a.docId) + '/' +
                 encodeURIComponent(a.pageNum) + '/' + encodeURIComponent(a.remarkId);
      var label = labels[a.docId + '/' + a.pageNum];
      var attrs = ' data-doc="' + escapeHtml(a.docId) + '" data-page="' +
                  escapeHtml(a.pageNum) + '" data-ann="' + escapeHtml(a.remarkId) + '"';
      return '<tr>' +
        '<td>' + escapeHtml(a.docId) + '</td>' +
        '<td class="app-nowrap">' + escapeHtml(label) + '</td>' +
        '<td class="app-nowrap">' + catDot(a.category) + escapeHtml(catTitle(a.category)) + '</td>' +
        '<td class="app-nowrap">' + escapeHtml(formatDay(a.updatedAt)) + '</td>' +
        '<td>' + escapeHtml((a.text || '').slice(0, 80)) + '</td>' +
        '<td class="app-row-actions">' +
          '<a href="' + card + '">Открыть</a>' +
          '<button type="button" class="ar-restore"' + attrs + '>Восстановить</button>' +
          (admin ? '<button type="button" class="app-btn-danger ar-purge"' + attrs +
            '>Удалить навсегда</button>' : '') +
        '</td>' +
      '</tr>';
    }).join('');

    el('ar-empty').hidden = !!items.length;
    el('ar-more').hidden = items.length >= state.archive.total;
    wireArchiveRows();
  }

  function archiveRow(btn) {
    return state.archive.items.filter(function (a) {
      return a.docId === btn.dataset.doc && a.pageNum === btn.dataset.page &&
             a.remarkId === btn.dataset.ann;
    })[0];
  }

  function dropFromArchive(btn) {
    var gone = archiveRow(btn);
    state.archive.items = state.archive.items.filter(function (a) { return a !== gone; });
    state.archive.total = Math.max(0, state.archive.total - 1);
  }

  function wireArchiveRows() {
    var rows = el('ar-rows');
    function each(sel, fn) {
      Array.prototype.forEach.call(rows.querySelectorAll(sel), function (btn) {
        btn.addEventListener('click', function () { fn(btn); });
      });
    }
    each('.ar-restore', async function (btn) {
      try {
        await apiMutate('PATCH', editorPath(btn) + '/status',
                        { status: 'draft', summary: 'возврат из архива' });
      } catch (e) { return; }
      dropFromArchive(btn);
      setStatus('Восстановлено в черновики.', false);
      renderArchiveRows().catch(function () {});
    });
    each('.ar-purge', async function (btn) {
      if (!W.isAdmin()) return;
      if (!window.confirm('Удалить замечание ' + btn.dataset.ann + ' НАВСЕГДА?\n\n' +
          'Будут стёрты текст, оценки, рабочие комментарии и вся история правок. ' +
          'Это необратимо.')) return;
      try { await apiMutate('DELETE', editorPath(btn) + '/purge'); } catch (e) { return; }
      dropFromArchive(btn);
      setStatus('Удалено навсегда.', false);
      renderArchiveRows().catch(function () {});
    });
  }

  W.loadCard = loadCard;
  W.loadQueue = loadQueue;
  W.loadSearch = loadSearch;
  W.loadArchive = loadArchive;
  W.togglePoolFor = togglePoolFor;
  W.wire.push(wireForm);
  W.wire.push(wireRatings);
  W.wire.push(wireNotes);
  W.wire.push(wireTimeline);
  W.wire.push(wireQueue);
})();
