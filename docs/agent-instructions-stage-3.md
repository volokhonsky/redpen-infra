# Инструкция для агента: Этап 3 — рабочий кабинет `/cabinet`

Задача: выполнить Этап 3 плана `docs/editor-improvement-plan.md` — рабочий
кабинет: профиль, списки аннотаций с фильтрами, черновики → публикация,
история с откатом, админ-раздел.

Этапы 0–2 выполнены и задеплоены (2026-07-08/09): Google-auth + роли
(viewer/editor/admin), CSRF, SQLite — канонический стор аннотаций
(`annotations` + `annotation_history`), публикация статических JSON в том
`redpen_public`, DELETE, optimistic locking, манифест страниц + `?p=`.
Логи: `docs/deployment-log.md`, `docs/stage-2-progress-log.md`.

Перед началом обязательно прочитай:

- `docs/editor-improvement-plan.md` — раздел «Этап 3» и **«Жёсткое ограничение:
  сайт остаётся полностью статическим»**;
- `scripts/api/main.py` — зависимости auth (`require_user/require_csrf/
  require_editor/require_admin/require_admin_csrf`, строки ~155–203),
  editor-эндпоинты (~640+), allowlist-API (~426–457), publish-all (~459);
- `scripts/api/db.py` — функции аннотаций (`_annotation_row_to_dict`,
  `upsert_annotation_db`, `soft_delete_annotation`, `add_history`,
  `list_page_annotations`) и пользователей/сессий;
- `scripts/api/publisher.py` — `render_page` (только published),
  `compute_page_sha`, `publish_page`;
- `templates/js/redpen-editor-bootstrap.js`, `redpen-editor-panel.js`,
  `templates/document_index.html` (конфиг-блок `REDPEN_API_BASE`/
  `REDPEN_GOOGLE_CLIENT_ID`, строки ~140–141);
- `scripts/build_website.py` — как копируются `css/`, `js/`, `*.svg`
  (строки ~570–583) — по этому образцу поедет и `cabinet/`;
- `tests/conftest.py`, `tests/test_api.py`, `tests/test_auth.py`,
  `tests/test_annotations_db.py`.

## Общие правила (нарушать нельзя)

1. **Статическое ограничение**: просмотрщик (без `?editor=1`) не получает ни
   одного нового сетевого вызова. Кабинет — страница редактирования, ему API
   можно; но в офлайн-копии сайта он должен деградировать корректно
   (сообщение «нет соединения с сервером», не белый экран).
2. **Черновики не должны попадать в статику**: `publisher.render_page` рендерит
   только `status='published'` — это уже так; не менять и покрыть тестом.
3. Каждый шаг — отдельный коммит, `pytest` зелёный после каждого.
4. Vanilla JS в стиле существующего кода (IIFE, без сборщиков/фреймворков);
   Python — как в main.py/db.py.
5. Веди журнал `docs/stage-3-progress-log.md` (по образцу stage-2), чтобы
   работу можно было продолжить после остановки.
6. Деплой — только по runbook'у и **только после подтверждения пользователя**.

## Ключевые факты (сверены с кодом 2026-07-09)

- `require_user` возвращает `{sessionId, csrf, userId, email, name, pictureUrl,
  role, username}`; `userId` — int id из таблицы users. Editor-эндпоинты
  передают `author_id=user["userId"]`.
- `require_editor` = сессия + CSRF + роль editor|admin; `require_admin` — без
  CSRF (только для GET); `require_admin_csrf` — с CSRF (для мутаций).
- `upsert_annotation_db(...)` **уже принимает** `status: str = "published"` и
  `action`, пишет history в той же транзакции. Snapshot в history — JSON от
  `_annotation_row_to_dict`, ключи **camelCase**: `annId, docId, pageNum,
  annType, text, coordX, coordY, status, authorId, createdAt, updatedAt`.
- `upsert_annotation_db` при UPDATE перезаписывает `author_id` — для этапа 3
  это ок (последний редактор = автор состояния).
- `GET /api/editor/{doc}/{page}` сейчас анонимный и отдаёт только published.
- `_validate_page_key` нормализует ключ страницы (`"6"`→`"006"`, `"-1"`→`"-01"`).
- Кабинет живёт на `medinsky.net/cabinet/`, API — `api.medinsky.net`: same-site,
  cookie `redpen_session` (SameSite=Lax) работает с `credentials:'include'` —
  так уже работает редактор.

---

## Шаги

### C.1. db.py: выборки для кабинета

Функции (стиль существующих, с `_lock`; camelCase-ключи в ответах как в
`_annotation_row_to_dict`):

- `list_annotations(doc_id=None, page_num=None, ann_type=None, status=None,
  author_id=None, q=None, limit=50, offset=0) -> List[dict]` — LEFT JOIN users,
  в каждом элементе добавь `authorName`/`authorEmail` (NULL для импортированных);
  `q` — `text LIKE '%'||?||'%'` (экранируй `%`/`_` через ESCAPE); сортировка
  `updated_at DESC, rowid_pk DESC` (фиксированная, без user-supplied sort).
- `count_annotations(<те же фильтры>) -> int`.
- `list_history(doc_id=None, page_num=None, ann_id=None, author_id=None,
  action=None, limit=50, offset=0) -> List[dict]` — LEFT JOIN users
  (`authorName`), `snapshot` — распарсенный dict, сортировка `id DESC`.
- `get_history_record(hist_id: int) -> Optional[dict]`.
- `list_users() -> List[dict]` — id, email, name, pictureUrl, role,
  createdAt, lastLoginAt (без google_sub).
- `get_stats() -> dict` — `{"docs": [{"docId", "published", "draft",
  "deleted"}], "recentActivity": [<последние 10 history: docId, pageNum,
  annId, action, authorName, createdAt>]}`.

Тесты: `tests/test_cabinet_db.py` — каждый фильтр, комбинации, пагинация,
поиск с `%` в запросе, счётчики stats.

### C.2. Черновики (бэкенд)

1. `_parse_annotation_body` (main.py:~223): опциональное поле `status`;
   допустимо `"draft"` или `"published"`, иначе 400. В результат — ключ
   `status` только если поле присутствовало.
2. POST/PUT `/api/editor/...`: передавай в `upsert_annotation_db`
   `status=parsed["status"]`, а если поле **не передано** — сохраняй статус
   существующей аннотации (`db.get_annotation(...)["status"]`), для новой —
   `"published"`. **Важно**: без этого правила PUT без `status` молча
   публиковал бы черновик.
3. `GET /api/editor/{doc}/{page}`: добавь опциональную аутентификацию —
   хелпер `async def get_optional_user(request) -> Optional[dict]` (как
   `require_user`, но `None` вместо 401). Аноним/viewer — как сейчас (только
   published). Editor/admin — дополнительно ВСЕ черновики (решение: команда
   маленькая, черновики видны всем редакторам), каждый с флагом
   `"draft": true`, в том же массиве `annotations`. Формат элемента — как у
   published (`id, text, annType, coords?`) + `draft`.
4. `serverPageSha` НЕ меняется: это sha только published-рендера (черновики
   не создают 409 другим). Мутация черновика всё равно возвращает
   `serverPageSha` (published-состояния) — клиент его просто сохраняет.
5. Optimistic lock: применяется как сейчас (сравнение published-sha) для всех
   мутаций, включая черновики.

Тесты (`tests/test_api.py` дополнить): создание draft → нет в статике-файле,
нет в анонимном GET, есть с флагом в editor-GET; PUT без status сохраняет
draft-статус; PUT со status=published → появился в файле; матрица
anon/viewer/editor для GET.

### C.3. API списков и статистики

- `GET /api/annotations` (`Depends(require_user)` + проверка роли editor|admin
  вручную → 403; НЕ `require_editor` — тот требует CSRF, для GET не нужен):
  query-параметры `docId, pageKey, annType, status, authorId, q, limit,
  offset`; валидация: docId — `_validate_doc_id`, pageKey —
  `_validate_page_key`, status ∈ {published,draft,deleted}, annType ∈
  {main,comment,general}, limit ≤ 200 (default 50), offset ≥ 0, len(q) ≤ 200.
  Ответ: `{"items": [...], "total": N, "limit": L, "offset": O}`.
- `GET /api/history` — та же защита; параметры `docId, pageKey, annId,
  authorId, action, limit, offset`; ответ аналогично (`total` можно не
  считать — отдай `hasMore: len(items)==limit`).
- `GET /api/stats` (`require_user` — доступно и viewer'у, там нет текстов):
  ответ `db.get_stats()`.

Тесты: `tests/test_cabinet_api.py` — матрица прав (anon 401 / viewer 403 /
editor 200 / admin 200) для annotations+history; stats: anon 401, viewer 200;
валидация параметров (400 на мусор); фильтры и пагинация сквозь API.

### C.4. Откат: `POST /api/history/{histId}/revert`

`Depends(require_editor)` (сессия+CSRF+роль).

- `db.get_history_record(histId)` → 404 если нет.
- Семантика: **восстановить состояние из snapshot как есть**, включая его
  `status` (snapshot записи action=delete имеет status='deleted' — такой
  revert повторно удаляет; это осознанно: кабинет показывает action каждой
  записи, пользователь выбирает нужное состояние).
- Реализация: `upsert_annotation_db(docId, pageNum, annId, annType, text,
  coord_x=coordX, coord_y=coordY, status=snapshot["status"],
  author_id=user["userId"], action="revert")` (ключи snapshot — camelCase!),
  затем `publisher.publish_page(...)`.
- Ответ: `{"annId", "docId", "pageNum", "serverPageSha", "published"}`.

Тесты: revert к update-записи восстанавливает текст+координаты и
републикует файл; revert к записи до удаления «воскрешает» аннотацию;
revert delete-записи повторно удаляет; в history появляется action='revert';
anon 401 / без CSRF 403 / viewer 403.

### C.5. Админ: список пользователей

`GET /api/admin/users` (`require_admin`) → `db.list_users()`. Роли меняются
существующим allowlist-API (`GET/POST/DELETE /api/admin/allowlist*`) — новый
эндпоинт только читает. Тест: editor → 403, admin → 200, нет `google_sub`
в ответе.

### C.6. Общий auth-модуль фронтенда: `templates/js/redpen-auth.js`

Сейчас весь auth-код (getCsrf/apiMe/loginWithToken/google-логин/GIS-загрузка)
живёт внутри IIFE `redpen-editor-bootstrap.js`. Вынеси в новый файл:

```js
window.RedPenAuth = {
  apiBase(path),            // window.REDPEN_API_BASE || 'https://api.medinsky.net'
  async getCsrf(),          // кэширует токен; сбрасывает кэш и повторяет 1 раз при 403
  async me(),               // null при 401, {userId,email,name,pictureUrl,role,username} иначе
  async loginWithToken(t),
  async loginWithGoogle(credential),  // POST /api/auth/google
  async logout(),           // POST /api/auth/logout (нужен CSRF)
  loadGis(cb),              // динамическая вставка https://accounts.google.com/gsi/client
  renderGoogleButton(el, onLogin),    // initialize + renderButton, callback → loginWithGoogle → onLogin
}
```

- Без состояния редактора: модуль хранит только csrf-кэш и текущего user.
- `redpen-editor-bootstrap.js` переводится на `RedPenAuth` (свои
  `getCsrf/apiMe/...` становятся тонкими обёртками, заполняющими
  `st.auth.*` как раньше). **Нулевая регрессия поведения редактора —
  отдельный критерий приёмки**; сохрани фолбэк: если `window.RedPenAuth`
  не загружен, редактор работает по-старому (не убирай старый код, а
  делегируй с проверкой).
- `templates/document_index.html`: подключи `../js/redpen-auth.js` ПЕРЕД
  `redpen-editor-bootstrap.js`.
- Проверка: `node --check` обоих файлов; ручной smoke редактора локально.

### C.7. Страница кабинета: `templates/cabinet/`

Файлы: `index.html`, `cabinet.js`, `cabinet.css`. Билд копирует каталог в
`<output>/cabinet/` (см. C.10). Пути: страница — `/cabinet/`, общий JS —
`../js/redpen-auth.js`, конфиг-блок (`REDPEN_API_BASE`,
`REDPEN_GOOGLE_CLIENT_ID`) — тот же, что в `document_index.html:140`.

Один экран, вкладки; всё рендерится из JS по данным API:

1. **Шапка/Профиль**: аватар (`pictureUrl`), имя, email, роль; кнопка
   «Выйти». Не залогинен → Google-кнопка (`RedPenAuth.renderGoogleButton`)
   + фолбэк-поле токена при `?auth=token`. Роль viewer → профиль + текст
   «Нет прав редактора. Обратитесь к администратору» (вкладки скрыты).
   Ошибка сети (офлайн-копия) → «Нет соединения с сервером» — страница не
   падает.
2. **Вкладка «Аннотации»** (главная): фильтры (док — из `GET /api/stats`;
   страница — текстовое поле; тип/статус — селекты; автор — селект,
   наполняемый уникальными `authorId/authorName` из уже загруженных строк
   (список всех пользователей доступен только админу); поиск по тексту), таблица: док, страница (label, см.
   C.8), тип, статус (бейдж: серый draft / зелёный published / красный
   deleted), автор, дата изменения, первые ~80 символов текста. Пагинация
   («Показать ещё» по offset). Действия в строке:
   - **Открыть** — ссылка в редактор (C.8);
   - **Опубликовать** (для draft) / **В черновик** (для published) — PUT
     `/api/editor/{doc}/{page}/{annId}` с полным телом строки + новым status;
   - **Удалить** (confirm) — DELETE;
   - **История** — переключение на вкладку «История» с фильтром по annId.
3. **Вкладка «История»**: лента `GET /api/history` (фильтры док/автор/action),
   каждая запись: когда, кто, action, док/страница/annId, фрагмент текста из
   snapshot, кнопка «Откатить к этому состоянию» (confirm) →
   `POST /api/history/{id}/revert` → перерисовать.
4. **Вкладка «Админ»** (только role=admin): allowlist (таблица + форма
   добавить email/роль + удалить — существующий API), список пользователей
   (`GET /api/admin/users`), кнопка «Перепубликовать всё»
   (`POST /api/admin/publish-all`, показывает `{pages, failed}`), «Логи» —
   `GET /api/logs?lines=200` в `<pre>` с кнопкой обновить.

Все мутации — с `X-CSRF-Token` (через `RedPenAuth.getCsrf`). 401 в любой
момент → показать логин-экран. Ошибки — инлайн-текстом (alert не использовать;
toast-библиотек не тащить — простой статус-див).

### C.8. Ссылка «Открыть в редакторе» (кабинет → редактор)

- Цель: `/{docId}/?p={label}&editor=1&ann={annId}`.
- label: кабинет лениво фетчит `/{docId}/metadata.json` (статика; кэш в
  памяти по docId), строит map `file → label` из `metadata.pages`. Ключ
  строки (`pageNum`, например `"006"`) → `file: "page_006"` → label.
- Нет манифеста у дока или файла нет в манифесте → fallback:
  `/{docId}/?page={int(pageNum)}&editor=1&ann={annId}` (для ключей ≤ 0 в
  легаси-режиме страницы недостижимы — тогда показать ключ как текст без
  ссылки с title «страница вне легаси-нумерации»).

### C.9. Редактор: deep-link `?ann=` и черновики в панели

В `redpen-editor-bootstrap.js` (+ мелочи в `redpen-editor-panel.js`):

1. **Deep-link**: в `onAnnotationsLoaded` (и после merge черновиков, п.3)
   прочитать `?ann=<id>` из URL (один раз, флаг в state): найти маркер —
   `window.RedPenEditor.markers.selectById(id)` и загрузить в форму (тот же
   путь, что клик по маркеру: собрать draft из `st.page.annotations` и
   `beginEditingExisting` + `setDraft`). Если id не найден — молча
   игнорировать.
2. **Чекбокс «Черновик»** в панели (`redpen-editor-panel.js`, рядом с
   кнопками): `getDraft()` добавляет `isDraft: bool`; при `setDraft`
   чекбокс выставляется из `draft.status === 'draft'`. `saveAnnotationToServer`
   шлёт `status: isDraft ? 'draft' : 'published'`.
3. **Показ черновиков**: в editor-режиме GET-сидинг страницы (существующий
   вызов `/api/editor/{doc}/{page}` при init) теперь возвращает и черновики
   (`draft:true`) — добавь их в `st.page.annotations` и отрисуй маркеры
   (`markers.upsert`) с отличием: `outline: 2px dashed #888` (класс
   `is-draft`). В обычном просмотре черновиков нет (их нет в статике).
4. Ответ мутаций содержит `published: false` для черновиков — не показывать
   это как ошибку.

Проверка: `node --check`; ручной сценарий локально (см. Verification).

### C.10. Сборка, документация, конфиги

- `scripts/build_website.py`: копировать `templates/cabinet/` →
  `<output>/cabinet/` (по образцу копирования `css/`/`js/`, строки ~570–583).
  Тест в `test_build_website.py`: после сборки `cabinet/index.html` существует.
- `scripts/api/README.md`: новые эндпоинты (annotations, history, revert,
  stats, admin/users) с ролями.
- `docs/STATE_OVERVIEW.md`: раздел про кабинет.
- `docs/editor-improvement-plan.md`: отметить пункты этапа 3.
- caddy/nginx не трогаем: `/cabinet/` — обычная статика в томе.

---

## Verification (локально, до деплоя)

1. `pytest` — все зелёные (ожидаемо ~190+).
2. Локальный стенд: `uvicorn main:app` с tmp `STORAGE_DIR/LOG_DIR/DB_PATH/
   PUBLISH_DIR` + `EDITOR_TOKENS=devtoken:dev`; собрать сайт в tmp
   (`build_website.py --skip-tests --skip-push --target-dir`), раздать
   `python -m http.server`; в браузере:
   - `/cabinet/` — логин по токену (`?auth=token`), список аннотаций
     фильтруется/ищется, «Опубликовать»/«В черновик»/«Удалить» работают и
     меняют статические JSON в PUBLISH_DIR;
   - создать черновик в редакторе (чекбокс) → его нет в статике и анонимном
     просмотре, он виден в кабинете и в editor-режиме пунктирным маркером;
   - история: откат к старому состоянию → текст вернулся, файл
     перепубликован;
   - «Открыть» из кабинета ведёт на страницу с выделенной аннотацией (`?ann=`);
   - редактор без кабинета (обычный сценарий этапов 0–2) работает как раньше
     (регрессия auth-рефакторинга C.6);
   - просмотрщик без `?editor=1`: вкладка Network — только статика.

## Runbook деплоя (после подтверждения пользователя!)

1. Бэкап БД на сервере: `VACUUM INTO '/var/redpen-db/pre-stage3.db'` (cron-бэкап
   уже есть, но сделай именной снапшот).
2. Бэкенд: `scp scripts/api/*.py root@70.34.202.231:/root/apps/redpen/infra/scripts/api/`
   → `docker compose up -d --build api`. Smoke: `/api/health`; анонимный
   `GET /api/annotations` → 401; `GET /api/editor/medinsky11klass/006` → 200
   и без `draft`-элементов.
3. Фронтенд: локально `python scripts/build_website.py --skip-tests` →
   проверить diff (`cabinet/` появился, `js/redpen-auth.js`, правки
   `document_index.html`/js) → push `redpen-publish` → на сервере
   `docker restart redpen-content-sync-1`.
4. Прод-проверка: `https://medinsky.net/cabinet/` — Google-вход (админский
   email), список аннотаций; e2e: черновик в редакторе → виден в кабинете →
   «Опубликовать» → `curl` статического JSON показывает аннотацию → откат из
   истории → `curl` показывает старый текст. Тестовые данные удалить.
5. Проверить редактор на проде (обычное сохранение) и просмотрщик
   (Network: только статика).
6. Запись в `docs/deployment-log.md`; журнал `docs/stage-3-progress-log.md`
   закрыть.

## Критерии приёмки

- [ ] `pytest` зелёный; новые тесты: cabinet_db (фильтры/пагинация/поиск/stats),
      cabinet_api (матрица прав anon/viewer/editor/admin на каждый эндпоинт),
      драфт-цикл (draft не в статике/анонимном GET; PUT без status не меняет
      статус; publish → в файле), revert (update/delete/восстановление),
      admin/users, build копирует cabinet.
- [ ] Черновики никогда не попадают в статические JSON и анонимные ответы.
- [ ] Кабинет: списки с фильтрами/поиском/пагинацией; publish/unpublish/delete
      из таблицы; история с откатом; админ-вкладка невидима для editor и
      недоступна по API (403).
- [ ] «Открыть» из кабинета открывает редактор с выделенной аннотацией
      (label-ссылка при манифесте, `?page=` fallback без него).
- [ ] Редактор после рефакторинга C.6 ведёт себя идентично (вход, CSRF,
      сохранение, 401/409-обработка).
- [ ] Просмотрщик без `?editor=1` — ноль запросов к API (Network-проверка).
- [ ] Офлайн-копия сайта: `/cabinet/` показывает «Нет соединения», ничего
      не ломает.

## Что НЕ входит (не делать)

- Toast'ы, drag маркеров, автосохранение форм, список аннотаций в панели
  редактора — этап 4.
- Модерация сложнее draft/publish (ревью чужих правок, комментарии к правкам),
  email-уведомления.
- Переделка HTML-страницы `/logs` (кабинет читает JSON `/api/logs`).
- Приватность черновиков (все редакторы видят все черновики — осознанное
  решение этапа 3).
- Изменение схемы БД кроме уже существующих таблиц (новые таблицы не нужны).
