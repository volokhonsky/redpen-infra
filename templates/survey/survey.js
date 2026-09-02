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
   * Заход опознаётся токеном в заголовке `X-Survey-Token`, а не кукой:
   * кука потребовала бы CSRF-защиты, а токен, недоступный чужому сайту,
   * снимает вопрос целиком. Токен живёт в sessionStorage — закрыли вкладку,
   * и заход кончился. Хранить опознание дольше не за чем: с 2026-09-01
   * вернуться можно, просто назвавшись тем же псевдонимом — он и есть
   * респондент, а заход лишь его сессия (docs/anonymity-model.md).
   *
   * Карточка рисуется по описанию вопросов из API (`questions`): у каждого
   * вопроса `answer` — `"value"` (кнопки) или `"text"` (поле).
   */

  var preview = window.RedPenPreview;
  var TOKEN_KEY = 'redpen_survey_token';

  var state = {
    token: null,
    author: '',
    questions: [],  // объединённый список: шкалы + открытые вопросы
    items: [],      // текущая порция
    index: 0,
    answers: {},    // name -> value|text для показанной карточки
    answered: 0,    // сколько замечаний оценено за этот заход
    returning: false, // под этим псевдонимом уже отвечали раньше
    remaining: 0,
    tail: false     // остаток не больше порции — покажем его весь
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

  function remarksWord(n) { return plural(n, 'замечание', 'замечания', 'замечаний'); }

  var SCREENS = ['sv-screen-name', 'sv-screen-intro', 'sv-screen-card', 'sv-screen-done'];

  function show(id) {
    SCREENS.forEach(function (name) { el(name).hidden = name !== id; });
    setStatus('');
    window.scrollTo(0, 0);
  }

  function valueQuestions() {
    return state.questions.filter(function (q) { return q.answer !== 'text'; });
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
    state.questions = data.questions || [];
    state.returning = !!data.returning;
    writeToken(data.token);
    // Порцию тянем сразу — чтобы на экране-инструкции уже знать про хвост пула.
    await fetchBatch();
    renderIntro();
    show('sv-screen-intro');
  }

  // --- 2. инструкция ------------------------------------------------------

  function renderTail(id, left) {
    var host = el(id);
    if (!host) return;
    var n = left == null ? state.remaining : left;
    if (state.tail && n > 0) {
      host.textContent = 'Осталось ' + n + ' ' + remarksWord(n) +
        ' — это всё, что вы ещё не оценивали; мы покажем их все.';
      host.hidden = false;
    } else {
      host.hidden = true;
    }
  }

  function renderIntro() {
    // Тот же псевдоним — продолжение, а не новый опрос: уже оценённое второй
    // раз не раздаётся, и человек иначе увидел бы пустую выдачу без причины.
    el('sv-intro-returning').hidden = !state.returning;
    el('sv-intro-scales').innerHTML = valueQuestions().map(function (q) {
      // Точка не дописывается: заголовок шкалы сам сформулирован вопросом.
      return '<li><b>' + escapeHtml(q.title) + '</b> ' + escapeHtml(q.hint) + '</li>';
    }).join('');
    renderTail('sv-intro-tail');
  }

  // --- 3. карточка --------------------------------------------------------

  function renderQuestions() {
    el('sv-scales').innerHTML = state.questions.map(function (q) {
      if (q.answer === 'text') {
        var cur = state.answers[q.name] || '';
        return '<div class="sv-scale sv-scale--text">' +
          '<div class="sv-scale-title">' + escapeHtml(q.title) + '</div>' +
          '<div class="sv-scale-hint">' + escapeHtml(q.hint) + '</div>' +
          '<textarea class="sv-open" data-question="' + escapeHtml(q.name) + '" rows="4" ' +
            'maxlength="' + (q.maxLength || 1000) + '" ' +
            'placeholder="Необязательно">' + escapeHtml(cur) + '</textarea>' +
        '</div>';
      }
      // Подписи вместо цифр там, где шкала — вопрос о решении, а не о мере.
      var choices = q.options;
      if (!choices) {
        choices = [];
        for (var v = q.min; v <= q.max; v++) choices.push({ value: v, label: String(v) });
      }
      var buttons = choices.map(function (choice) {
        return '<button type="button" class="sv-choice' +
          (state.answers[q.name] === choice.value ? ' is-picked' : '') +
          '" data-scale="' + escapeHtml(q.name) + '" data-value="' + choice.value + '">' +
          escapeHtml(choice.label) + '</button>';
      }).join('');
      // Подписи концов принадлежат шкале: пара «совсем нет — да, вполне»
      // отвечала на вопрос «да или нет», а шкала спрашивает о мере.
      var ends = q.ends
        ? '<div class="sv-scale-ends"><span>' + escapeHtml(q.ends.low) +
          '</span><span>' + escapeHtml(q.ends.high) + '</span></div>'
        : '';
      return '<div class="sv-scale">' +
        '<div class="sv-scale-title">' + escapeHtml(q.title) + '</div>' +
        '<div class="sv-scale-hint">' + escapeHtml(q.hint) + '</div>' +
        '<div class="sv-scale-row">' + buttons + '</div>' + ends +
      '</div>';
    }).join('');
  }

  async function renderCard() {
    var item = state.items[state.index];
    if (!item) { finish(); return; }
    state.answers = {};
    renderQuestions();
    el('sv-progress-text').textContent =
      'Замечание ' + (state.index + 1) + ' из ' + state.items.length;

    var label = await preview.pageLabel(item.docId, item.pageKey || item.pageNum);
    var url = preview.remarkUrl(item.docId, label, item.remarkId);
    el('sv-preview').src = url;
    el('sv-open-link').href = url;
    preview.fitFrame('sv-preview-fit', 'sv-preview', { native: true });
  }

  function currentText(name) {
    return (state.answers[name] || '').trim();
  }

  async function submitAndAdvance(save) {
    var item = state.items[state.index];
    if (!item) return;
    if (save) {
      var scaleNames = valueQuestions().map(function (q) { return q.name; });
      var hasScale = scaleNames.some(function (n) { return state.answers[n] != null; });
      var hasText = state.questions.some(function (q) {
        return q.answer === 'text' && currentText(q.name);
      });
      if (hasText && !hasScale) {
        // Иначе текст потерялся бы на 400: сервер комментарий без оценки не примет.
        setStatus('Отметьте хотя бы одну оценку — или нажмите «Пропустить», ' +
                  'если оценивать не хотите.', true);
        return;
      }
      if (hasScale || hasText) {
        var payload = {
          docId: item.docId,
          pageKey: item.pageKey || item.pageNum,
          remarkId: item.remarkId
        };
        state.questions.forEach(function (q) {
          var a = state.answers[q.name];
          if (q.answer === 'text') {
            if (currentText(q.name)) payload[q.name] = currentText(q.name);
          } else if (a != null) {
            payload[q.name] = a;
          }
        });
        await api('PUT', '/api/survey/ratings', payload);
        state.answered += 1;
      }
    }
    state.index += 1;
    await renderCard();
  }

  function finish() {
    var left = Math.max(0, state.remaining - state.answered);
    el('sv-done-text').textContent = state.answered
      ? 'Вы оценили ' + state.answered + ' ' + remarksWord(state.answered) + '. Это уже помогло.'
      : 'В этот раз вы ничего не оценили — тоже ответ.';

    var more = el('sv-more');
    var doneAll = el('sv-done-all');
    if (left > 0) {
      more.hidden = false;
      more.textContent = state.tail
        ? 'Оценить оставшиеся ' + left + ' ' + remarksWord(left)
        : 'Оценить ещё ' + Math.min(left, 10);
      doneAll.hidden = true;
    } else {
      more.hidden = true;
      doneAll.hidden = false;
      doneAll.textContent = 'Вы прошли весь пул. Спасибо.';
    }
    renderTail('sv-done-tail', left);
    show('sv-screen-done');
  }

  // --- порция ------------------------------------------------------------

  async function fetchBatch() {
    var data = await api('GET', '/api/survey/batch');
    state.items = data.items || [];
    state.remaining = data.remaining || 0;
    state.tail = !!data.tail;
    state.index = 0;
    state.answered = 0;
    state.author = data.author || state.author;
    if (data.questions) state.questions = data.questions;
  }

  async function startBatch() {
    if (!state.items.length) { finish(); return; }
    show('sv-screen-card');
    await renderCard();
  }

  // --- запуск -----------------------------------------------------------

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
      startBatch().catch(function (e) { setStatus(e.message, true); });
    });
    el('sv-scales').addEventListener('click', function (event) {
      var target = event.target;
      if (!target.classList.contains('sv-choice')) return;
      var scale = target.dataset.scale;
      var value = parseInt(target.dataset.value, 10);
      // Повторное нажатие снимает ответ: передумать проще, чем начинать заново.
      state.answers[scale] = state.answers[scale] === value ? null : value;
      if (state.answers[scale] == null) delete state.answers[scale];
      // Перерисовываем только кнопки этой шкалы — полный innerHTML затёр бы
      // текст в открытом поле и увёл бы из него фокус.
      Array.prototype.forEach.call(target.parentNode.querySelectorAll('.sv-choice'),
        function (btn) {
          btn.classList.toggle('is-picked',
            state.answers[scale] === parseInt(btn.dataset.value, 10));
        });
    });
    el('sv-scales').addEventListener('input', function (event) {
      var target = event.target;
      if (!target.classList.contains('sv-open')) return;
      var name = target.dataset.question;
      if (target.value.trim()) state.answers[name] = target.value;
      else delete state.answers[name];
    });
    el('sv-next').addEventListener('click', function () {
      submitAndAdvance(true).catch(function (e) { setStatus(e.message, true); });
    });
    el('sv-skip').addEventListener('click', function () {
      submitAndAdvance(false).catch(function (e) { setStatus(e.message, true); });
    });
    el('sv-more').addEventListener('click', function () {
      fetchBatch().then(startBatch).catch(function (e) { setStatus(e.message, true); });
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
    // Токен есть — вкладку перезагрузили посреди опроса. Словарь вопросов
    // приходит вместе с порцией; если токен ещё жив, api() вернёт данные,
    // иначе сам отправит на первый экран.
    fetchBatch().then(function () {
      if (!state.questions.length) {
        dropToken(); state.token = null; show('sv-screen-name'); return;
      }
      renderIntro();
      show('sv-screen-intro');
    }).catch(function () { /* api() уже показал нужный экран */ });
  }

  document.addEventListener('DOMContentLoaded', init);
})();
