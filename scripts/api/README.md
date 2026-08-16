# RedPen API

FastAPI-сервис для хранения входящих данных и редактирования аннотаций.
Каноническая реализация: `scripts/api` (`main.py`, `db.py`, `publisher.py`,
`storage.py`, `config.py`).

С этапа 2 аннотации редактора хранятся в SQLite (`db.py`, таблицы
`annotations`/`annotation_history`), а не в файлах. `publisher.py` рендерит
их в статические `<docId>/annotations/page_NNN.json` (тот же «голый массив»,
который читает просмотрщик) при каждой мутации — это единственное, что
пишется в `PUBLISH_DIR`. `storage.py` теперь отвечает только за inbox
(`/api/store*`); CLI `import_annotations.py`/`export_annotations.py`
переносят данные между файлами и БД (см. ниже).

## Конфигурация (переменные окружения)

| Переменная | Значение по умолчанию | Назначение |
|---|---|---|
| `STORAGE_DIR` | `/data` | Корень для inbox: `inbox/…` (`/api/store*`) |
| `LOG_DIR` | `/app/logs` | Каталог для файла лога `redpen-api.log` |
| `LOG_LEVEL` | `INFO` | Уровень логирования |
| `CORS_ALLOW_ORIGINS` | `_` (→ `*`) | Список origin через запятую; `_`/`*` = разрешить все |
| `EDITOR_TOKENS` | (пусто) | Токены личного входа: `token1:username1,token2:username2`. Пусто = вход по токену отключён |
| `DB_PATH` | `/var/redpen-db/redpen.db` | SQLite-файл: users/sessions/allowlist (стадия 1) + annotations/annotation_history (стадия 2). Не должен лежать в `STORAGE_DIR` |
| `PUBLISH_DIR` | (пусто) | Куда `publisher.py` пишет `<docId>/annotations/page_NNN.json`. Пусто = публикация отключена (тесты, dev без volume) |
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

Редактор аннотаций (канон — таблица `annotations` в `DB_PATH`; каждая мутация
республикует голый массив в `${PUBLISH_DIR}/<docId>/annotations/page_NNN.json`):
- `GET /api/editor/{docId}/{pageNum}` — вернуть страницу, рендер из БД
  (`{pageId, serverPageSha, annotations}`). `pageNum` — 1..999. Анонимно/для
  `viewer` возвращает только `status='published'`; для сессии `editor`/`admin`
  дополнительно включает черновики (`status='draft'`) с флагом `draft: true`
  в каждом элементе — черновики никогда не попадают в статический JSON.
- 🔒 `POST /api/editor/{docId}/{pageNum}` — добавить/обновить аннотацию.
  Тело: `{annType, text, coords?[x,y], id?, clientPageSha?, status?}`.
  `status` — `"draft"` или `"published"` (иначе `400`); если поле не
  передано, у существующей аннотации статус сохраняется, у новой —
  `"published"`. Можно передать целочисленные
  `coords`. Ответ: `{id, serverPageSha, published}` — `published` учитывает
  и результат записи в volume, и статус самой аннотации (`false` для
  черновиков — это не ошибка).
- 🔒 `PUT /api/editor/{docId}/{pageNum}/{annId}` — обновить аннотацию по id
  (если не найдена — создаётся новая). То же тело/ответ/семантика `status`.
- 🔒 `DELETE /api/editor/{docId}/{pageNum}/{annId}` — мягкое удаление
  (`status='deleted'`, остаётся в истории) + республикация. `404`, если
  аннотации нет или она уже удалена. Ответ: `{id, serverPageSha, published}`.

Оптимистичная блокировка: если `clientPageSha` передан, не пуст и не
совпадает с текущим `serverPageSha` страницы — ответ `409`
`{"detail": "conflict", "serverPageSha": "<текущий>"}`. Если `clientPageSha`
не передан, запрос принимается (переходный режим, пишется предупреждение в лог).

Кабинет (`/cabinet/`, стадия 3) — списки/история/статистика поверх той же
таблицы `annotations`/`annotation_history`:
- 🔒 `GET /api/annotations?docId&pageKey&annType&status&authorId&q&limit&offset`
  (роль `editor`/`admin`, CSRF не требуется — чтение) → `{items, total, limit, offset}`.
  `items[]` — как `_annotation_row_to_dict` + `authorName`/`authorEmail`
  (`null` для импортированных). Валидация: `docId`/`pageKey` — как в
  `/api/editor/...`; `status ∈ {published,draft,deleted}`;
  `annType ∈ {main,comment}`; `limit ≤ 200` (по умолчанию 50);
  `offset ≥ 0`; `len(q) ≤ 200` — иначе `400`.
- 🔒 `GET /api/history?docId&pageKey&annId&authorId&action&limit&offset`
  (та же защита) → `{items, hasMore, limit, offset}`; `items[].snapshot` —
  распарсенное состояние аннотации на момент записи.
- `GET /api/stats` (любая роль, включая `viewer`, требует только сессию) →
  `{"docs": [{"docId","published","draft","deleted"}, …], "recentActivity": […]}`.
- 🔒 `POST /api/history/{histId}/revert` — восстанавливает аннотацию в
  состояние из снапшота истории (включая его `status` — откат к записи
  `action='delete'` повторно удаляет) и республикует страницу. `404`, если
  записи нет. Ответ: `{annId, docId, pageNum, serverPageSha, published}`.
- ⛔ `GET /api/admin/users` → `{"users": [{id,email,name,pictureUrl,role,createdAt,lastLoginAt}, …]}`
  (без `google_sub`). Роли меняются существующим allowlist-API ниже.

Администрирование (allowlist редакторов, публикация):
- ⛔ `GET /api/admin/allowlist` → `{"allowlist": [{email, role, addedBy, addedAt}, …]}`
- ⛔🔒 `POST /api/admin/allowlist` `{email, role: "editor"|"admin"}` → upsert,
  возвращает обновлённый список
- ⛔🔒 `DELETE /api/admin/allowlist/{email}` → удаляет запись (404, если не было)
- ⛔🔒 `POST /api/admin/publish-all` → перепубликовать все страницы из БД в
  `PUBLISH_DIR` (`{"pages": N, "failed": M}`); то же самое выполняется
  автоматически при старте сервиса (самолечение volume после пересоздания
  контейнера/тома).

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
> `medinsky11klass_page_006`) всё ещё работает, тоже рендерится из БД.
> Используйте `/api/editor/...` для новых интеграций.

## CLI: перенос данных между файлами и БД (этап 2)

- `python scripts/api/import_annotations.py <source_dir> [--doc <docId>] [--dry-run] [--overwrite]` —
  разовый импорт существующих `<source_dir>/<docId>/annotations/page_*.json`
  (оба формата: голый массив и старый page-объект) в БД. По умолчанию
  идемпотентен (пропускает уже существующие `ann_id`); `--overwrite` обновляет
  их. Ничего не публикует — после импорта вызовите `publish-all` (или
  перезапустите сервис).
- `python scripts/api/export_annotations.py --to <dir> [--doc <docId>]` —
  обратный экспорт: пишет голые массивы из БД в `<dir>/<docId>/annotations/page_*.json`
  тем же рендером, что и `publisher.py`. Используется для синхронизации
  переносимого git-снапшота `redpen-publish` с БД.

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

Эндпоинты покрыты `tests/test_api.py` и `tests/test_auth.py` (через
`fastapi.TestClient`, без запуска сервера); БД аннотаций — `tests/test_db.py` /
`tests/test_annotations_db.py`; рендер/публикация — `tests/test_publisher.py`;
CLI — `tests/test_import_annotations.py` / `tests/test_export_annotations.py`.
Кабинет (стадия 3) — `tests/test_cabinet_db.py` (запросы в `db.py`) и
`tests/test_cabinet_api.py` (матрица прав anon/viewer/editor/admin на
`/api/annotations`, `/api/history`, `/api/stats`, `/api/history/{id}/revert`,
`/api/admin/users`). См. `tests/README.md`.
