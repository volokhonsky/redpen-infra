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
| `DB_PATH` | `/var/redpen-db/redpen.db` | SQLite-файл: users/sessions/invites + remarks/remark_history/remark_tags/remark_ratings/remark_notes/sections/agent_runs + rating_pool/survey_respondents/survey_sessions/survey_answers. Не должен лежать в `STORAGE_DIR` |
| `RATE_LIMIT_PER_MINUTE` / `RATE_LIMIT_BURST` | `240` / `60` | Общее ведро для `/api/*` (кроме `/api/health`) |
| `RATE_LIMIT_AUTH_PER_MINUTE` / `RATE_LIMIT_AUTH_BURST` | `12` / `6` | Жёсткое ведро для входа и записи опроса: `/api/auth/google`, `/api/auth/login`, `/api/survey/session`, `/api/survey/ratings` |
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

> 🔒 = требует сессию с ролью `editor`/`admin` + заголовок
> `X-CSRF-Token` (иначе `401`/`403`). 📖 = та же роль, но чтение: CSRF не
> нужен. ⛔ = требует роль `admin` (⛔🔒 дополнительно требует CSRF).
> Роли: `viewer < editor < admin` — см. раздел «Роли» ниже.
>
> **Публичны только** `GET /api/health`, `GET /api/hello`,
> `GET /api/pages/{pageId}` и `GET /api/editor/{docId}/{pageNum}` (последний
> отдаёт черновики лишь редакторской сессии). Всё остальное чтение —
> редакторское: до 2026-08-31 легенда обещала обратное, и это была ошибка
> описания, а не кода.

Служебные:
- `GET /api/health` → `{"status":"ok"}`
- `GET /api/hello` → `{message, version, now}` (smoke-тест)
- ⛔ `GET /logs` → HTML-просмотр лога; ⛔ `GET /api/logs?lines=N` → JSON

Приём данных (inbox). **Клиентов нет:** это наследие этапа 0, когда правки
складывались в файлы до появления SQLite-канона. Ни просмотрщик, ни `/work/`,
ни content-sync сюда не обращаются; вместе с ними жив только ради
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
  «очистить». Имена `draft`/`published`/`archived`/`deleted` зарезервированы.
  `status` — `"draft"` или `"published"` (иначе `400`); если поле не
  передано, у существующей замечания статус сохраняется, у новой —
  `"published"`. Можно передать целочисленные
  `coords`. Ответ: `{id, serverPageSha, published}` — `published` учитывает
  и результат записи в volume, и статус самой замечания (`false` для
  черновиков — это не ошибка).
- 🔒 `PUT /api/editor/{docId}/{pageNum}/{remarkId}` — обновить замечание по id
  (если не найдена — создаётся новая). То же тело/ответ/семантика `status`.
- 🔒 `DELETE /api/editor/{docId}/{pageNum}/{remarkId}` — **в архив**
  (`status='archived'`, ревизия `archive`, остаётся в истории) + республикация.
  `404`, если замечания нет или оно уже в архиве. Ответ:
  `{id, serverPageSha, published}`. Обратимо: `PATCH .../status`
  (`draft`/`published`) достаёт из архива, diff подписывает `restore`.
- 🔒 `DELETE /api/editor/{docId}/{pageNum}/{remarkId}/purge` — **удалить
  навсегда** (роль `admin`, CSRF). Необратимо: стирает строку `remarks`, теги,
  оценки, комментарии, членство в пуле, ответы опроса и всю историю; остаётся
  одна запись `remark_history` с `action='purge'` (кто и когда + снимок головы).
  Применимо при любом статусе; страница перепубликовывается. `404`, если
  замечания нет. Ответ: `{id, serverPageSha, published}`.

Узкие операции — правят одно поле и записывают в журнал ровно то изменение,
которое произошло. `serverPageSha` не требуют: оптимистическая блокировка
защищает текст и координаты, где двое правят одно и то же.
- 🔒 `PATCH /api/editor/{docId}/{pageNum}/{remarkId}/status` `{status, summary?}`
- 🔒 `PATCH /api/editor/{docId}/{pageNum}/{remarkId}/category` `{category|null, summary?}`
- 🔒 `PATCH /api/editor/{docId}/{pageNum}/{remarkId}/tags` `{tags, summary?}` —
  полная замена; `[]` очищает.

Оптимистичная блокировка: если `clientPageSha` передан, не пуст и не
совпадает с текущим `serverPageSha` страницы — ответ `409`
`{"detail": "conflict", "serverPageSha": "<текущий>"}`. Если `clientPageSha`
не передан, запрос принимается, но в лог пишется предупреждение. Клиенты
редактора шлют его всегда: и экран страницы, и карточка.

Рабочее место (`/work/`) — списки, история и статистика поверх той же
таблицы `remarks`/`remark_history`. До 2026-08-31 эти ручки делили между собой
два приложения, `/app/` и `/cabinet/`; теперь они питают одни и те же экраны:
- 📖 `GET /api/remarks?docId&pageKey&kind&status&authorId&q&tag&category&categorySource&section&inPool&includeArchived&limit&offset`
  → `{items, total, limit, offset}`.
  `items[]` — как `_remark_row_to_dict` + `authorName` (псевдоним автора;
  `null` для импортированных) + `tags` + **`inPool`/`poolAnswers`** (лежит ли
  замечание в пуле опроса и сколько ответов на него получено). Фильтры:
  `tag` — один тег; `category` (один из семи слагов) и `categorySource`
  (`default`/`tags-backfill`/`agent`/`human`) — вход очереди приёмки;
  `section` — параграф (выводится из диапазона страниц, отдельной колонки
  нет); `inPool=true|false` — членство в пуле опроса. **Архив в выдачу не
  попадает**, пока его не спросят: `includeArchived=true` подмешивает
  `status='archived'`, либо явный `status=archived` отдаёт только его. Валидация:
  `docId`/`pageKey` — как в `/api/editor/...`; `status ∈ {published,draft,archived}`;
  `kind ∈ {major,minor}`; `limit ≤ 200` (по умолчанию 50);
  `offset ≥ 0`; `len(q) ≤ 200` — иначе `400`.
- 📖 `GET /api/remarks/{docId}/{pageKey}/{remarkId}` → `{remark, section}` —
  одно замечание целиком, вход карточки в редакторе (список для этого не
  годится: карточке нужен адрес, а не страница выдачи). `remark` несёт те же
  поля, что элемент списка, включая `inPool`/`poolAnswers`.
- 📖 `GET /api/tags?docId` (роль `editor`/`admin`, чтение) →
  `{"tags": [{tag, count}, …]}` (по убыванию частоты, без архивных замечаний) — словарь тегов для фильтра в кабинете.
- 📖 `GET /api/history?docId&pageKey&remarkId&authorId&action&changed&limit&offset`
  → `{items, hasMore, limit, offset}`; `items[].snapshot` —
  распарсенное состояние замечания на момент записи. `action` фильтрует по
  происхождению записи (`create`/`update`/`import`/…), `changed` — по составу
  изменения (токен `remark_actions.ACTIONS`): это разные вопросы, «кто это
  записал» и «что при этом изменилось».
- `GET /api/stats` (любая роль, включая `viewer`, требует только сессию) →
  `{"docs": [{"docId","published","draft","archived"}, …], "recentActivity": […]}`.
- 🔒 `POST /api/history/{histId}/revert` — восстанавливает замечание в
  состояние из снапшота истории (включая его `status` — откат к записи
  `action='delete'` повторно удаляет) и республикует страницу. `404`, если
  записи нет. Ответ: `{remarkId, docId, pageNum, serverPageSha, published}`.
- 📖 `GET /api/sections?docId` →
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

Оценки и комментарии (рабочие данные редактора). **Ни один из этих маршрутов
не публикует ничего:** оценки и комментарии в статику не попадают и ревизиями
не становятся, а сводятся только на чтении — `docs/remark_specification.md`,
раздел «Оценки и комментарии».
- 🔒 `GET /api/rating-scales` (чтение) → `{"scales": [{name, title, hint, min,
  max, options}, …]}` — единственный источник, из которого интерфейсы узнают о
  шкалах. `options` (список `{value, label}`) есть у шкал, где цифра сама по
  себе непонятна: у `admissibility` это «Нет»/«Да», диапазон 1..2. Зашивать
  диапазоны в JS нельзя — они принадлежат шкале.
- 🔒 `GET /api/remarks/{docId}/{pageKey}/{remarkId}/ratings` (чтение) →
  `{summary: {scale: {scale, count, average, mine, myNote}}, items: [...]}`
- 🔒 `PUT .../ratings/{scale}` `{value, note?}` → `{rating, summary}`.
  `value` проверяется по границам своей шкалы (400 вне диапазона);
  `note` ≤ 500 символов. Одна оценка на (замечание, шкала, участник):
  повторная перезаписывает прежнюю, журнала изменения оценок нет.
- 🔒 `DELETE .../ratings/{scale}` → `{summary}` (404, если оценки не было)
- 🔒 `GET .../notes` (чтение) → `{items, open}`;
  🔒 `POST .../notes` `{body, parentId?}` → `{note}` (ответ ровно в один уровень)
- 🔒 `PATCH /api/notes/{noteId}` `{body}` либо `{resolved}` → `{note}`.
  Править тело может автор или админ, закрыть тред — любой редактор.
- 🔒 `DELETE /api/notes/{noteId}` → мягкое удаление: строка остаётся ради
  связности треда, тело затирается.
- 🔒 `GET .../timeline?limit=100` (чтение) → `{items}` — ревизии, оценки
  участников, ответы опроса и комментарии одним списком, новые сверху. У
  элемента `kind ∈ {revision, rating, note}`; у оценок `source ∈
  {editor, survey}` — голос участника и голос с улицы не смешиваются.

Опрос (`/survey/`) — оценка замечаний людьми вне закрытого круга. Заход
опознаётся заголовком **`X-Survey-Token`**, а не кукой: кука потребовала бы
CSRF, а токен, недоступный чужому сайту, снимает вопрос. Респондент — это
псевдоним (`survey_respondents`), заход — сессия (`survey_sessions`); одно имя
— один респондент с любым числом заходов. Модель субъекта —
`docs/anonymity-model.md`, раздел «Анонимные респонденты опроса».
- `POST /api/survey/session` `{pseudonym}` → `{respondentId, sessionId,
  pseudonym, author, token, createdAt, returning, questions}`. **Единственный
  маршрут, заводящий субъекта без приглашения.** Псевдоним: 2..32 символа, без
  `:` (иначе 400) — подпись `anonymous:<псевдоним>` собирается на чтении и
  подделке не поддаётся. Респондент не заводится в `users`. `returning` —
  под этим именем уже отвечали раньше; пароля у псевдонима нет, сравнение
  посимвольное. `questions` — шкалы и открытые вопросы одним списком, у
  каждого `answer` (`"value"` — кнопки, `"text"` — поле).
- `GET /api/survey/batch?limit=10` (токен) → `{items: [{docId, pageNum,
  remarkId}], remaining, tail, author, questions}` — случайные замечания из
  пула, которых **этот псевдоним** ещё не оценивал (не больше 50).
  `tail = remaining <= limit` — выдан весь остаток, дальше ничего нет.
  Отдаются одни адреса: текст опросник берёт с читательской страницы
  (`?only=<id>`).
- `PUT /api/survey/ratings` `{docId, pageKey, remarkId, interest?, importance?,
  admissibility?, comment?}` (токен) → `{saved, docId, pageNum, remarkId}`. Все
  вопросы одним вызовом; хотя бы одна оценка обязательна (пустой ответ или один
  `comment` — 400); замечание вне пула — 403. `comment` — открытый ответ с
  семантикой тегов: ключа нет — не трогать, `""` — стереть, длиннее предела
  (`rating_scales.OPEN_QUESTIONS`) — 400.
Пулом ведает **редактор**, сводкой ответов — **админ** (с 2026-08-31).
Вынести замечание на оценку решает тот, кто его читает, — это обычная
редакторская работа; агрегированные мнения анонимных респондентов ближе к
персональным данным, чем к содержанию разбора.
- 📖 `GET /api/survey/pool?docId&limit&offset` → `{items, total, limit, offset}`
  (с текстом замечания и числом полученных ответов)
- 🔒 `POST /api/survey/pool` `{docId, pageKey, remarkId}` → `{item}`
  (повтор — не ошибка)
- 🔒 `DELETE /api/survey/pool/{docId}/{pageKey}/{remarkId}` → `{removed}`.
  Ответы при этом остаются: снять вопрос с раздачи и стереть ответы — разные
  действия.
- ⛔ `GET /api/survey/results?docId&limit&offset` → `{items, total, limit,
  offset}`; у элемента `interest`/`importance` — `{count, average}`,
  `admissibility` — `{yes, no}`. Расклад, а не среднее: среднее по «да или
  нет» ничего не сообщает. Голос сводится к псевдониму: из нескольких заходов
  берётся последний ответ, `raters` считает псевдонимы. Открытые ответы —
  `commentsN` и `comments: [{author, question, text, createdAt}]`; их, в
  отличие от цифр, показывают все: текст не усредняется.
- ⛔ `GET /api/survey/respondents?limit&offset` → `{items, total, limit, offset}`;
  у элемента `{id, pseudonym, author, createdAt, lastSeenAt, sessions, answers,
  remarks}`. Токена нет ни в каком виде — в базе от него только хеш.
- ⛔ `GET /api/survey/respondents/{id}/sessions` → `{items: [{id, createdAt,
  lastSeenAt, answers, remarks}]}`, свежие сверху.
- ⛔ `DELETE /api/survey/sessions/{id}` → `{deleted: {answers, sessions,
  respondentId}}` — стереть один заход с его ответами; псевдоним и прочие его
  заходы остаются, токен стёртого захода перестаёт работать (401).
- ⛔ `DELETE /api/survey/respondents/{id}` → `{deleted: {answers, sessions,
  pseudonym}}` — псевдоним целиком. Обе необратимы, ревизиями не становятся и
  в `remark_history` не пишутся.

`/api/survey/session` и `/api/survey/ratings` живут в жёстком ведре
рейт-лимита (`AUTH_PATHS`) — это единственные анонимные пути записи.

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

Лестница: `viewer` < `editor` < `admin`. Это единственная ось разграничения:
рабочее место одно (`/work/`), и роль решает, какие его экраны показывать.

- `viewer` — приглашён, права ещё не выданы: своя учётная запись и больше
  ничего. Все данные разбора закрыты;
- `editor` — весь разбор: замечания, черновики, публикация, категории, теги,
  очередь приёмки, история и откат, оценки, тред и **пул опроса**;
- `admin` — плюс приглашения, роли участников и отставка, перепубликация,
  логи и **сводка ответов опроса**.

Роль `reviewer` **упразднена 2026-08-31**. Она обещала отделить «принимать
чужое» от «писать», но ни одна ветка кода этого не делала: в API она была
равна `editor`, а кабинет её не пускал вовсе. Существующие строки переводит
`db._retire_reviewer_role` при старте; запросить эту роль в приглашении или в
смене роли теперь — `400`.

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
  -d '{"kind":"minor","text":"Текст","coords":[100,200]}'
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
