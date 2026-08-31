/**
 * redpen-config.js — куда идти за API и под каким Google client id входить.
 *
 * Один файл вместо трёх копий одного и того же скрипта, разъехавшихся по
 * точкам входа (`/app/`, `/cabinet/`, `/survey/`): на localhost адрес API
 * должен указывать на локальный стенд, иначе стенд молча правит боевые данные,
 * — и забыть эту оговорку в новой точке входа было бы слишком легко.
 *
 * Просмотрщик этот файл не подключает и подключать не должен: он не ходит в
 * сеть вовсе (главный инвариант проекта, docs/README.md).
 *
 * Подключать ДО redpen-auth.js: тот читает REDPEN_API_BASE на первом же
 * запросе. Значения, выставленные страницей заранее, не перетираются — так
 * тесты и локальные стенды могут подставить своё.
 */
(function () {
  'use strict';

  if (!window.REDPEN_API_BASE) {
    var host = window.location.hostname;
    if (host === 'localhost' || host === '127.0.0.1') {
      // Порт задаётся ?api=<порт>, по умолчанию 8080.
      var port = new URLSearchParams(window.location.search).get('api') || '8080';
      window.REDPEN_API_BASE = 'http://' + host + ':' + port;
    } else {
      window.REDPEN_API_BASE = 'https://api.medinsky.net';
    }
  }

  window.REDPEN_GOOGLE_CLIENT_ID = window.REDPEN_GOOGLE_CLIENT_ID ||
    '679121872329-tl9fk0aq6i1rjf6jgdb1460p0pvn50md.apps.googleusercontent.com';
})();
