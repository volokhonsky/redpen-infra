(function () {
  'use strict';

  /**
   * pages.js — доска параграфов, список замечаний параграфа и экран страницы
   * со сканом: единственное место, где замечание создают и двигают.
   */

  var W = window.RedPenWork;
  var state = W.state;
  var el = W.el, escapeHtml = W.escapeHtml, formatDay = W.formatDay;
  var setStatus = W.setStatus, apiGet = W.apiGet, apiMutate = W.apiMutate;
  var catTitle = W.catTitle, catDot = W.catDot, pageLabel = W.pageLabel;
  var cats = W.cats, markers = W.markers;
  var fillCategorySelect = W.fillCategorySelect;


  async function loadSections() {
    var docId = await W.loadDocs();
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

  function wireFilters() {
    ['sec-status', 'sec-category', 'sec-source'].forEach(function (id) {
      el(id).addEventListener('change', function () {
        if (state.section) W.loadSection(state.section).catch(function () {});
      });
    });
  }
  W.loadSections = loadSections;
  W.loadSection = loadSection;
  W.loadPage = loadPage;
  W.drawPageMarkers = drawPageMarkers;
  W.wire.push(wireFilters);
  W.wire.push(wirePage);
})();
