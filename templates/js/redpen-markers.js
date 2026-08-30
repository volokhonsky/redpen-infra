/**
 * Маркеры замечаний: геометрия, размер, цвет.
 *
 * Один модуль на всех, кто рисует кружки поверх скана страницы: просмотрщик
 * (page-view.js) и редактор (/app/). До него отрисовка существовала в четырёх
 * экземплярах, которые успели разойтись — просмотрщик красил по категории, а
 * редактор по виду замечания, и одна и та же страница выглядела по-разному в
 * зависимости от того, кто на неё смотрит.
 *
 * ПРАВИЛО ФАЙЛА: здесь не должно появиться ни fetch(), ни XMLHttpRequest, ни
 * обращения к API. Модуль подключается просмотрщиком, а тот обязан работать с
 * флешки и офлайн (docs/README.md, «Ключевое ограничение»); на это есть тест.
 */
(function (global) {
  'use strict';

  // Размер задаётся видом замечания, цвет — категорией приёма
  // (redpen-categories.js). Привычка «красный = главное» на этих страницах не
  // действует: легенда на титульной переписана под категории.
  var SIZES = { major: 90, minor: 50, small: 25 };
  var FALLBACK_COLOR = '#546E7A';

  /**
   * Вид замечания: major (крупный маркер) или minor (обычный).
   *
   * Читает и новый ключ `kind`, и старый `annType` со старыми значениями.
   * Второй ключ убран из блока страницы в фазе 6 переименования (2026-08-30),
   * но эта ветка остаётся навсегда: страницы перерисовываются по одной, а уже
   * розданные офлайн-копии не перерисуются никогда — и в них лежат старые
   * имена. Упразднённый `general` показываем как обычное замечание.
   */
  function kindOf(ann) {
    var k = (ann && (ann.kind || ann.annType)) || 'minor';
    if (k === 'main') return 'major';
    if (k === 'comment' || k === 'general') return 'minor';
    return k;
  }

  function categoryOf(ann) {
    var cats = global.RedPenCategories;
    return cats ? cats.categoryFor(ann) : 'other';
  }

  function colorOf(ann) {
    var cats = global.RedPenCategories;
    if (!cats) return FALLBACK_COLOR;
    return cats.COLORS[cats.categoryFor(ann)] || FALLBACK_COLOR;
  }

  /** Во сколько раз показанная картинка меньше исходной страницы. */
  function scaleOf(image) {
    return {
      x: image.width / image.naturalWidth,
      y: image.height / image.naturalHeight
    };
  }

  /** Наложить оверлей ровно на картинку. */
  function fitOverlay(overlay, image) {
    overlay.style.width = image.width + 'px';
    overlay.style.height = image.height + 'px';
    overlay.style.top = image.offsetTop + 'px';
    overlay.style.left = image.offsetLeft + 'px';
  }

  /**
   * Экранная геометрия одного маркера при данном масштабе.
   * cx/cy — центр по горизонтали и вертикали в пикселях показанной картинки.
   */
  function geometry(ann, scale) {
    // Диаметр задан в координатах исходной страницы и масштабируется вместе с
    // картинкой — иначе на телефоне диск занимает весь экран.
    var diameter = (SIZES[kindOf(ann)] || SIZES.minor) * (scale.x || 1);
    return {
      diameter: diameter,
      cx: ann.coords[0] * scale.x,
      cy: ann.coords[1] * scale.y
    };
  }

  /** Есть ли у замечания пригодные координаты. */
  function hasCoords(ann) {
    return !!ann && Array.isArray(ann.coords) && ann.coords.length === 2;
  }

  /**
   * Кружок замечания. `label` — что написать внутри (обычно порядковый номер).
   * Обработчики вешает вызывающий: они у просмотрщика и редактора разные.
   */
  function createCircle(ann, label, scale) {
    var g = geometry(ann, scale);
    var color = colorOf(ann);

    var circle = document.createElement('div');
    circle.className = 'circle circle--cat-' + categoryOf(ann) + (ann.draft ? ' is-draft' : '');
    circle.id = 'circle-' + ann.id;
    circle.setAttribute('data-ann-id', ann.id);
    circle.style.width = g.diameter + 'px';
    circle.style.height = g.diameter + 'px';
    circle.style.left = g.cx + 'px';
    circle.style.top = (g.cy - g.diameter / 2) + 'px';
    circle.style.fontSize = (g.diameter * 0.6) + 'px';
    circle.style.background = 'radial-gradient(circle, ' + color + '80 0%, ' + color + '40 50%, ' + color + '00 100%)';
    circle.style.transform = 'translateX(-50%)';
    if (ann.draft) circle.style.outline = '2px dashed #888';
    circle.textContent = String(label);
    return circle;
  }

  /**
   * Точка клика по картинке → координаты в системе исходной страницы.
   * Именно они хранятся в `coords`: страницу показывают в разных масштабах, а
   * замечание должно стоять на том же месте текста.
   */
  function pointToCoords(image, clientX, clientY) {
    var rect = image.getBoundingClientRect();
    var scale = scaleOf(image);
    if (!scale.x || !scale.y) return null;
    return [
      Math.round((clientX - rect.left) / scale.x),
      Math.round((clientY - rect.top) / scale.y)
    ];
  }

  global.RedPenMarkers = {
    SIZES: SIZES,
    FALLBACK_COLOR: FALLBACK_COLOR,
    kindOf: kindOf,
    categoryOf: categoryOf,
    colorOf: colorOf,
    scaleOf: scaleOf,
    fitOverlay: fitOverlay,
    geometry: geometry,
    hasCoords: hasCoords,
    createCircle: createCircle,
    pointToCoords: pointToCoords
  };
})(window);
