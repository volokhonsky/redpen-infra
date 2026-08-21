# Инструкция для агента: Этапы 0 и 1 плана доработки редактора

> **Историческое.** Этапы 0–1 выполнены и на проде; топология сервера здесь и в
> `docs/server-setup-guide.md`, на который эта инструкция ссылается, устарела.
> Актуальная топология — `docs/deployment-log.md` и память проекта.

Задача: выполнить Этап 0 (устранение дыр безопасности) и Этап 1 (Google-аутентификация
и пользователи) из `docs/editor-improvement-plan.md`. Инструкция самодостаточна,
но перед началом обязательно прочитай:

- `docs/editor-improvement-plan.md` — общий план и найденные проблемы (разделы 2–3);
- `scripts/api/main.py`, `scripts/api/config.py`, `scripts/api/storage.py` — весь бэкенд;
- `templates/js/redpen-editor-bootstrap.js`, `templates/js/redpen-editor-panel.js` — фронтенд редактора;
- `tests/conftest.py`, `tests/test_api.py` — как устроены тесты (`TestClient`, env задаётся в conftest до импорта);
- память проекта: деплой описан в `docs/deployment-log.md` / `docs/server-setup-guide.md`.

## Общие правила (нарушать нельзя)

1. **Сайт остаётся полностью статическим** (см. раздел «Ключевое ограничение» в
   `docs/README.md`): просмотрщик (без `?editor=1`) не должен получить ни одного
   нового сетевого вызова. Всё новое — только в editor-режиме.
2. **Ноль регрессий просмотра**: код редактора активируется только при
   `hasEditorFlag()`; это правило уже соблюдается в bootstrap.js — сохрани его.
3. Каждый шаг сопровождается тестами в `tests/test_api.py` (или новом файле
   `tests/test_auth.py`). После каждого шага `pytest` должен быть зелёным.
4. Не коммить секреты. Всё секретное — через env / `.env.secrets` (на сервере),
   в репозитории — только `.env.sample` с плейсхолдерами.
5. Фронтенд-исходники живут в `templates/js/` и копируются в `redpen-publish`
   скриптом `build_website.py`. Правь только `templates/`, не `redpen-publish/` напрямую.
6. Стиль кода — как в существующих файлах (vanilla JS с IIFE, без сборщиков;
   Python без новых фреймворков поверх FastAPI).

---

## Этап 0 — Закрыть дыры безопасности

Порядок шагов важен: 0.1 → 0.2 → … Каждый шаг — отдельный коммит.

### 0.1. Токены доступа из кода в окружение

Сейчас: `VALID_TOKENS` захардкожен в `scripts/api/main.py:34`.

- В `config.py` добавь чтение `EDITOR_TOKENS` — строка вида
  `"token1:username1,token2:username2"`; распарси в dict `{token: username}`.
  Пустая/отсутствующая переменная → пустой dict (вход по токену отключён).
- В `main.py` замени `VALID_TOKENS` на `config.EDITOR_TOKENS`.
- Удали строку `logger.debug("login: available tokens: %s", ...)` (`main.py:263`) —
  секреты в логах недопустимы. Проверь весь файл: логировать можно длину токена,
  но не содержимое (текущий `token[:8]` тоже убери).
- Обнови `scripts/api/.env.sample` (добавь `EDITOR_TOKENS=`) и `scripts/api/README.md`.
- Тест: логин с токеном из monkeypatch-окружения проходит, с посторонним — 401.
  Учти: `config.py` читает env при импорте, поэтому в тестах патчь
  `config.EDITOR_TOKENS` (или `main.VALID_TOKENS`-замену) напрямую, а не env.

### 0.2. Серверные сессии: срок жизни и logout

Сессии пока остаются in-memory (`_session_store`) — в БД переедут на этапе 1,
но уже сейчас:

- Храни в записи сессии `created_at` и `expires_at` (7 дней, как у cookie).
  Просроченная сессия при обращении удаляется и считается отсутствующей.
- Добавь `POST /api/auth/logout`: удаляет сессию, очищает cookie
  (`response.delete_cookie("redpen_session")`), возвращает `{"ok": true}`.
- Вынеси проверку сессии в зависимость:

  ```python
  async def require_user(request: Request) -> dict:
      """FastAPI dependency: return session user data or raise 401."""
      # cookie redpen_session -> запись в _session_store, проверка expires_at
  ```

- Тесты: me после logout — 401; истёкшая сессия — 401.

### 0.3. Аутентификация на всех write-эндпоинтах

Сейчас `POST/PUT /api/editor/...` (`main.py:671`, `main.py:731`) доступны анонимно.

- Повесь `user = Depends(require_user)` на: `POST /api/editor/...`,
  `PUT /api/editor/...`, `POST /api/rebuild/...`, `POST /api/store`,
  `POST /api/store-raw`.
- `GET /api/editor/...` и `GET /api/pages/...` оставь публичными (чтение).
- `GET /logs` и `GET /api/logs` — тоже за `require_user` (на этапе 1 сузим до admin).
- Клиент уже обрабатывает 401 (показывает login-модалку) — фронт не трогаем.
- Тесты: анонимный POST/PUT → 401; с валидной сессией → 200. Существующие тесты
  на editor-эндпоинты нужно обновить: сначала логин, потом запрос (TestClient
  сохраняет cookies в рамках одного клиента).

### 0.4. Реальная проверка CSRF

Сейчас `/api/auth/csrf` выдаёт токен, но никто его не сверяет.

Схема — привязка к сессии (надёжнее double-submit и не требует читаемой cookie):

- `GET /api/auth/csrf` требует сессию; генерирует токен, кладёт его **в запись
  сессии** (`session["csrf"]`), возвращает в JSON-теле (клиент уже берёт из тела).
  Cookie `csrf_token` больше не нужна — убери её установку.
- Зависимость `require_csrf`: заголовок `X-CSRF-Token` обязан совпадать с
  `session["csrf"]`, иначе 403. Повесь на все write-эндпоинты из шага 0.3
  (кроме `/api/auth/login` — на момент логина сессии ещё нет; login защищён
  самим токеном + SameSite=Lax).
- Фронт: в `loginWithToken` убери предварительный `getCsrf()` (до логина он
  теперь вернёт 401), а в `onSubmit` он уже вызывается после проверки
  `isAuthenticated` — порядок правильный. Проверь, что при 403 от CSRF клиент
  сбрасывает `st.auth.csrfToken` и повторяет `getCsrf()` один раз.
- Тесты: POST без заголовка → 403; с чужим/протухшим токеном → 403; полный
  цикл login → csrf → POST → 200.

### 0.5. CORS: только реальные origin'ы

- В `main.py`: если список origin'ов — `["*"]`, то `allow_credentials=False`
  (комбинация `*` + credentials некорректна); иначе `allow_credentials=True`.
- Сузь `allow_methods` до `["GET", "POST", "PUT", "DELETE", "OPTIONS"]` и
  `allow_headers` до `["Content-Type", "X-CSRF-Token"]`.
- В деплой-чеклист (см. ниже) внеси: на проде
  `CORS_ALLOW_ORIGINS=https://medinsky.net`.
- Тест: с явным origin'ом preflight возвращает его в `access-control-allow-origin`.

### 0.6. Optimistic locking (409)

Клиент уже шлёт `clientPageSha` в payload и обрабатывает 409 — сервер игнорирует.

- В `_parse_annotation_body` не попадает `clientPageSha` — читай его отдельно
  из body в POST/PUT editor-эндпоинтах.
- Логика: если `clientPageSha` передан и не пуст и текущая страница существует
  (`page["serverPageSha"]` непустой) и значения не совпадают → `409` с телом
  `{"detail": "conflict", "serverPageSha": "<текущий>"}`. Если `clientPageSha`
  не передан — принять запрос, но `logger.warning` (переходный режим).
- Фронт: чтобы sha был известен до первой отправки, при инициализации редактора
  (в `init()` после монтирования панели) сделай `GET /api/editor/{docId}/{pageNum}`
  и сохрани `serverPageSha` в `st.page.serverPageSha` (функция уже почти есть —
  `fetchPageFromServer`, но она перерисовывает маркеры; нужен лёгкий вариант,
  который берёт только sha и НЕ трогает DOM просмотра). docId/pageNum уже
  лежат в `st.page` (`window.currentDocId` / `window.currentPageNum`).
  После успешного сохранения sha обновляется из ответа (уже реализовано).
- Тесты: два клиента читают страницу; первый пишет — ок; второй пишет со старым
  sha → 409; после перечитывания — ок.

### 0.7. Чистка фронтенда

- Удали все отладочные `console.log`/`console.warn` с пометками «✅ ЛОГ» из
  `redpen-editor-bootstrap.js` (функции `saveAnnotationToServer`, `onSubmit`).
  Оставь только `console.error` в catch-блоках.
- Убери `window.alert('Сохранено')` / `alert('Отправлено')`? — НЕТ, пока оставь
  (замена на toast — этап 4). Только логи.

### Критерии приёмки этапа 0

- [ ] `pytest` зелёный; добавлены тесты: 401 на анонимную запись, CSRF 403,
      409-конфликт, logout, истечение сессии.
- [ ] В коде репозитория нет ни одного секрета (grep по бывшим токенам пуст).
- [ ] Анонимный `curl -X POST https://api.medinsky.net/api/editor/x/1` → 401.
- [ ] Просмотр без `?editor=1` не делает запросов к API (проверить вкладкой
      Network: только статика).

---

## Этап 1 — Google-аутентификация, пользователи, роли

### 1.0. Предусловие (делает пользователь, не агент)

Нужен Google OAuth Client ID: console.cloud.google.com → APIs & Services →
Credentials → Create OAuth client ID → тип **Web application** → Authorized
JavaScript origins: `https://medinsky.net` и `http://localhost:8000` (для dev).
Секрет клиента НЕ нужен (используем только ID-token). Если client ID ещё не
выдан — реализуй всё по этой инструкции с плейсхолдером и отметь в отчёте,
что нужно вписать значение.

### 1.1. Зависимости

В `scripts/api/requirements-api.txt` добавь:

```
google-auth~=2.30
requests~=2.32
```

(`requests` нужен транспорту `google.auth.transport.requests`.)

### 1.2. База данных (SQLite, stdlib `sqlite3`)

Новый модуль `scripts/api/db.py`. Без ORM. Требования:

- Путь к БД — env `DB_PATH` (добавь в `config.py`), дефолт
  `/var/redpen-db/redpen.db`. **Не клади БД в `STORAGE_DIR`** — тот
  примонтирован к рабочей копии `redpen-publish`, БД попадёт в git-репозиторий
  публикации.
- При старте (`init_db()`, вызвать из startup-хука): `os.makedirs` для каталога,
  `PRAGMA journal_mode=WAL`, создание схемы `CREATE TABLE IF NOT EXISTS`:

```sql
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  google_sub TEXT UNIQUE,            -- NULL для token-пользователей
  email TEXT UNIQUE,
  name TEXT,
  picture_url TEXT,
  role TEXT NOT NULL DEFAULT 'viewer',   -- viewer | editor | admin
  created_at TEXT NOT NULL,
  last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,               -- secrets.token_hex(32)
  user_id INTEGER NOT NULL REFERENCES users(id),
  csrf TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS editor_allowlist (
  email TEXT PRIMARY KEY,
  role TEXT NOT NULL DEFAULT 'editor',
  added_by TEXT,
  added_at TEXT NOT NULL
);
```

- Соединение: `sqlite3.connect(..., check_same_thread=False)` на запрос или
  одно защищённое локом — выбери простейшее корректное (у uvicorn 1 worker).
- Функции: `get_or_create_user_google(sub, email, name, picture) -> user`,
  `get_or_create_user_token(username) -> user`, `create_session(user_id) -> id`,
  `get_session(id) -> (session, user) | None` (с проверкой expires_at и
  удалением просроченной), `delete_session(id)`, `set_session_csrf(id, token)`,
  `resolve_role(email) -> str` (см. 1.4), allowlist CRUD.
- Тесты: в `tests/conftest.py` добавь `os.environ.setdefault("DB_PATH", os.path.join(_TMP_ROOT, "db", "redpen.db"))`.

### 1.3. Эндпоинт `POST /api/auth/google`

- Тело: `{"credential": "<ID-token из GIS>"}`.
- Верификация — оберни в отдельную функцию (для мокабельности в тестах):

  ```python
  def verify_google_token(credential: str) -> dict:
      from google.oauth2 import id_token
      from google.auth.transport import requests as google_requests
      return id_token.verify_oauth2_token(
          credential, google_requests.Request(), config.GOOGLE_CLIENT_ID)
  ```

- `GOOGLE_CLIENT_ID` — новый env в `config.py`; если пуст, эндпоинт отвечает
  503 «google auth is not configured».
- Проверки: исключение при верификации → 401; `email_verified` должен быть
  true; из claims взять `sub`, `email`, `name`, `picture`.
- `get_or_create_user_google(...)`, роль — `resolve_role(email)` (пересчитывать
  при каждом логине), `create_session`, cookie `redpen_session`
  (`httponly=True, samesite="lax", secure=True, max_age=30*86400`).
  Для локальной разработки по http: `secure=config.COOKIE_SECURE` (env, дефолт true).
- Ответ: `{"userId", "email", "name", "picture", "role"}`.

### 1.4. Роли

- `resolve_role(email)`: если email в env `ADMIN_EMAILS` (список через запятую,
  добавь в config) → `admin`; иначе если в `editor_allowlist` → роль из таблицы;
  иначе `viewer`.
- Зависимости `require_editor` / `require_admin` поверх `require_user`
  (403 при недостаточной роли).
- Раздай права:
  - запись аннотаций (`POST/PUT /api/editor/...`) → `require_editor`;
  - `/logs`, `/api/logs`, allowlist-эндпоинты → `require_admin`;
  - `POST /api/store*`, `POST /api/rebuild/...` → `require_editor`.
- Токен-вход (`/api/auth/login`) остаётся dev-fallback: создаёт пользователя
  через `get_or_create_user_token(username)` с ролью `editor`. Сессии токен- и
  google-пользователей устроены одинаково (обе в БД — перенеси `_session_store`
  в таблицу sessions, in-memory dict удали).
- Админ-API allowlist'а (для кабинета этапа 3, но сделать сейчас — просто):
  - `GET /api/admin/allowlist` → список;
  - `POST /api/admin/allowlist` `{"email", "role"}` → upsert;
  - `DELETE /api/admin/allowlist/{email}`.

### 1.5. `GET /api/auth/me` и `POST /api/auth/logout`

- `me`: `{"userId", "email", "name", "picture", "role", "username"}` (username —
  для совместимости с текущим фронтом).
- `logout`: удалить сессию из БД, очистить cookie.

### 1.6. Фронтенд: кнопка Google вместо токен-модалки

Правки только в `templates/js/redpen-editor-bootstrap.js`,
`templates/js/redpen-editor-panel.js`, `templates/document_index.html`.

1. В `document_index.html` перед подключением js добавь конфиг-блок:

   ```html
   <script>
     window.REDPEN_API_BASE = window.REDPEN_API_BASE || 'https://api.medinsky.net';
     window.REDPEN_GOOGLE_CLIENT_ID = window.REDPEN_GOOGLE_CLIENT_ID || '<CLIENT_ID>.apps.googleusercontent.com';
   </script>
   ```

   (client ID — публичное значение, в статике ему быть можно.)

2. GIS-скрипт `https://accounts.google.com/gsi/client` подключай **динамически
   и только в editor-режиме** (в `init()`), чтобы просмотрщик остался без
   внешних запросов.

3. Переработай `showLoginModal(message)`:
   - контейнер `#redpen-login` показывает: заголовок «Требуется вход», кнопку
     Google (`google.accounts.id.initialize({client_id, callback})` +
     `google.accounts.id.renderButton(el, {...})`), и — если
     `window.REDPEN_DEV_TOKEN_LOGIN === true` — старое поле токена как fallback;
   - callback GIS: `POST /api/auth/google` c `{credential: response.credential}`,
     `credentials:'include'`; при успехе — `apiMe()`, скрыть модалку, обновить
     шапку панели.

4. Шапка панели (в `redpen-editor-panel.js`, над заголовком «Редактор
   аннотаций»): блок `#redpen-auth-status`. Состояния:
   - не залогинен: текст «Вы не вошли» + кнопка «Войти»;
   - залогинен: аватар (`picture`, 24px, скруглённый), имя, роль в скобках,
     кнопка «Выйти» (`POST /api/auth/logout` → сброс `st.auth`, показать
     «Вы не вошли»).
   Панель должна дать bootstrap'у колбэки/методы `setAuthState(user|null)` —
   сохраняй существующий стиль взаимодействия через `window.RedPenEditorPanel`.

5. При `init()` редактора вызывай `apiMe()` (тихо, 401 — не ошибка) и заполни
   шапку. При 403 от записи (роль viewer) показывай понятное сообщение
   «Недостаточно прав, обратитесь к администратору» — не login-модалку.

### 1.7. Тесты (`tests/test_auth.py`)

Мокай `main.verify_google_token` через `monkeypatch.setattr`. Минимальный набор:

- логин google: новый пользователь создан, роль viewer; повторный логин — тот же user id;
- email из `ADMIN_EMAILS` → role admin; email из allowlist → editor;
- viewer: `POST /api/editor/...` → 403; editor → 200;
- `email_verified: false` → 401; невалидный credential (мок кидает ValueError) → 401;
- logout → me 401; сессия с истёкшим `expires_at` → 401;
- админ-CRUD allowlist: viewer → 403, admin → 200, после добавления email
  повторный логин этого email даёт editor;
- GOOGLE_CLIENT_ID пуст → 503.

### 1.8. Docker / деплой-конфигурация (файлы в репо)

- `scripts/api/Dockerfile`: добавь `RUN mkdir -p /var/redpen-db && chown app:app /var/redpen-db`
  до `USER app` (свежий named volume унаследует владельца; известные грабли —
  api работает под uid 10001, см. память `redpen-storage-permissions-gotcha`).
- `docker-compose.yml`: сервису `api` добавь том `redpen_db:/var/redpen-db`
  (новый named volume в секции `volumes:`) и env
  `DB_PATH=/var/redpen-db/redpen.db`, `GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}`,
  `ADMIN_EMAILS=${ADMIN_EMAILS}`, `EDITOR_TOKENS=${EDITOR_TOKENS}`.
- `.env.sample` (корневой и/или `scripts/api/.env.sample`): все новые переменные
  с плейсхолдерами и комментариями.
- Обнови `scripts/api/README.md` (эндпоинты auth, переменные) и
  `docs/STATE_OVERVIEW.md` (раздел про аутентификацию).

### 1.9. Деплой на прод (в самом конце, после зелёных тестов)

По модели из памяти `redpen-deployment-model`:

1. Скопируй изменённые файлы бэкенда/инфры на сервер в
   `/root/apps/redpen/infra` (scp; это не git-клон инфры).
2. На сервере добавь в `/root/apps/redpen/infra/.env` (или `.env.secrets`):
   `GOOGLE_CLIENT_ID`, `ADMIN_EMAILS=volokhonsky@gmail.com`, `EDITOR_TOKENS`
   (новые значения — старые токены скомпрометированы попаданием в git),
   `CORS_ALLOW_ORIGINS=https://medinsky.net`.
3. `docker compose up -d --build api` и smoke-check:
   `curl https://api.medinsky.net/api/health`;
   анонимный POST на editor → 401; `/logs` → 401.
4. Фронтенд: локально `python scripts/build_website.py --skip-tests` → push
   `redpen-publish` → на сервере `docker restart redpen-content-sync-1`.
5. Ручная проверка в браузере: `https://medinsky.net/medinsky11klass/?editor=1` —
   вход через Google, сохранение аннотации под editor-ролью, 403 под viewer.

**Деплой затрагивает прод — перед пунктами 1–5 покажи пользователю, что готово
локально, и получи подтверждение.**

### Критерии приёмки этапа 1

- [ ] `pytest` зелёный, включая новый `tests/test_auth.py`.
- [ ] Вход через Google работает end-to-end (после вписывания реального client ID).
- [ ] Роли действуют: viewer не может писать (403), editor может, admin видит `/logs`.
- [ ] Сессии переживают рестарт контейнера (лежат в SQLite на томе).
- [ ] Просмотр без `?editor=1`: по-прежнему ни одного запроса к API и ни одного
      запроса к accounts.google.com.
- [ ] В репозитории нет секретов; `.env.sample` актуален.

## Что НЕ входит в эти этапы (не делать)

- Перенос аннотаций в SQLite, DELETE-эндпоинт, история — этап 2.
- Кабинет `/cabinet` — этап 3 (admin-API allowlist'а из 1.4 — исключение).
- Toast'ы, drag маркеров, автосохранение форм — этап 4.
- Правка механизма публикации/content-sync — этап 2/5.
