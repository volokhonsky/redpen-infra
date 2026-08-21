(function(){
  // Офлайн-копия разбора (см. docs/offline-bundle-plan.md).
  //
  // На сайте этот файл — no-op: window.REDPEN_OFFLINE определяет только
  // offline-data.js, который кладёт в архив scripts/make_offline_bundle.py.
  //
  // Зачем вообще: у страницы, открытой как file://, origin равен null, и
  // браузер блокирует fetch к соседним файлам. Просмотрщик грузит fetch'ем
  // ровно три вида данных (metadata.json, annotations/<page>.json,
  // text/<page>.json) — их и подменяем, чтобы main.js и annotations.js не
  // пришлось трогать. Картинки идут через <img src> и на file:// работают.

  var data = window.REDPEN_OFFLINE;
  if (!data || typeof data !== 'object') return;

  var metadata = data.metadata || null;
  var annotations = data.annotations || {};
  var text = data.text || {};

  // Ответ-утка вместо настоящего Response: вызывающий код использует только
  // ok/status/json()/text(), а конструктор Response под file:// в старых
  // движках вёл себя по-разному.
  function jsonResponse(value){
    var body = JSON.stringify(value);
    return {
      ok: true,
      status: 200,
      statusText: 'OK',
      headers: { get: function(name){
        return String(name).toLowerCase() === 'content-type' ? 'application/json' : null;
      } },
      json: function(){ return Promise.resolve(JSON.parse(body)); },
      text: function(){ return Promise.resolve(body); }
    };
  }

  function notFound(){
    return {
      ok: false,
      status: 404,
      statusText: 'Not Found (offline bundle)',
      headers: { get: function(){ return null; } },
      json: function(){ return Promise.reject(new Error('offline_not_found')); },
      text: function(){ return Promise.resolve(''); }
    };
  }

  // Путь без query/hash. Относительные адреса (metadata.json,
  // annotations/page_007.json) остаются как есть.
  function pathOf(url){
    return String(url).split('#')[0].split('?')[0];
  }

  var RE_ANNOTATIONS = /(?:^|\/)annotations\/([^\/]+)\.json$/;
  var RE_TEXT = /(?:^|\/)text\/([^\/]+)\.json$/;
  var RE_METADATA = /(?:^|\/)metadata\.json$/;

  // undefined — «не наш адрес, пропускаем дальше»; null — «наш, но данных нет»
  // (например легаси-компаньон page_007.drafts.json: черновики давно лежат в
  // основном файле, и запрос должен получить честный 404, а не сетевую ошибку).
  function lookup(url){
    var path = pathOf(url);
    var m;
    if (RE_METADATA.test(path)) return metadata || null;
    if ((m = RE_ANNOTATIONS.exec(path))) {
      return Object.prototype.hasOwnProperty.call(annotations, m[1]) ? annotations[m[1]] : null;
    }
    if ((m = RE_TEXT.exec(path))) {
      return Object.prototype.hasOwnProperty.call(text, m[1]) ? text[m[1]] : null;
    }
    return undefined;
  }

  var originalFetch = typeof window.fetch === 'function' ? window.fetch.bind(window) : null;

  window.fetch = function(input, init){
    var url = typeof input === 'string' ? input : (input && input.url) || '';
    var hit;
    try {
      hit = lookup(url);
    } catch (e) {
      hit = undefined;
    }
    if (hit !== undefined) {
      return Promise.resolve(hit === null ? notFound() : jsonResponse(hit));
    }
    if (originalFetch) return originalFetch(input, init);
    return Promise.reject(new Error('fetch_unavailable_offline'));
  };

  window.REDPEN_OFFLINE_ACTIVE = true;
})();
