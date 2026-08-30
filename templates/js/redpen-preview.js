/**
 * Предпросмотр замечания: та же страница, которую видит читатель.
 *
 * Единичное замечание нигде не рисуется заново. Показать его — значит открыть
 * во фрейме обычную читательскую страницу с `?only=<id>`: там уже есть и
 * маркер нужного цвета, и скан, и разметка текста, и оно гарантированно
 * выглядит ровно так, как увидит читатель. Второй реализации не будет.
 *
 * Модуль появился 2026-08-31, когда такой фрейм понадобился третьему
 * потребителю (опроснику `/survey/`) — до этого две копии жили в `/app/`.
 *
 * Как и `redpen-markers.js`, к API этот файл не ходит: `metadata.json` —
 * статический файл сайта, читательский офлайн-инвариант им не нарушается.
 */
(function () {
  'use strict';

  //: Ширина, в которую рисуется просмотр. Больше брейкпоинта просмотрщика
  //: (767px), иначе во фрейме показывался бы мобильный вид.
  var PREVIEW_WIDTH = 1200;

  //: docId -> {page_006: "6"}. Манифест читается один раз на документ.
  var manifest = {};

  /**
   * Читательский номер страницы по файловому ключу: "006" -> "6", "-01" -> "A1".
   * Нумерация для читателя задаётся манифестом, а не арифметикой; арифметика
   * остаётся запасным вариантом, если манифест не прочитался.
   */
  async function pageLabel(docId, pageKey, base) {
    var prefix = base == null ? '../' : base;
    if (!manifest[docId]) {
      try {
        var res = await fetch(prefix + encodeURIComponent(docId) + '/metadata.json');
        var meta = await res.json();
        var map = {};
        (meta.pages || []).forEach(function (p) { map[p.file] = String(p.label); });
        manifest[docId] = map;
      } catch (e) {
        manifest[docId] = {};
      }
    }
    return manifest[docId]['page_' + pageKey] || String(parseInt(pageKey, 10) || pageKey);
  }

  /** Адрес читательской страницы с раскрытым замечанием. */
  function remarkUrl(docId, label, remarkId, base) {
    var prefix = base == null ? '../' : base;
    return prefix + encodeURIComponent(docId) + '/pages/' +
           encodeURIComponent(label) + '/?only=' + encodeURIComponent(remarkId);
  }

  //: Ниже этой ширины сжатая десктопная страница нечитаема: на телефоне
  //: масштаб выходит около трети, и текст замечания разобрать нельзя.
  var NATIVE_BELOW = 700;

  /**
   * Вписать фрейм в отведённое место масштабированием.
   *
   * Фрейм живёт в своих координатах (PREVIEW_WIDTH), а на экране занимает
   * столько, сколько дали: сжимать страницу шириной контейнера значило бы
   * показывать мобильную вёрстку вместо той, о которой идёт речь.
   *
   * `opts.native` переворачивает это правило для узких экранов. Редактору
   * нужен вид большинства читателей, каким бы ни был его собственный экран, —
   * там правило остаётся. Опроснику наоборот: респондент с телефона сам и
   * есть читатель, и показывать ему сжатую втрое десктопную вёрстку вместо
   * мобильной, сделанной ровно для этого случая, значит не показать ничего.
   */
  function fitFrame(fit, frame, opts) {
    if (typeof fit === 'string') fit = document.getElementById(fit);
    if (typeof frame === 'string') frame = document.getElementById(frame);
    if (!fit || !frame) return;
    var available = fit.clientWidth;
    if (!available) return;
    if (opts && opts.native && available < NATIVE_BELOW) {
      frame.style.setProperty('--preview-width', available + 'px');
      frame.style.setProperty('--preview-height', fit.clientHeight + 'px');
      frame.style.transform = 'none';
      return;
    }
    var scale = Math.min(1, available / PREVIEW_WIDTH);
    frame.style.setProperty('--preview-width', PREVIEW_WIDTH + 'px');
    // Высота в собственных координатах фрейма: то, что после сжатия займёт всю
    // панель. Без этого низ страницы обрезался бы.
    frame.style.setProperty('--preview-height', Math.round(fit.clientHeight / scale) + 'px');
    frame.style.transform = 'scale(' + scale + ')';
  }

  window.RedPenPreview = {
    PREVIEW_WIDTH: PREVIEW_WIDTH,
    NATIVE_BELOW: NATIVE_BELOW,
    pageLabel: pageLabel,
    remarkUrl: remarkUrl,
    fitFrame: fitFrame
  };
})();
