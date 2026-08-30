# RedPen API

FastAPI-сервис для хранения входящих данных и редактирования замечаний.
Каноническая реализация: `scripts/api` (`main.py`, `db.py`, `publisher.py`,
`storage.py`, `config.py`).

С этапа 2 замечания редактора хранятся в SQLite (`db.py`, таблицы
`remarks`/`remark_history`), а не в файлах. При каждой мутации
`publisher.py` делает два шага: пишет «голый массив»
`<docId>/remarks/page_NNN.json` и перерисовывает
`<docId>/pages/<label>/index.html`.

Второй шаг обязателен: постраничный просмотрщик **не читает** JSON — он вообще
ничего не загружает (инвариант офлайна) и берёт замечания из инлайнового блока
`redpen-page-data` внутри HTML. JSON остался для внешних потребителей и
офлайн-архива; адреса из прежнего каталога `<docId>/annotations/` переадресует
nginx (301), сам каталог удалён в фазе 6 переименования 2026-08-30.
Оглавление и `sitemap.xml` перерисовываются только полной сборкой.

`storage.py` отвечает только за inbox
(`/api/store*`); CLI `import_remarks.py`/`export_remarks.py`
переносят данные между файлами и БД (см. ниже).

## Конфигурация (переменные окружения)

| Переменная | Значение по умолчанию | Назначение |
|---|---|---|
| `STORAGE_DIR` | `/data` | Корень для inbox: `inbox/…` (`/api/store*`) |
| `LOG_DIR` | `/app/logs` | Каталог для файла лога `redpen-api.log` |
| `LOG_LEVEL` | `INFO` | Уровень логирования |
| `CORS_ALLOW_ORIGINS` | `_` (→ `*`) | Список origin через запятую; `_`/`*` = разрешить все |
| `AGENT_TOKENS` | (пусто) | Токены входа агентов: `token1:agent1,token2:agent2`. Пусто = вход по токену отключён. `EDITOR_TOKENS` — прежнее имя, читается для совместимости |
| `DB_PATH` | `/var/redpen-db/redpen.db` | SQLite-файл: users/sessions/invites + remarks/remark_history/sections/agent_runs. Не должен лежать в `STORAGE_DIR` |
| `PUBLISH_DIR` | (пусто) | Куда `publisher.py` пишет `<docId>/remarks/page_NNN.json`. Пусто = публикация отключена (тесты, dev без volume) |
| `GOOGLE_CLIENT_ID` | (пусто) | OAuth client id для верификации Google ID-token. Пусто = `POST /api/auth/google` отвечает 503 |
| `IDENTITY_PEPPER` | (пусто) | **Обязателен.** Перец для `HMAC(перец, google_sub)`. Пусто = `POST /api/auth/google` отвечает 503. Только в `.env.secrets`, никогда в git и в БД — см. `docs/anonymity-model.md` |
| `BOOTSTRAP_INVITE_CODE` | (пусто) | Одноразовый код, дающий роль `admin` первому вошедшему на пустой базе. Заменил `ADMIN_EMAILS` |
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
> CSRF). Читающие эндпоинты остаются публичными. Роли:
> `viewer < editor < reviewer < admin` — см. раздел «Роли» ниже.

Служебные:
- `GET /api/health` → `{"status":"ok"}`
- `GET /api/hello` → `{message, version, now}` (smoke-тест)
- ⛔ `GET /logs` → HTML-просмотр лога; ⛔ `GET /api/logs?lines=N` → JSON

Приём данных (inbox). **Клиентов нет:** это наследие этапа 0, когда правки
складывались в файлы до появления SQLite-канона. Ни просмотрщик, ни `/app/`,
ни кабинет, ни content-sync сюда не обращаются; вместе с ними жив только ради
них модуль `storage.py`. Оставлены намеренно (решение 2026-08-30).
- 🔒 `POST /api/store` — сохраняет JSON-объект в `${STORAGE_DIR}/inbox/YYYYMMDD/<uuid>.json`.
  Ответ: `{"status":"stored","path":"inbox/YYYYMMDD/<uuid>.json"}`.
- 🔒 `POST /api/store-raw` — то же, плюс необязательные поля `bucket` и `pageId`.
  Путь: `${STORAGE_DIR}/inbox/YYYYMMDD[/bucket]/<uuid>.json`. Значение
  очищается (`sanitize_bucket`: только `[a-z0-9/_-]`, пробелы → `-`; для
  `pageId` дополнительно `:` и `.` → `-`; максимум 3 сегмента, 120 символов).
  При наличии обоих полей приоритет у `bucket`. Ответ содержит
  `{stored,id,dateDir,bucket,relPath,size}`.

Редактор замечаний (канон — таблица `remarks` в `DB_PATH`; каждая мутация
республикует голый массив в `${PUBLISH_DIR}/<docId>/remarks/page_NNN.json`):
- `GET /api/editor/{docId}/{pageNum}` — вернуть страницу, рендер из БД
  (`{pageId, serverPageSha, remarks}`). `pageNum` — 1..999. Анонимно/для
  `viewer` возвращает только `status='published'`; для сессии `editor`/`admin`
  дополнительно включает черновики (`status='draft'`) с флагом `draft: true`
  в каждом элементе. В анонимный ответ черновики не попадают. В статический
  `page_NNN.json` они, наоборот, попадают с 2026-08-15 — помеченные
  `"draft": true` и тегом `draft` (см. `publisher.py` и
  `docs/remark_specification.md`); просмотрщик скрывает их по умолчанию
  и раскрывает по `?tags=draft`.
- 🔒 `POST /api/editor/{docId}/{pageNum}` — добавить/обновить замечание.
  Тело: `{kind, text, coords?[x,y], id?, clientPageSha?, status?, tags?}`.
  `tags` — список строк; отсутствие поля означает «не трогать теги», `[]` —
  «очистить». Имена `draft`/`published`/`deleted` зарезервированы.
  `status` — `"draft"` или `"published"` (иначе `400`); если поле не
  передано, у существующей замечания статус сохраняется, у новой —
  `"published"`. Можно передать целочисленные
  `coords`. Ответ: `{id, serverPageSha, published}` — `published` учитывает
  и результат записи в volume, и статус самой замечания (`false` для
  черновиков — это не ошибка).
- 🔒 `PUT /api/editor/{docId}/{pageNum}/{remarkId}` — обновить замечание по id
  (если не найдена — создаётся новая). То же тело/ответ/семантика `status`.
- 🔒 `DELETE /api/editor/{docId}/{pageNum}/{remarkId}` — мягкое удаление
  (`status='deleted'`, остаётся в истории) + республикация. `404`, если
  замечания нет или она уже удалена. Ответ: `{id, serverPageSha, published}`.

Оптимистичная блокировка: если `clientPageSha` передан, не пуст и не
совпадает с текущим `serverPageSha` страницы — ответ `409`
`{"detail": "conflict", "serverPageSha": "<текущий>"}`. Если `clientPageSha`
не передан, запрос принимается, но в лог пишется предупреждение. Клиенты
редактора шлют его всегда: и экран страницы, и карточка.

Кабинет (`/cabinet/`, стадия 3) — списки/история/статистика поверх той же
таблицы `remarks`/`remark_history`:
- 🔒 `GET /api/remarks?docId&pageKey&kind&status&authorId&q&limit&offset`
  (роль `editor`/`admin`, CSRF не требуется — чтение) → `{items, total, limit, offset}`.
  `items[]` — как `_remark_row_to_dict` + `authorName` (псевдоним автора;
  `null` для импортированных). Дополнительные фильтры: `category` (один из семи
  слагов) и `categorySource` (`default`/`tags-backfill`/`agent`/`human`) — вход
  очереди приёмки. Валидация: `docId`/`pageKey` — как в
  `/api/editor/...`; `status ∈ {published,draft,deleted}`;
  `kind ∈ {major,minor}`; `limit ≤ 200` (по умолчанию 50);
  `offset ≥ 0`; `len(q) ≤ 200` — иначе `400`.
- 🔒 `GET /api/tags?docId` (роль `editor`/`admin`, чтение) →
  `{"tags": [{tag, count}, …]}` (по убыванию частоты, без удалённых замечаний) — словарь тегов для фильтра в кабинете.
- 🔒 `GET /api/history?docId&pageKey&remarkId&authorId&action&limit&offset`
  (та же защита) → `{items, hasMore, limit, offset}`; `items[].snapshot` —
  распарсенное состояние замечания на момент записи.
- `GET /api/stats` (любая роль, включая `viewer`, требует только сессию) →
  `{"docs": [{"docId","published","draft","deleted"}, …], "recentActivity": […]}`.
- 🔒 `POST /api/history/{histId}/revert` — восстанавливает замечание в
  состояние из снапшота истории (включая его `status` — откат к записи
  `action='delete'` повторно удаляет) и республикует страницу. `404`, если
  записи нет. Ответ: `{remarkId, docId, pageNum, serverPageSha, published}`.
- 🔒 `GET /api/sections?docId` (роль `editor` и выше, чтение) →
  `{"sections": [{sectionId, chapterId, chapterTitle, title, pageStart, pageEnd,
  counts: {total, published, draft, unclassified}, lastActivity}, …]}` — доска
  работ по параграфам. Заливается `scripts/api/import_sections.py` из
  `metadata.json`.
- ⛔ `GET /api/admin/users` → `{"users": [{id, kind, displayName, role,
  createdAt, lastLoginAt}, …]}`. Ни email, ни имени из Google, ни аватара —
  см. `docs/anonymity-model.md`.
- ⛔🔒 `POST /api/admin/users/{userId}/role` `{role}` → сменить роль
- ⛔🔒 `POST /api/admin/users/{userId}/retire` → отвязать участника от аккаунта
  (история сохраняется, связь с аккаунтом теряется)

Администрирование (приглашения, публикация):
- ⛔ `GET /api/admin/invites` → `{"invites": [{codeHash, role, note, createdBy,
  createdAt, expiresAt, usedAt, usedBy}, …]}`. Кодов здесь нет — только их хеши.
- ⛔🔒 `POST /api/admin/invites` `{role?, note?}` → `{code, invite, invites}`.
  **`code` возвращается ровно один раз**; передаётся человеку вне системы.
- ⛔🔒 `DELETE /api/admin/invites/{codeHash}` → отзывает неиспользованное
  приглашение (404, если не найдено или уже погашено)
- ⛔🔒 `POST /api/admin/publish-all` → перепубликовать все страницы из БД в
  `PUBLISH_DIR` (`{"pages": N, "failed": M}`); то же самое выполняется
  автоматически при старте сервиса (самолечение volume после пересоздания
  контейнера/тома).

### Роли

Лестница: `viewer` < `editor` < `reviewer` < `admin`.

- `viewer` — читает, не пишет;
- `editor` — пишет черновики и правит;
- `reviewer` — плюс к этому принимает чужие черновики и предложения категорий;
- `admin` — плюс приглашения, роли и служебные операции.

**Круг участников закрыт.** Роль выдаётся приглашением при первом входе и
хранится в `users.role`; она не пересчитывается при каждом входе, а меняется
явно (`POST /api/admin/users/{id}/role`). Прежний механизм — `ADMIN_EMAILS` +
таблица `editor_allowlist` по email — упразднён вместе с хранением email;
модель и обоснование — `docs/anonymity-model.md`, миграция —
`scripts/api/scrub_identities.py`.

Токен-вход (`AGENT_TOKENS`) — вход агентов: актор заводится с `kind='agent'`.

Аутентификация (пользователи и сессии — в SQLite, `DB_PATH`):
- `POST /api/auth/login` `{token}` — вход агента, токены из `AGENT_TOKENS`
  → `{userId, username, kind}` + cookie `redpen_session`
- `POST /api/auth/google` `{credential, invite?}` — ID-token из Google Identity
  Services; верифицируется `google-auth` (audience = `GOOGLE_CLIENT_ID`,
  503 если не задан). Из токена берётся **только** `sub`; он хешируется
  `HMAC(IDENTITY_PEPPER, sub)` (503, если перец не задан). Неизвестному
  участнику нужен `invite` — иначе `403 invite required`. →
  `{userId, role, kind, displayName}` + cookie `redpen_session`
- `GET /api/auth/csrf` (требует сессию) → `{csrfToken}`, привязанный к сессии;
  отправляйте его в заголовке `X-CSRF-Token` на все 🔒-эндпоинты, кроме `login`
- `GET /api/auth/me` → `{userId, role, kind, displayName, username}`
  (401 без валидной сессии); `username` — псевдоним либо «Участник №N»
- 🔒 `POST /api/auth/display-name` `{displayName}` → сменить псевдоним (≤60 симв.)
- 🔒 `POST /api/auth/leave` → покинуть проект: аккаунт отвязывается, сессии
  убиваются, история остаётся связной
- `POST /api/auth/logout` → удаляет сессию, очищает cookie, `{"ok": true}`

> Легаси: `GET /api/pages/{pageId}` (формат `{docId}_page_{NNN}`, например
> `medinsky11klass_page_006`) всё ещё работает, тоже рендерится из БД.
> Используйте `/api/editor/...` для новых интеграций.

## CLI: перенос данных между файлами и БД (этап 2)

- `python scripts/api/import_remarks.py <source_dir> [--doc <docId>] [--dry-run] [--overwrite]` —
  разовый импорт существующих `<source_dir>/<docId>/remarks/page_*.json`
  (оба формата: голый массив и старый page-объект) в БД. По умолчанию
  идемпотентен (пропускает уже существующие `remark_id`); `--overwrite` обновляет
  их. Ничего не публикует — после импорта вызовите `publish-all` (или
  перезапустите сервис).
- `python scripts/api/export_remarks.py --to <dir> [--doc <docId>]` —
  обратный экспорт: пишет голые массивы из БД в `<dir>/<docId>/remarks/page_*.json`
  тем же рендером, что и `publisher.py`. Используется для синхронизации
  переносимого git-снапшота `redpen-publish` с БД.

- `python scripts/api/backfill_tags.py <md_dir> [--doc <docId>] [--apply]` —
  подтягивает `tags`/`confidence` из markdown-черновиков к уже импортированным
  замечанием (`confidence: high` → тег `confidence:high`). По умолчанию только
  отчёт; запись — `--apply`.

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
  -d '{"kind":"comment","text":"Текст","coords":[100,200]}'
```

## Тесты

Эндпоинты покрыты `tests/test_api.py` и `tests/test_auth.py` (через
`fastapi.TestClient`, без запуска сервера); БД замечаний — `tests/test_db.py` /
`tests/test_remarks_db.py`; рендер/публикация — `tests/test_publisher.py`;
CLI — `tests/test_import_remarks.py` / `tests/test_export_remarks.py`.
Кабинет (стадия 3) — `tests/test_cabinet_db.py` (запросы в `db.py`) и
`tests/test_cabinet_api.py` (матрица прав anon/viewer/editor/admin на
`/api/remarks`, `/api/history`, `/api/stats`, `/api/history/{id}/revert`,
`/api/admin/users`). См. `tests/README.md`.
