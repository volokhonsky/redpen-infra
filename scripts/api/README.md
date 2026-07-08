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

> Эндпоинты, помеченные 🔒, требуют аутентифицированную сессию (cookie
> `redpen_session`) — иначе `401`. Читающие эндпоинты остаются публичными.

Служебные:
- `GET /api/health` → `{"status":"ok"}`
- `GET /api/hello` → `{message, version, now}` (smoke-тест)
- 🔒 `GET /logs` → HTML-просмотр лога; 🔒 `GET /api/logs?lines=N` → JSON

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
  Тело: `{annType, text, coords?[x,y], id?}`. Для `annType != "general"` можно
  передать целочисленные `coords`. Ответ: `{id, serverPageSha}`.
- 🔒 `PUT /api/editor/{docId}/{pageNum}/{annId}` — обновить аннотацию по id
  (если не найдена — добавляется как новая). Ответ: `{id, serverPageSha}`.

Пересборка JSON из Markdown:
- 🔒 `POST /api/rebuild/{bookSlug}/annotations/{pageId}` — конвертирует
  `redpen-content/{bookSlug}/annotations/{pageId}.md` →
  `redpen-publish/{bookSlug}/annotations/{pageId}.json`. `bookSlug`:
  `[a-z0-9_-]+`; `pageId`: `page_NNN` (три цифры). 404, если Markdown нет.

Аутентификация (сессии в памяти; токены — из `EDITOR_TOKENS`):
- `POST /api/auth/login` `{token}` → `{userId, username}` + cookie `redpen_session`
- `GET /api/auth/csrf` (требует сессию) → `{csrfToken}`, привязанный к сессии;
  отправляйте его в заголовке `X-CSRF-Token` на все 🔒-эндпоинты, кроме `login`
- `GET /api/auth/me` → текущий пользователь (401 без валидной сессии)
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
