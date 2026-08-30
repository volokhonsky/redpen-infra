(function () {
  'use strict';

  /**
   * Опросник: оценка замечаний людьми вне закрытого круга.
   *
   * Адрес: /survey/
   *
   * Устроен как редактор и кабинет: свой html/js/css, все данные из API,
   * просмотрщик о нём не знает. Замечание не рисуется здесь заново — оно
   * показывается во фрейме обычной читательской страницы с `?only=<id>`
   * (общий модуль redpen-preview.js). Из-за этого опрос не заводит второго
   * пути к тексту замечания и ничего не знает о геометрии маркеров.
   *
   * Респондент опознаётся токеном в заголовке `X-Survey-Token`, а не кукой:
   * кука потребовала бы CSRF-защиты, а токен, недоступный чужому сайту,
   * снимает вопрос целиком. Токен живёт в sessionStorage — закрыли вкладку,
   * и опроса больше нет. Это осознанно: возвращаться некуда, а хранить
   * опознание человека дольше нужного не за чем (docs/anonymity-model.md).
   */

  var preview = window.RedPenPreview;
  var TOKEN_KEY = 'redpen_survey_token';

  var state = {
    token: null,
    author: '',
    scales: [],
    items: [],      // текущая порция
    index: 0,
    answers: {},    // scale -> value для показанной карточки
    answered: 0,    // сколько замечаний оценено за этот заход
    remaining: 0
  };

  // --- мелочи -------------------------------------------------------------

  function el(id) { return document.getElementById(id); }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function setStatus(text, isError) {
    var host = el('sv-status');
    host.textContent = text || '';
    host.hidden = !text;
    host.classList.toggle('is-error', !!isError);
  }

  var SCREENS = ['sv-screen-name', 'sv-screen-intro', 'sv-screen-card', 'sv-screen-done'];

  function show(id) {
    SCREENS.forEach(function (name) { el(name).hidden = name !== id; });
    setStatus('');
    window.scrollTo(0, 0);
  }

  function readToken() {
    try { return window.sessionStorage.getItem(TOKEN_KEY); } catch (e) { return null; }
  }

  function writeToken(token) {
    try { window.sessionStorage.setItem(TOKEN_KEY, token); } catch (e) { /* приватный режим */ }
  }

  function dropToken() {
    try { window.sessionStorage.removeItem(TOKEN_KEY); } catch (e) { /* см. выше */ }
  }

  // --- обращения к API ----------------------------------------------------

  function apiBase(path) {
    var base = window.REDPEN_API_BASE || 'https://api.medinsky.net';
    return base.replace(/\/$/, '') + path;
  }

  async function api(method, path, body) {
    var options = { method: method, headers: {} };
    if (state.token) options.headers['X-Survey-Token'] = state.token;
    if (body !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    var response;
    try {
      response = await fetch(apiBase(path), options);
    } catch (e) {
      throw new Error('Нет связи с сервером. Проверьте соединение.');
    }
    var data = null;
    try { data = await response.json(); } catch (e) { /* пустой ответ — тоже ответ */ }
    if (response.status === 401) {
      // Сессия опроса кончилась (или её никогда не было): начинаем заново, а
      // не показываем непонятную ошибку.
      dropToken();
      state.token = null;
      show('sv-screen-name');
      throw new Error('Опрос начат заново: сессия не найдена.');
    }
    if (!response.ok) {
      throw new Error((data && data.detail) || ('Ошибка ' + response.status));
    }
    return data;
  }

  // --- 1. псевдоним -------------------------------------------------------

  function updateNamePreview() {
    var value = el('sv-name-input').value.trim();
    el('sv-name-preview').textContent = 'anonymous:' + (value || '…');
  }

  async function startSession(pseudonym) {
    var data = await api('POST', '/api/survey/session', { pseudonym: pseudonym });
    state.token = data.token;
    state.author = data.author;
    state.scales = data.scales || [];
    writeToken(data.token);
    renderIntro();
    show('sv-screen-intro');
  }

  // --- 2. инструкция ------------------------------------------------------

  function renderIntro() {
    el('sv-intro-scales').innerHTML = state.scales.map(function (scale) {
      return '<li><b>' + escapeHtml(scale.title) + '.</b> ' + escapeHtml(scale.hint) + '</li>';
    }).join('');
  }

  // --- 3. карточка --------------------------------------------------------

  function renderScales() {
    el('sv-scales').innerHTML = state.scales.map(function (scale) {
      // Подписи вместо цифр там, где шкала — вопрос о решении, а не о мере.
      var choices = scale.options;
      if (!choices) {
        choices = [];
        for (var v = scale.min; v <= scale.max; v++) choices.push({ value: v, label: String(v) });
      }
      var buttons = choices.map(function (choice) {
        return '<button type="button" class="sv-choice' +
          (state.answers[scale.name] === choice.value ? ' is-picked' : '') +
          '" data-scale="' + escapeHtml(scale.name) + '" data-value="' + choice.value + '">' +
          escapeHtml(choice.label) + '</button>';
      }).join('');
      var ends = scale.options ? '' :
        '<div class="sv-scale-ends"><span>совсем нет</span><span>да, вполне</span></div>';
      return '<div class="sv-scale">' +
        '<div class="sv-scale-title">' + escapeHtml(scale.title) + '</div>' +
        '<div class="sv-scale-hint">' + escapeHtml(scale.hint) + '</div>' +
        '<div class="sv-scale-row">' + buttons + '</div>' + ends +
      '</div>';
    }).join('');
  }

  async function renderCard() {
    var item = state.items[state.index];
    if (!item) { finish(); return; }
    state.answers = {};
    renderScales();
    el('sv-progress-text').textContent =
      'Замечание ' + (state.index + 1) + ' из ' + state.items.length;

    var label = await preview.pageLabel(item.docId, item.pageKey || item.pageNum);
    var url = preview.remarkUrl(item.docId, label, item.remarkId);
    el('sv-preview').src = url;
    el('sv-open-link').href = url;
    preview.fitFrame('sv-preview-fit', 'sv-preview', { native: true });
  }

  async function submitAndAdvance(save) {
    var item = state.items[state.index];
    if (!item) return;
    if (save && Object.keys(state.answers).length) {
      var payload = {
        docId: item.docId,
        pageKey: item.pageKey || item.pageNum,
        remarkId: item.remarkId
      };
      state.scales.forEach(function (scale) {
        if (state.answers[scale.name] != null) payload[scale.name] = state.answers[scale.name];
      });
      await api('PUT', '/api/survey/ratings', payload);
      state.answered += 1;
    }
    state.index += 1;
    await renderCard();
  }

  //: «1 замечание», «2 замечания», «5 замечаний». Библиотеки ради одного
  //: слова не нужно, а «2 замечаний» на финальном экране читается как небрежность.
  function plural(n, one, few, many) {
    var mod100 = n % 100;
    if (mod100 >= 11 && mod100 <= 14) return many;
    var mod10 = n % 10;
    if (mod10 === 1) return one;
    if (mod10 >= 2 && mod10 <= 4) return few;
    return many;
  }

  function finish() {
    el('sv-done-text').textContent = state.answered
      ? 'Вы оценили ' + state.answered + ' ' +
        plural(state.answered, 'замечание', 'замечания', 'замечаний') + '. Это уже помогло.'
      : 'В этот раз вы ничего не оценили — тоже ответ.';
    // «Ещё» показывается, только если есть что показать: кнопка, ведущая в
    // пустой экран, — обман. Остаток считается от того, что было на входе,
    // за вычетом отвеченного: заново спрашивать сервер ради одной кнопки
    // не стоит, а пропущенные так и остаются в остатке — это верно.
    el('sv-more').hidden = (state.remaining - state.answered) <= 0;
    show('sv-screen-done');
  }

  async function loadBatch() {
    var data = await api('GET', '/api/survey/batch');
    state.items = data.items || [];
    state.remaining = data.remaining || 0;
    state.index = 0;
    state.answered = 0;
    state.author = data.author || state.author;
    if (!state.items.length) { finish(); return; }
    show('sv-screen-card');
    await renderCard();
  }

  // --- запуск -------------------------------------------------------------

  function wire() {
    el('sv-name-input').addEventListener('input', updateNamePreview);
    el('sv-name-form').addEventListener('submit', function (event) {
      event.preventDefault();
      el('sv-name-error').textContent = '';
      startSession(el('sv-name-input').value).catch(function (e) {
        el('sv-name-error').textContent = e.message;
      });
    });
    el('sv-intro-start').addEventListener('click', function () {
      loadBatch().catch(function (e) { setStatus(e.message, true); });
    });
    el('sv-scales').addEventListener('click', function (event) {
      var target = event.target;
      if (!target.classList.contains('sv-choice')) return;
      var scale = target.dataset.scale;
      var value = parseInt(target.dataset.value, 10);
      // Повторное нажатие снимает ответ: передумать проще, чем начинать заново.
      state.answers[scale] = state.answers[scale] === value ? null : value;
      if (state.answers[scale] == null) delete state.answers[scale];
      renderScales();
    });
    el('sv-next').addEventListener('click', function () {
      submitAndAdvance(true).catch(function (e) { setStatus(e.message, true); });
    });
    el('sv-skip').addEventListener('click', function () {
      submitAndAdvance(false).catch(function (e) { setStatus(e.message, true); });
    });
    el('sv-more').addEventListener('click', function () {
      loadBatch().catch(function (e) { setStatus(e.message, true); });
    });
    window.addEventListener('resize', function () {
      preview.fitFrame('sv-preview-fit', 'sv-preview', { native: true });
    });
    el('sv-preview').addEventListener('load', function () {
      // Масштаб считается по размеру панели, а она к моменту первой отрисовки
      // может быть ещё не разложена.
      preview.fitFrame('sv-preview-fit', 'sv-preview', { native: true });
    });
  }

  function init() {
    wire();
    updateNamePreview();
    state.token = readToken();
    if (!state.token) { show('sv-screen-name'); return; }
    // Токен есть — вкладку перезагрузили посреди опроса. Словарь шкал приходит
    // вместе с сессией, а её уже не создать: спрашиваем порцию, и если токен
    // ещё жив, api() вернёт данные, иначе сам отправит на первый экран.
    api('GET', '/api/survey/batch').then(function (data) {
      state.scales = data.scales || [];
      if (!state.scales.length) { dropToken(); state.token = null; show('sv-screen-name'); return; }
      state.items = data.items || [];
      state.remaining = data.remaining || 0;
      state.author = data.author || '';
      renderIntro();
      show('sv-screen-intro');
    }).catch(function () { /* api() уже показал нужный экран */ });
  }

  document.addEventListener('DOMContentLoaded', init);
})();
