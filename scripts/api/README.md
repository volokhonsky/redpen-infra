# RedPen API

FastAPI-сервис для хранения входящих данных и редактирования аннотаций.
Каноническая реализация: `scripts/api` (`main.py`, `storage.py`, `config.py`).

## Конфигурация (переменные окружения)

| Переменная | Значение по умолчанию | Назначение |
|---|---|---|
| `STORAGE_DIR` | `/data` | Корень для данных: `inbox/…` и `<docId>/annotations/page_NNN.json` |
| `LOG_DIR` | `/app/logs` | Каталог для файла лога `redpen-api.log` |
| `LOG_LEVEL` | `INFO` | Уровень логирования |
| `CORS_ALLOW_ORIGINS` | `_` (→ `*`) | Список origin через запятую; `_`/`*` = разрешить все |
| `EDITOR_TOKENS` | (пусто) | Токены личного входа: `token1:username1,token2:username2`. Пусто = вход по токену отключён |
| `DB_PATH` | `/var/redpen-db/redpen.db` | SQLite-файл для users/sessions/allowlist (стадия 1). Не должен лежать в `STORAGE_DIR` |
| `GOOGLE_CLIENT_ID` | (пусто) | OAuth client id для верификации Google ID-token. Пусто = `POST /api/auth/google` отвечает 503 |
| `ADMIN_EMAILS` | (пусто) | Email через запятую, получающие роль `admin` безусловно |
| `COOKIE_SECURE` | `true` | Флаг `Secure` у cookie `redpen_session`. `false` — для локальной разработки по http |

> `LOG_DIR` вынесен в конфиг, чтобы сервис можно было запускать и тестировать
> вне контейнера (где `/app` недоступен для записи).

## Запуск

Локально (без Docker):

```bash
pip install -r scripts/api/requirements-api.txt
cd scripts/api
STORAGE_DIR=./.data LOG_DIR=./.logs LOG_LEVEL=INFO python main.py   # слушает :8080
```

Через Docker Compose (сервис `api`, за прокси Caddy) — см. `docker-compose.yml`
и корневой `.env`. Данные хранятся в volume `redpen_data`, примонтированном к
`/var/redpen-data` (или к `STORAGE_DIR`).

## Эндпоинты

> 🔒 = требует сессию с ролью `editor`/`admin` + заголовок `X-CSRF-Token`
> (иначе `401`/`403`). ⛔ = требует роль `admin` (⛔🔒 дополнительно требует
> CSRF). Читающие эндпоинты остаются публичными. Роли: `viewer` (по умолчанию
> после входа через Google) `< editor < admin` — см. раздел «Роли» ниже.

Служебные:
- `GET /api/health` → `{"status":"ok"}`
- `GET /api/hello` → `{message, version, now}` (smoke-тест)
- ⛔ `GET /logs` → HTML-просмотр лога; ⛔ `GET /api/logs?lines=N` → JSON

Приём данных (inbox):
- 🔒 `POST /api/store` — сохраняет JSON-объект в `${STORAGE_DIR}/inbox/YYYYMMDD/<uuid>.json`.
  Ответ: `{"status":"stored","path":"inbox/YYYYMMDD/<uuid>.json"}`.
- 🔒 `POST /api/store-raw` — то же, плюс необязательные поля `bucket` и `pageId`.
  Путь: `${STORAGE_DIR}/inbox/YYYYMMDD[/bucket]/<uuid>.json`. Значение
  очищается (`sanitize_bucket`: только `[a-z0-9/_-]`, пробелы → `-`; для
  `pageId` дополнительно `:` и `.` → `-`; максимум 3 сегмента, 120 символов).
  При наличии обоих полей приоритет у `bucket`. Ответ содержит
  `{stored,id,dateDir,bucket,relPath,size}`.

Редактор аннотаций (данные в `${STORAGE_DIR}/<docId>/annotations/page_NNN.json`):
- `GET /api/editor/{docId}/{pageNum}` — вернуть страницу (создаёт и сохраняет
  `serverPageSha`, если его не было). `pageNum` — 1..999.
- 🔒 `POST /api/editor/{docId}/{pageNum}` — добавить/обновить аннотацию.
  Тело: `{annType, text, coords?[x,y], id?, clientPageSha?}`. Для
  `annType != "general"` можно передать целочисленные `coords`. Ответ:
  `{id, serverPageSha}`.
- 🔒 `PUT /api/editor/{docId}/{pageNum}/{annId}` — обновить аннотацию по id
  (если не найдена — добавляется как новая). То же тело/ответ.

Оптимистичная блокировка: если `clientPageSha` передан, не пуст и не
совпадает с текущим `serverPageSha` страницы — ответ `409`
`{"detail": "conflict", "serverPageSha": "<текущий>"}`. Если `clientPageSha`
не передан, запрос принимается (переходный режим, пишется предупреждение в лог).

Пересборка JSON из Markdown:
- 🔒 `POST /api/rebuild/{bookSlug}/annotations/{pageId}` — конвертирует
  `redpen-content/{bookSlug}/annotations/{pageId}.md` →
  `redpen-publish/{bookSlug}/annotations/{pageId}.json`. `bookSlug`:
  `[a-z0-9_-]+`; `pageId`: `page_NNN` (три цифры). 404, если Markdown нет.

Администрирование (allowlist редакторов):
- ⛔ `GET /api/admin/allowlist` → `{"allowlist": [{email, role, addedBy, addedAt}, …]}`
- ⛔🔒 `POST /api/admin/allowlist` `{email, role: "editor"|"admin"}` → upsert,
  возвращает обновлённый список
- ⛔🔒 `DELETE /api/admin/allowlist/{email}` → удаляет запись (404, если не было)

### Роли

Вход через Google доступен кому угодно, но роль определяется на сервере
(`resolve_role`, пересчитывается при каждом входе):
- `admin` — email в `ADMIN_EMAILS`;
- `editor` — email в таблице `editor_allowlist` (роль там же может быть `admin`);
- `viewer` — все остальные (может читать, не может писать).

Токен-вход (`EDITOR_TOKENS`) — dev-fallback, роль всегда `editor`.

Аутентификация (пользователи и сессии — в SQLite, `DB_PATH`):
- `POST /api/auth/login` `{token}` — dev-fallback, токены из `EDITOR_TOKENS`,
  роль всегда `editor` → `{userId, username}` + cookie `redpen_session`
- `POST /api/auth/google` `{credential}` — ID-token из Google Identity
  Services; верифицируется `google-auth` (audience = `GOOGLE_CLIENT_ID`,
  503 если не задан); роль считается через `resolve_role(email)` →
  `{userId, email, name, picture, role}` + cookie `redpen_session`
- `GET /api/auth/csrf` (требует сессию) → `{csrfToken}`, привязанный к сессии;
  отправляйте его в заголовке `X-CSRF-Token` на все 🔒-эндпоинты, кроме `login`
- `GET /api/auth/me` → `{userId, email, name, picture, role, username}`
  (401 без валидной сессии); `username` сохранён для совместимости с фронтом
- `POST /api/auth/logout` → удаляет сессию, очищает cookie, `{"ok": true}`

> Легаси: `GET /api/pages/{pageId}` (формат `{docId}_page_{NNN}`, например
> `medinsky11klass_page_006`) всё ещё работает. Старые эндпоинты
> `POST/PUT /api/pages/{pageId}/annotations` **закомментированы** —
> используйте `/api/editor/...`.

## Примеры

```bash
curl -s http://localhost:8080/api/health
curl -s http://localhost:8080/api/editor/medinsky11klass/7   # чтение, без сессии

# Запись требует сессию (см. EDITOR_TOKENS) + CSRF-токен, привязанный к ней:
curl -s -c /tmp/redpen.cookies -X POST http://localhost:8080/api/auth/login \
  -H 'Content-Type: application/json' -d '{"token":"<значение из EDITOR_TOKENS>"}'
CSRF=$(curl -s -b /tmp/redpen.cookies http://localhost:8080/api/auth/csrf | python3 -c 'import sys,json;print(json.load(sys.stdin)["csrfToken"])')
curl -s -b /tmp/redpen.cookies -X POST http://localhost:8080/api/store \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" -d '{"hello":"world"}'
curl -s -b /tmp/redpen.cookies -X POST http://localhost:8080/api/editor/medinsky11klass/7 \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" \
  -d '{"annType":"comment","text":"Текст","coords":[100,200]}'
```

## Тесты

Эндпоинты покрыты `tests/test_api.py` (через `fastapi.TestClient`, без запуска
сервера), `storage.py` — `tests/test_storage.py`. См. `tests/README.md`.
