/**
 * redpen-api.js — один клиент API на все рабочие интерфейсы.
 *
 * До слияния /app/ и /cabinet/ обёртки жили в двух копиях и успели разойтись:
 * в редакторе был тихий повтор на 403 (токен CSRF перевыписывается на каждый
 * GET /api/auth/csrf, поэтому две открытые вкладки гасят друг друга) и
 * человеческое сообщение на 409, в кабинете — нет. Взята редакторская версия
 * как более полная.
 *
 * Просмотрщик этот файл не подключает: он не ходит в сеть вовсе.
 *
 * Реакции на «не вошли» и на ошибку задаёт хозяин страницы — модуль не знает,
 * как выглядит её экран входа и её строка состояния:
 *
 *   RedPenApi.configure({ onUnauthorized: showLogin, onError: setStatus });
 */
(function () {
  'use strict';

  var handlers = { onUnauthorized: function () {}, onError: function () {} };

  function configure(opts) {
    if (!opts) return;
    if (opts.onUnauthorized) handlers.onUnauthorized = opts.onUnauthorized;
    if (opts.onError) handlers.onError = opts.onError;
  }

  function url(path) {
    return (window.REDPEN_API_BASE || '') + path;
  }

  async function get(path) {
    var res = await fetch(url(path), { credentials: 'include' });
    if (res.status === 401) { handlers.onUnauthorized(); throw new Error('unauthorized'); }
    if (!res.ok) {
      handlers.onError('Ошибка запроса: ' + res.status);
      throw new Error('http ' + res.status);
    }
    return res.json();
  }

  async function mutate(method, path, body, isRetry) {
    var csrf = await window.RedPenAuth.getCsrf(!!isRetry);
    var res = await fetch(url(path), {
      method: method,
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf.csrfToken },
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'include'
    });
    // Один тихий повтор: см. про две вкладки в шапке файла.
    if (res.status === 403 && !isRetry) return mutate(method, path, body, true);
    if (res.status === 401) { handlers.onUnauthorized(); throw new Error('unauthorized'); }
    if (res.status === 409) {
      handlers.onError('Страницу успели изменить в другом месте. Нажмите «Перечитать».');
      throw new Error('conflict');
    }
    if (!res.ok) {
      var detail = '';
      try { detail = (await res.json()).detail || ''; } catch (e) {}
      handlers.onError('Не сохранено: ' + (detail || res.status));
      throw new Error('http ' + res.status);
    }
    return res.json();
  }

  window.RedPenApi = { configure: configure, url: url, get: get, mutate: mutate };
})();
