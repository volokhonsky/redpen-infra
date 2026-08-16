/**
 * Переезд со старых адресов просмотрщика на постраничные.
 *
 * Раньше вся книга жила по одному адресу с параметром: <doc>/?p=157,
 * легаси <doc>/?page=157 и якорь #page157. Теперь у страницы свой адрес
 * <doc>/pages/157/. Оглавление подхватывает старый параметр и уводит на него,
 * чтобы внешние ссылки и закладки не ломались.
 *
 * Замена в истории (replace), а не переход: старый адрес не должен оставаться
 * в истории браузера и не должен конкурировать с новым за индексацию.
 */
(function () {
  'use strict';

  var params = new URLSearchParams(window.location.search);
  // Редактору старый просмотрщик по-прежнему нужен — не уводим его.
  if (params.get('editor')) return;

  var label = params.get('p') || params.get('page');
  if (!label) {
    var hash = /^#page(.+)$/.exec(window.location.hash || '');
    if (hash) label = hash[1];
  }
  if (!label) return;

  label = String(label).trim();
  if (!/^[A-Za-z0-9-]+$/.test(label)) return;

  // Фильтры показа тегов переносим на новый адрес.
  var carried = new URLSearchParams();
  ['tags', 'notags', 'showDrafts'].forEach(function (key) {
    if (params.get(key)) carried.set(key, params.get(key));
  });
  var query = carried.toString();

  window.location.replace('pages/' + encodeURIComponent(label) + '/index.html' + (query ? '?' + query : ''));
})();
