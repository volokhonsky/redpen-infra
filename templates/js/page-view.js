/**
 * Страница разбора: <doc>/pages/<label>/index.html
 *
 * Панель со списком аннотаций приезжает уже отрендеренной из сборки — это то,
 * что индексируют поисковики. Этот скрипт её оживляет и НИЧЕГО не загружает:
 * все аннотации страницы, включая черновики, лежат в инлайновом
 * <script type="application/json" id="redpen-page-data">.
 *
 * Отсюда следует главное правило файла: здесь не должно появиться ни fetch(),
 * ни XMLHttpRequest, ни обращения к API. Просмотрщик обязан работать с флешки
 * и офлайн (см. docs/README.md, «Ключевое ограничение»); на это есть тест.
 *
 * Намеренно не переиспользует main.js/remarks.js: те написаны под
 * SPA-переключение страниц, которого здесь больше нет — навигация это обычные
 * ссылки.
 */
(function () {
  'use strict';

  var MARKER_SIZES = { major: 90, minor: 50, small: 25 };
  // Размер по-прежнему задаётся видом замечания (major/minor), а цвет —
  // категорией приёма (redpen-categories.js). Раньше цвет тоже шёл от вида,
  // поэтому
  // привычка «красный = главное» на этих страницах больше не действует;
  // легенда на титульной переписана под категории.
  var CATEGORY_FALLBACK_COLOR = '#546E7A';

  /**
   * Вид замечания: major (крупный маркер) или minor (обычный).
   *
   * Читает и новый ключ `kind`, и старый `annType` со старыми значениями:
   * страницы перерисовываются по одной, поэтому в момент выкладки этот JS
   * встречает и то и другое. Легаси-ветка снимается в фазе 6 переименования.
   * Упразднённый `general` показываем как обычное замечание.
   */
  function remarkKind(ann) {
    var k = (ann && (ann.kind || ann.annType)) || 'minor';
    if (k === 'main') return 'major';
    if (k === 'comment' || k === 'general') return 'minor';
    return k;
  }

  function markerColor(ann) {
    var cats = window.RedPenCategories;
    if (!cats) return CATEGORY_FALLBACK_COLOR;
    return cats.COLORS[cats.categoryFor(ann)] || CATEGORY_FALLBACK_COLOR;
  }

  function categorySlug(ann) {
    var cats = window.RedPenCategories;
    return cats ? cats.categoryFor(ann) : 'other';
  }

  var data = [];
  var visible = [];
  var overlay = null;
  var image = null;
  var panel = null;
  var pinnedId = null;

  /**
   * Умеет ли устройство наводить курсор. Именно это, а не ширина экрана,
   * определяет поведение: на тач-устройстве вместо подсветки по наведению
   * открывается #mobile-overlay. Ширину брать нельзя — при нулевой или ещё не
   * устоявшейся вёрстке медиазапрос по ширине ложно срабатывает как «мобильный»
   * и подсветка молча перестаёт работать на десктопе.
   */
  function canHover() {
    return window.matchMedia('(hover: hover) and (pointer: fine)').matches;
  }

  // --- данные ------------------------------------------------------------

  function readPageData() {
    var node = document.getElementById('redpen-page-data');
    if (!node) return [];
    try {
      var parsed = JSON.parse(node.textContent);
      return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
      console.error('redpen: не удалось разобрать данные страницы', e);
      return [];
    }
  }

  // --- фильтр по тегам ---------------------------------------------------
  // Семантика та же, что у просмотрщика: ?tags= включает (ИЛИ), ?notags=
  // исключает, исключение сильнее. Черновики скрыты, пока их не попросят явно.

  function urlParam(name) {
    var params = new URLSearchParams(window.location.search);
    return params.get(name);
  }

  function parseTagList(raw) {
    if (!raw) return [];
    return raw.split(',').map(function (t) {
      return t.trim().toLowerCase();
    }).filter(Boolean);
  }

  function remarkTags(ann) {
    var tags = (ann.tags || []).map(function (t) {
      return String(t).toLowerCase();
    });
    if (ann.draft && tags.indexOf('draft') === -1) tags.push('draft');
    return tags;
  }

  function getFilter() {
    var include = parseTagList(urlParam('tags'));
    var exclude = parseTagList(urlParam('notags'));
    var showDrafts = urlParam('showDrafts') === '1';
    var draftsRequested = showDrafts ||
      include.indexOf('draft') !== -1 ||
      exclude.indexOf('draft') !== -1;
    if (!draftsRequested) exclude = exclude.concat(['draft']);
    return { include: include, exclude: exclude };
  }

  // ?only=<id> — постоянная ссылка на одно замечание: страница со сканом и
  // единственным выделенным маркером. Это по-прежнему чистая фильтрация уже
  // встроенных данных, ни одного запроса, поэтому инвариант офлайна цел.
  // Черновик по такой ссылке показывается: ссылка адресная, а не обзорная,
  // и просить ?tags=draft вдобавок было бы лишней церемонией.
  function onlyId() {
    var value = urlParam('only');
    return value ? value.trim() : null;
  }

  function applyFilter(remarks) {
    var only = onlyId();
    if (only) {
      return remarks.filter(function (ann) { return String(ann.id) === only; });
    }
    var filter = getFilter();
    return remarks.filter(function (ann) {
      var tags = remarkTags(ann);
      for (var i = 0; i < filter.exclude.length; i++) {
        if (tags.indexOf(filter.exclude[i]) !== -1) return false;
      }
      if (!filter.include.length) return true;
      for (var j = 0; j < filter.include.length; j++) {
        if (tags.indexOf(filter.include[j]) !== -1) return true;
      }
      return false;
    });
  }

  function isDefaultView(shown) {
    // Пре-рендер содержит ровно опубликованные аннотации в исходном порядке.
    // Если фильтр не изменил набор, перерисовывать нечего — не мигаем.
    if (urlParam('tags') || urlParam('notags') || urlParam('showDrafts') ||
        urlParam('only')) return false;
    var published = data.filter(function (a) { return !a.draft; });
    if (published.length !== shown.length) return false;
    for (var i = 0; i < published.length; i++) {
      if (published[i].id !== shown[i].id) return false;
    }
    return true;
  }

  // --- панель ------------------------------------------------------------

  function displayTags(ann) {
    return (ann.tags || []).filter(function (t) {
      // `cat:*` — зеркало поля category, оно уже показано цветом.
      return t !== 'draft' && t.indexOf('confidence:') !== 0 && t.indexOf('cat:') !== 0;
    });
  }

  function renderPanel(shown) {
    var list = document.getElementById('panel-list');
    if (!list) {
      // Страница без опубликованного разбора приходит без списка (там заглушка).
      // Собираем ту же структуру, что и пре-рендер, вместе с <details> —
      // иначе при ?showDrafts=1 на телефоне снова получится стена текста.
      var wrap = document.createElement('details');
      wrap.className = 'panel-list-wrap';
      wrap.id = 'panel-list-wrap';
      wrap.setAttribute('open', '');
      wrap.appendChild(document.createElement('summary'));

      list = document.createElement('ol');
      list.className = 'panel-list';
      list.id = 'panel-list';
      wrap.appendChild(list);

      var empty = panel.querySelector('.panel-empty');
      if (empty) empty.parentNode.replaceChild(wrap, empty);
      else panel.appendChild(wrap);
    }

    list.innerHTML = '';
    shown.forEach(function (ann, index) {
      var item = document.createElement('li');
      item.className = 'panel-item panel-item--' + remarkKind(ann)
        + ' panel-item--cat-' + categorySlug(ann) + (ann.draft ? ' is-draft' : '');
      item.id = 'panel-item-' + ann.id;
      item.setAttribute('data-ann-id', ann.id);

      var num = document.createElement('span');
      num.className = 'panel-item__num';
      num.setAttribute('aria-hidden', 'true');
      num.textContent = String(index + 1);

      var body = document.createElement('div');
      body.className = 'panel-item__body remark-content';
      // html приходит из сборки (scripts/page_html.py), где он собран из
      // markdown с экранированием — не пользовательский ввод.
      body.innerHTML = (ann.draft ? '<em class="draft-tag">[черновик]</em> ' : '') + (ann.html || '');

      item.appendChild(num);
      item.appendChild(body);

      var tags = displayTags(ann);
      if (tags.length) {
        var tagList = document.createElement('ul');
        tagList.className = 'panel-item__tags';
        tags.forEach(function (tag) {
          var li = document.createElement('li');
          li.className = 'panel-item__tag';
          li.textContent = tag;
          tagList.appendChild(li);
        });
        item.appendChild(tagList);
      }

      list.appendChild(item);
    });

    if (!shown.length) {
      var note = document.createElement('p');
      note.className = 'panel-empty';
      note.textContent = 'Ни одно замечание не подходит под выбранный фильтр.';
      list.parentNode.insertBefore(note, list.nextSibling);
    }

    updateCount(shown.length);
    syncListWrap(shown.length);
  }

  /**
   * Список свёрнут только там, где замечание показывает оверлей по тапу, —
   * то есть на тач-устройствах. В разметке <details> приходит открытым, чтобы
   * краулер и читатель без JS видели весь текст; сворачиваем уже здесь.
   *
   * Признак — canHover(), а не ширина: при нулевой ширине (вкладка ещё не
   * отрисована) медиазапрос по ширине ложно срабатывает, и список схлопывался
   * на десктопе.
   */
  function syncListWrap(count) {
    var wrap = document.getElementById('panel-list-wrap');
    if (!wrap) return;
    var summary = wrap.querySelector('summary');
    if (summary) summary.textContent = 'Все замечания (' + count + ')';
    if (canHover()) wrap.setAttribute('open', '');
    else wrap.removeAttribute('open');
  }

  function updateCount(n) {
    var el = document.querySelector('.panel-context__count');
    if (!el) return;
    if (!n) {
      el.textContent = 'Разбор этой страницы ещё не опубликован.';
      return;
    }
    var mod10 = n % 10;
    var mod100 = n % 100;
    var word = 'замечаний';
    if (mod10 === 1 && mod100 !== 11) word = 'замечание';
    else if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) word = 'замечания';
    el.textContent = n + ' ' + word + ' к странице';
  }

  function markActiveChips() {
    var include = parseTagList(urlParam('tags'));
    Array.prototype.forEach.call(document.querySelectorAll('.panel-chip'), function (chip) {
      var tag = (chip.getAttribute('data-tag') || '').toLowerCase();
      chip.classList.toggle('is-active', include.indexOf(tag) !== -1);
      // Повторный клик по активному тегу снимает фильтр.
      if (include.indexOf(tag) !== -1) chip.setAttribute('href', './');
    });
  }

  // --- поп-апы ------------------------------------------------------------
  // Основная механика сайта: наведение на маркер показывает замечание поверх
  // страницы, клик его закрепляет. Логика перенесена из SPA
  // (remark-content.js createRemarkPopup, remarks.js) без её глобалов:
  // разметка замечания уже готова в инлайн-данных, markdown-библиотека не
  // нужна.

  var POPUP_GAP_BELOW = 10;   // зазор под маркером
  var POPUP_GAP_ABOVE = 20;   // и над ним — асимметрия из оригинала
  var POPUP_LEAVE_DELAY = 50; // мс на переход курсора с маркера в поп-ап

  function popupNeedsWidth(ann) {
    // Картинка или длинный текст в поп-апе шириной 300px нечитаемы.
    return /<img\b/i.test(ann.html || '') || (ann.html || '').length > 600;
  }

  function positionPopup(popup, cy, diameter, hasImage) {
    var height = popup.getBoundingClientRect().height;
    var viewportHeight = window.innerHeight;
    var spaceBelow = viewportHeight - (cy + diameter / 2 + POPUP_GAP_BELOW);
    var spaceAbove = cy - diameter / 2 - POPUP_GAP_BELOW;
    var wouldClipTop = (cy - diameter / 2 - height - POPUP_GAP_ABOVE) < 0;

    var below = wouldClipTop ||
      (!hasImage && height <= spaceBelow) ||
      (!hasImage && cy < viewportHeight / 2 && spaceBelow >= spaceAbove);

    if (below) {
      popup.style.top = (cy + diameter / 2 + POPUP_GAP_BELOW) + 'px';
      popup.classList.remove('above');
    } else {
      popup.style.top = (cy - diameter / 2 - height - POPUP_GAP_ABOVE) + 'px';
      popup.style.bottom = 'auto';
      popup.classList.add('above');
    }
  }

  /**
   * Поп-ап живёт в #overlay-container, который спозиционирован абсолютно и не
   * увеличивает высоту документа. Поэтому поп-ап у нижнего края экрана
   * оказывается обрезанным — под него подкладывается распорка.
   *
   * Распорка добавляется ТОЛЬКО в body. Внутри #layout элемент с width:100%
   * схлопывает соседа по флексу, и картинка страницы исчезает — грабли из
   * оригинального кода, воспроизводить их незачем.
   */
  function addSpacer(popup) {
    var rect = popup.getBoundingClientRect();
    var overflow = rect.bottom - window.innerHeight;
    if (overflow <= 0) return;

    var id = 'popup-spacer-' + popup.id;
    var spacer = document.getElementById(id);
    var needed = overflow + 50;
    if (!spacer) {
      spacer = document.createElement('div');
      spacer.id = id;
      spacer.style.width = '100%';
      spacer.style.position = 'relative';
      spacer.style.clear = 'both';
      spacer.style.zIndex = '-1';
      document.body.appendChild(spacer);
    }
    if (parseFloat(spacer.style.height || '0') < needed) spacer.style.height = needed + 'px';
  }

  function removeSpacer(popup) {
    var spacer = document.getElementById('popup-spacer-' + popup.id);
    if (spacer && spacer.parentNode) spacer.parentNode.removeChild(spacer);
  }

  function showPopup(popup) {
    popup.style.display = 'block';
    addSpacer(popup);
  }

  function hidePopup(popup) {
    popup.style.display = 'none';
    removeSpacer(popup);
  }

  function hideAllPopups() {
    Array.prototype.forEach.call(document.querySelectorAll('.remark-popup'), function (popup) {
      popup.dataset.clickShown = 'false';
      popup.dataset.hoverShown = 'false';
      hidePopup(popup);
    });
  }

  function createPopup(ann, index, cx, cy, diameter) {
    var popup = document.createElement('div');
    popup.className = 'remark-popup';
    popup.id = 'popup-' + ann.id;
    popup.innerHTML =
      '<div class="remark-popup__title">Замечание ' + (index + 1) +
      (ann.draft ? ' <span class="draft-tag">[черновик]</span>' : '') + '</div>' +
      '<div class="remark-content">' + (ann.html || '') + '</div>';

    // left + CSS transform: translateX(-50%) центрируют поп-ап на маркере.
    popup.style.left = cx + 'px';
    popup.style.top = (cy + diameter / 2 + POPUP_GAP_BELOW) + 'px';

    var hasImage = /<img\b/i.test(ann.html || '');
    if (popupNeedsWidth(ann) && image && image.width) {
      popup.style.width = (image.width * 0.6) + 'px';
      popup.style.maxWidth = 'none';
    }

    popup.dataset.hoverShown = 'false';
    popup.dataset.clickShown = 'false';
    popup.dataset.popupHover = 'false';

    // Клик внутри поп-апа не должен всплывать до обработчика «закрыть всё»,
    // иначе нельзя ни выделить текст, ни перейти по ссылке.
    popup.addEventListener('click', function (event) { event.stopPropagation(); });
    popup.addEventListener('mouseenter', function () { popup.dataset.popupHover = 'true'; });
    popup.addEventListener('mouseleave', function () {
      popup.dataset.popupHover = 'false';
      if (popup.dataset.hoverShown === 'true' && popup.dataset.clickShown !== 'true') {
        hidePopup(popup);
        popup.dataset.hoverShown = 'false';
      }
    });

    overlay.appendChild(popup);

    // Замер высоты: показать невидимым, спозиционировать, спрятать обратно.
    popup.style.visibility = 'hidden';
    popup.style.display = 'block';
    positionPopup(popup, cy, diameter, hasImage);
    popup.style.display = 'none';
    popup.style.visibility = 'visible';

    // Высота поп-апа с картинкой известна только после её загрузки.
    if (hasImage) {
      Array.prototype.forEach.call(popup.querySelectorAll('img'), function (img) {
        if (img.complete) return;
        img.addEventListener('load', function () {
          var wasVisible = popup.style.display === 'block';
          popup.style.visibility = 'hidden';
          popup.style.display = 'block';
          positionPopup(popup, cy, diameter, hasImage);
          if (!wasVisible) popup.style.display = 'none';
          popup.style.visibility = 'visible';
          if (wasVisible) addSpacer(popup);
        });
      });
    }

    return popup;
  }

  function wirePopupHover(circle, popup) {
    circle.addEventListener('mouseenter', function () {
      if (popup.dataset.clickShown !== 'true') {
        showPopup(popup);
        popup.dataset.hoverShown = 'true';
      }
    });

    circle.addEventListener('mouseleave', function () {
      if (popup.dataset.hoverShown !== 'true' || popup.dataset.clickShown === 'true') return;
      // Между маркером и поп-апом есть зазор: без паузы курсор не успевает
      // до него дойти и поп-ап захлопывается перед носом.
      setTimeout(function () {
        if (popup.dataset.popupHover !== 'true' && popup.dataset.clickShown !== 'true') {
          hidePopup(popup);
          popup.dataset.hoverShown = 'false';
        }
      }, POPUP_LEAVE_DELAY);
    });
  }

  function pinPopup(popup) {
    Array.prototype.forEach.call(document.querySelectorAll('.remark-popup'), function (other) {
      other.dataset.clickShown = 'false';
      if (other !== popup) {
        other.dataset.hoverShown = 'false';
        hidePopup(other);
      }
    });
    showPopup(popup);
    popup.dataset.clickShown = 'true';
  }

  // --- маркеры -----------------------------------------------------------

  function ensureOverlay() {
    var container = document.getElementById('image-container');
    if (!container) return null;
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'overlay-container';
      container.appendChild(overlay);
    }
    return overlay;
  }

  function drawMarkers() {
    if (!image || !image.complete || !image.naturalWidth) return;
    if (!ensureOverlay()) return;
    drawMarkersInner();
    // Поп-апы только что пересозданы — раскрыть заново. Живёт здесь, а не в
    // refresh(), потому что перерисовка случается ещё и по resize и по load.
    openOnlyRemark();
  }

  function drawMarkersInner() {

    var scaleX = image.width / image.naturalWidth;
    var scaleY = image.height / image.naturalHeight;

    overlay.style.width = image.width + 'px';
    overlay.style.height = image.height + 'px';
    overlay.style.top = image.offsetTop + 'px';
    overlay.style.left = image.offsetLeft + 'px';
    // Поп-апы пересоздаются вместе с маркерами, а их распорки лежат в body —
    // без явной уборки они накапливались бы при каждом resize.
    Array.prototype.forEach.call(document.querySelectorAll('[id^="popup-spacer-"]'), function (el) {
      el.parentNode.removeChild(el);
    });
    overlay.innerHTML = '';

    visible.forEach(function (ann, index) {
      if (!Array.isArray(ann.coords) || ann.coords.length !== 2) return;

      var type = remarkKind(ann);
      // Диаметр задан в координатах исходной страницы и масштабируется вместе
      // с картинкой — иначе на телефоне диск занимает весь экран.
      var diameter = (MARKER_SIZES[type] || MARKER_SIZES.minor) * (scaleX || 1);
      var cx = ann.coords[0] * scaleX;
      var cy = ann.coords[1] * scaleY;
      var color = markerColor(ann);

      var circle = document.createElement('div');
      circle.className = 'circle circle--cat-' + categorySlug(ann) + (ann.draft ? ' is-draft' : '');
      circle.id = 'circle-' + ann.id;
      circle.setAttribute('data-ann-id', ann.id);
      circle.style.width = diameter + 'px';
      circle.style.height = diameter + 'px';
      circle.style.left = cx + 'px';
      circle.style.top = (cy - diameter / 2) + 'px';
      circle.style.fontSize = (diameter * 0.6) + 'px';
      circle.style.background = 'radial-gradient(circle, ' + color + '80 0%, ' + color + '40 50%, ' + color + '00 100%)';
      circle.style.transform = 'translateX(-50%)';
      if (ann.draft) circle.style.outline = '2px dashed #888';
      circle.textContent = String(index + 1);

      circle.addEventListener('mouseenter', function () { highlight(ann.id, false); });
      circle.addEventListener('mouseleave', function () { if (pinnedId !== ann.id) clearHighlight(); });

      overlay.appendChild(circle);

      if (canHover()) {
        var popup = createPopup(ann, index, cx, cy, diameter);
        circle.dataset.popupId = popup.id;
        wirePopupHover(circle, popup);
        circle.addEventListener('click', function (event) {
          event.stopPropagation();
          pinPopup(popup);
          selectRemark(ann.id, null);
        });
      } else {
        circle.addEventListener('click', function (event) {
          event.stopPropagation();
          showMobileComment(index, ann);
        });
      }
    });
  }

  // --- синхронизация списка и маркеров ------------------------------------

  function highlight(remarkId, scrollList) {
    clearHighlight();
    var circle = document.getElementById('circle-' + remarkId);
    var item = document.getElementById('panel-item-' + remarkId);
    if (circle) circle.classList.add('is-highlighted');
    if (item) {
      item.classList.add('is-highlighted');
      if (scrollList) item.scrollIntoView({ block: 'nearest' });
    }
  }

  function clearHighlight() {
    Array.prototype.forEach.call(
      document.querySelectorAll('.circle.is-highlighted, .panel-item.is-highlighted'),
      function (el) { el.classList.remove('is-highlighted'); }
    );
  }

  /**
   * scroll: куда разрешено прокручивать. 'image' — клик по пункту списка ведёт
   * к маркеру; 'list' — наоборот. Клик по маркеру не прокручивает никуда:
   * список теперь под страницей, и прокрутка к нему уводила бы читателя от
   * скана прямо в момент, когда он открыл поп-ап.
   */
  function selectRemark(remarkId, scroll) {
    pinnedId = remarkId;
    Array.prototype.forEach.call(document.querySelectorAll('.panel-item.is-active'), function (el) {
      el.classList.remove('is-active');
    });
    var item = document.getElementById('panel-item-' + remarkId);
    if (item) {
      item.classList.add('is-active');
      if (scroll === 'list') item.scrollIntoView({ block: 'nearest' });
    }
    var circle = document.getElementById('circle-' + remarkId);
    if (circle && scroll === 'image') circle.scrollIntoView({ block: 'center', behavior: 'smooth' });
    highlight(remarkId, false);
  }

  function wirePanel() {
    if (!panel) return;
    panel.addEventListener('mouseover', function (event) {
      var item = event.target.closest ? event.target.closest('.panel-item') : null;
      if (item && canHover()) highlight(item.getAttribute('data-ann-id'), false);
    });
    panel.addEventListener('mouseout', function (event) {
      var item = event.target.closest ? event.target.closest('.panel-item') : null;
      if (item && !pinnedId) clearHighlight();
    });
    panel.addEventListener('click', function (event) {
      if (event.target.closest('a')) return;   // ссылки в тексте и чипы тегов
      var item = event.target.closest ? event.target.closest('.panel-item') : null;
      if (item) selectRemark(item.getAttribute('data-ann-id'), 'image');
    });
  }

  // --- мобильный оверлей --------------------------------------------------

  function showMobileComment(index, ann) {
    var overlayEl = document.getElementById('mobile-overlay');
    var content = document.getElementById('mobile-remark-body');
    if (!overlayEl || !content) return;
    content.innerHTML = '<h3>Замечание ' + (index + 1) + '</h3>' +
      (ann.draft ? '<em class="draft-tag">[черновик]</em> ' : '') + (ann.html || '');
    overlayEl.style.display = 'block';
    selectRemark(ann.id, null);
  }

  function closeMobileOverlay() {
    var overlayEl = document.getElementById('mobile-overlay');
    if (overlayEl) overlayEl.style.display = 'none';
  }

  function wireMobileOverlay() {
    var close = document.getElementById('mobile-overlay-close');
    if (close) {
      close.addEventListener('click', function (event) {
        event.stopPropagation();
        closeMobileOverlay();
      });
    }
    document.addEventListener('click', function (event) {
      var overlayEl = document.getElementById('mobile-overlay');
      if (!overlayEl || overlayEl.style.display !== 'block') return;
      if (!overlayEl.contains(event.target) && !event.target.closest('.circle')) closeMobileOverlay();
    });
  }

  // --- запуск -------------------------------------------------------------

  // По ссылке на одно замечание оно должно быть сразу раскрыто: кружок без
  // текста — не ответ на «покажи мне вот это замечание». Работает и как
  // постоянная ссылка для читателя, и как окно предпросмотра в редакторе.
  function openOnlyRemark() {
    var only = onlyId();
    if (!only) return;
    var ann = null;
    for (var i = 0; i < visible.length; i++) {
      if (String(visible[i].id) === only) { ann = visible[i]; break; }
    }
    if (!ann) return;
    // Здесь условие — ширина, а не canHover(): поп-апы скрыты правилом CSS
    // `@media (max-width: 767px)`, и на узком экране (в том числе в окне
    // предпросмотра редактора) раскрытый поп-ап был бы невидим. Клики
    // по-прежнему различают режимы по canHover() — это осознанное решение
    // просмотрщика, его не трогаем.
    if (window.matchMedia('(max-width: 767px)').matches) {
      showMobileComment(0, ann);
    } else {
      var popup = document.getElementById('popup-' + ann.id);
      if (popup) pinPopup(popup);
    }
    // Прокручиваем к маркеру: по ссылке на одно замечание оно может быть в
    // самом низу длинной страницы, и без этого читатель увидит пустой скан.
    selectRemark(ann.id, 'image');
  }

  function refresh() {
    visible = applyFilter(data);
    if (!isDefaultView(visible)) renderPanel(visible);
    else syncListWrap(visible.length);
    markActiveChips();
    drawMarkers();
  }

  function init() {
    data = readPageData();
    image = document.getElementById('page-image');
    panel = document.getElementById('page-panel');

    wirePanel();
    wireMobileOverlay();
    refresh();

    if (image) {
      if (!image.complete) image.addEventListener('load', drawMarkers);
      window.addEventListener('resize', function () {
        drawMarkers();
        syncListWrap(visible.length);
      });
      // Пересчёт после того, как раскладка устоялась: до этого img.width
      // может быть нулевой и маркеры схлопнутся в точку. Поп-апы при этом
      // пересоздаются, поэтому раскрытие по ?only= надо повторить.
      setTimeout(drawMarkers, 100);
    }

    // Клик мимо маркера, поп-апа и списка — снять закрепление и всё закрыть.
    // Клики внутри поп-апа до сюда не доходят: он их не пропускает.
    document.addEventListener('click', function (event) {
      if (event.target.closest('.circle') || event.target.closest('.panel-item')) return;
      hideAllPopups();
      pinnedId = null;
      clearHighlight();
      Array.prototype.forEach.call(document.querySelectorAll('.panel-item.is-active'), function (el) {
        el.classList.remove('is-active');
      });
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
