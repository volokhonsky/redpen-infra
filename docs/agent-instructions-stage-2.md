# Инструкция для агента: Этап 2 (аннотации в SQLite + публикация) и адресация страниц

Два блока работ, выполняются в указанном порядке:

- **Часть A** — Этап 2 плана `docs/editor-improvement-plan.md`: SQLite как
  каноническое хранилище аннотаций + публикация статических JSON-снапшотов,
  чтобы правки редактора сразу попадали на живой сайт.
- **Часть B** — реализация `docs/page-addressing-proposal.md`: манифест страниц,
  метки A1/A2/1/2/3, новый параметр `?p=`, фикс латентного бага редактора и
  расширение ключа страницы в API.

Этапы 0–1 выполнены и задеплоены (см. `docs/deployment-log.md`, записи
2026-07-08): auth/роли/CSRF/409 работают, сессии и пользователи — в SQLite
(`scripts/api/db.py`).

Перед началом обязательно прочитай:

- `docs/editor-improvement-plan.md` — план (разделы 2–4) и **раздел «Жёсткое
  ограничение: сайт остаётся полностью статическим»**;
- `docs/page-addressing-proposal.md` — целиком (термины: файловый ключ,
  порядковый индекс, label; проблемы P1–P5);
- `scripts/api/main.py`, `db.py`, `storage.py`, `config.py` — текущий бэкенд
  (зависимости `require_user/require_csrf/require_editor/require_admin` уже есть);
- `content-sync/content_sync.py` — как публикуется статика (`publish_from_parent`:
  rsync git-клона → staging → `rsync -a --delete` → том `redpen_public`);
- `templates/js/main.js` и `templates/js/redpen-editor-bootstrap.js` — просмотрщик
  и клиент редактора;
- `tests/conftest.py`, `tests/test_api.py`, `tests/test_auth.py` — устройство тестов;
- память проекта: `redpen-deployment-model`, `redpen-storage-permissions-gotcha`.

## Ключевые факты, без которых легко ошибиться

1. **Формат опубликованных аннотаций — «голый» массив.** Файлы
   `redpen-publish/<docId>/annotations/page_NNN.json` содержат
   `[{id, text, annType, coords}, ...]` (у `general` — без `coords`). Именно этот
   формат читает просмотрщик. Page-объект `{pageId, serverPageSha, annotations}`,
   который пишет `storage.save_page`, — внутренний формат API, в статику он
   попадать не должен. После этапа 2: **в статику публикуем только голые массивы**,
   `serverPageSha` живёт в API-ответах и БД.
2. **Правки редактора сейчас не видны на сайте**: API пишет в примонтированную
   рабочую копию `./redpen-publish` (`STORAGE_DIR=/var/redpen-data`), а nginx
   раздаёт том `redpen_public`, который наполняет content-sync из GitHub
   (`rsync -a --delete` — затирает всё, что положили в том мимо него).
3. **API работает под uid 10001** (`app`), а content-sync — под root: всё, что
   content-sync кладёт в том, для API read-only. Права надо выдавать явно.
4. Существуют страницы с «нестандартными» номерами: `page_000.json`,
   `page_-01.json`. Валидация API (`_validate_page_num`: 1–999) их не принимает.
   Импорт (A2.3) обязан их сохранить; редактируемыми они станут в B.4.
5. `id` аннотаций уникальны только в пределах страницы (`ann-page7-1` и т.п.) —
   глобальный PRIMARY KEY по `id` делать нельзя.
6. **Три нумерации страниц** (см. proposal): файловая `page_NNN`, логическая
   `?page=N` (`phys = physicalStart + N - 1`), печатная книжная. Редактор сейчас
   шлёт в API **логический** номер — работает только пока `physicalStart=1` (P2).

## Целевая схема

```
Редактор ──POST/PUT/DELETE──▶ API ──▶ SQLite (канон: annotations + history)
   (ключ страницы = файловый)   │
                                └─▶ publisher: атомарная запись голого массива
                                    page_NNN.json в PUBLISH_DIR (том redpen_public)
                                    → правка видна на сайте сразу

Просмотрщик: metadata.json c манифестом pages[{file,label}] → пагинация A1,A2,1,2…
   адрес ?p=<label> (новый канон), ?page=N — легаси, работает по старой арифметике.

content-sync: публикует ВСЁ, КРОМЕ */annotations/ (их владелец теперь API);
git-репозиторий redpen-publish: офлайн-снапшот, обновляется export-скриптом из БД.
```

Статическое ограничение соблюдено: просмотрщик по-прежнему читает только файлы.

---

# Часть A — Этап 2: аннотации в SQLite + публикация

Порядок важен, каждый шаг — отдельный коммит с тестами.

### A2.1. Схема БД: annotations + annotation_history

В `db.py` (в `init_db()` — `CREATE TABLE IF NOT EXISTS`, миграции не нужны):

```sql
CREATE TABLE IF NOT EXISTS annotations (
  rowid_pk INTEGER PRIMARY KEY AUTOINCREMENT,
  ann_id TEXT NOT NULL,              -- строковый id из существующих файлов / srv-...
  doc_id TEXT NOT NULL,
  page_num TEXT NOT NULL,            -- строка как в имени файла: "006", "000", "-01"
  ann_type TEXT NOT NULL,
  text TEXT NOT NULL,
  coord_x INTEGER, coord_y INTEGER,  -- NULL для general
  status TEXT NOT NULL DEFAULT 'published',  -- published | deleted (drafts — этап 3)
  author_id INTEGER REFERENCES users(id),    -- NULL для импортированных
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(doc_id, page_num, ann_id)
);
CREATE INDEX IF NOT EXISTS idx_annotations_page ON annotations(doc_id, page_num);

CREATE TABLE IF NOT EXISTS annotation_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id TEXT NOT NULL,
  page_num TEXT NOT NULL,
  ann_id TEXT NOT NULL,
  action TEXT NOT NULL,              -- import | create | update | delete
  snapshot TEXT NOT NULL,            -- полный JSON аннотации ПОСЛЕ действия
  author_id INTEGER,
  created_at TEXT NOT NULL
);
```

Функции в `db.py` (стиль — как существующие, с `_lock`):
`list_page_annotations(doc_id, page_num, include_deleted=False)` (порядок —
по `rowid_pk`, стабильный), `get_annotation(doc_id, page_num, ann_id)`,
`upsert_annotation_db(...)`, `soft_delete_annotation(...)`,
`add_history(...)`, `list_pages(doc_id=None) -> [(doc_id, page_num)]` (DISTINCT —
все страницы, где есть хоть одна строка), `list_doc_ids()`.

Каждая мутация (create/update/delete/import) пишет строку в history в той же
транзакции.

Тесты: новый `tests/test_annotations_db.py` — upsert/soft-delete/история/
уникальность (doc, page, ann_id)/порядок выдачи.

### A2.2. Публикатор: `scripts/api/publisher.py`

Новый модуль:

- `config.py`: env `PUBLISH_DIR` (дефолт `""` = публикация отключена — важно
  для тестов и локального dev без тома).
- `render_page(doc_id, page_num) -> list[dict]` — голый массив в формате
  просмотрщика: `{"id", "text", "annType"}` + `"coords": [x, y]` только если
  координаты заданы. Только `status='published'`. Порядок стабильный.
- `compute_page_sha(rendered) -> str` — sha256 канонической сериализации
  (`ensure_ascii=False, separators=(",",":"), sort_keys=True`) — это новый
  `serverPageSha` (семантика для клиента не меняется).
- `publish_page(doc_id, page_num) -> bool` — если `PUBLISH_DIR` пуст → False.
  Атомарная запись (`tempfile.mkstemp` + `os.replace`, паттерн уже есть в
  `storage.py`) в `{PUBLISH_DIR}/{doc_id}/annotations/page_{page_num}.json`
  с `indent=2` (как текущие файлы). `os.makedirs(..., exist_ok=True)`.
  **Ошибка публикации не должна ронять запрос**: поймать, `logger.error`,
  вернуть False — данные уже в БД, том лечится publish-all.
- `publish_all() -> {"pages": N, "failed": M}` — по `list_pages()`.

Тесты: `PUBLISH_DIR` в tmp (добавь в `tests/conftest.py`
`os.environ.setdefault("PUBLISH_DIR", ...)`), проверить: файл — валидный JSON-массив,
у general нет `coords`, повторная публикация идемпотентна; `PUBLISH_DIR=""` →
False без исключений; sha детерминирован.

### A2.3. Импорт существующих аннотаций: `scripts/api/import_annotations.py`

CLI-скрипт (запускается внутри контейнера или локально):

```
python import_annotations.py <source_dir> [--doc <docId>] [--dry-run]
```

- Обходит `<source_dir>/<docId>/annotations/page_*.json` (все `<docId>`, либо один).
- `page_num` — строка из имени файла между `page_` и `.json` (сохраняет
  `000`, `-01` как есть).
- Поддерживает **оба формата**: голый массив и page-объект
  (`{"annotations": [...]}` — такие файлы могла записать текущая версия API
  в рабочую копию на сервере).
- Для каждой аннотации: без `id` — сгенерировать (`srv-import-<hex>`); coords
  нормализовать в int или NULL. `annType` отсутствует → `comment`.
- **Идемпотентность**: если (doc_id, page_num, ann_id) уже в БД — по умолчанию
  пропустить (счётчик skipped); флаг `--overwrite` обновляет текст/тип/координаты.
- history: action `import`, author_id NULL.
- Вывод: docs/pages/imported/skipped/errors. `--dry-run` — только отчёт.
- После импорта скрипт НЕ публикует (публикация — отдельным шагом).

Тесты: `tests/test_import_annotations.py` — оба формата, идемпотентность
повторного запуска, страница `-01`, отсутствие `id`.

### A2.4. Перевод editor-эндпоинтов на БД

В `main.py`:

- `GET /api/editor/{docId}/{pageNum}` → из БД:
  `{"pageId": f"{docId}_page_{NNN}", "serverPageSha": compute_page_sha(render_page(...)),
  "annotations": render_page(...)}`. Файлы больше не читает. (Поле
  `origW/origH` не возвращаем — клиент имеет fallback на naturalWidth/Height.)
- `POST /api/editor/{docId}/{pageNum}` (require_editor — уже висит):
  optimistic lock теперь сверяет `clientPageSha` с `compute_page_sha` текущего
  состояния БД (перенеси логику `_check_optimistic_lock` на новый sha; missing
  sha — по-прежнему принять с warning). Затем `upsert_annotation_db` (author_id
  = user["id"]) + history `create` + `publish_page`. Ответ — как раньше:
  `{"id", "serverPageSha"}` (sha — уже нового состояния) + добавь
  `"published": bool`.
- `PUT .../{annId}`: аналогично, history `update`. Если аннотации нет —
  создать (текущее поведение сохраняем), action `create`.
- **Новый** `DELETE /api/editor/{docId}/{pageNum}/{annId}` (require_editor):
  soft-delete + history `delete` + republish. 404 если нет или уже удалена.
  Ответ `{"id", "serverPageSha", "published"}`. (UI-кнопка — этап 4, сейчас
  только API.)
- `GET /api/pages/{pageId}` (легаси) → тот же рендер из БД, формат ответа
  сохранить.
- **Удалить** `POST /api/rebuild/{bookSlug}/annotations/{pageId}` — он писал
  md→json в эфемерный путь и противоречит канону БД (md-файлы после импорта —
  архив). Удали эндпоинт, импорт `annotation_converter` в main.py и тесты на него.
- `storage.py`: функции `page_path/load_page/save_page/upsert_annotation/
  update_annotation/compute_sha` больше не нужны — удали вместе с их тестами
  (`test_storage.py` подчистить; `sanitize_bucket`/`save_inbox` остаются —
  их используют /api/store*).

Тесты (`test_api.py` переработать): полный CRUD-цикл через API с проверкой,
что после каждой мутации файл в tmp-`PUBLISH_DIR` соответствует БД; 409 на
устаревший sha; DELETE → аннотация исчезла из GET и из файла; анонимный
DELETE → 401; viewer-роль → 403.

### A2.5. Admin-эндпоинт полной публикации

- `POST /api/admin/publish-all` (`require_admin_csrf`) → `publisher.publish_all()`,
  ответ `{"pages", "failed"}`.
- Вызов `publish_all()` также при старте приложения (в `on_startup`, если
  `PUBLISH_DIR` непуст и доступен) — самолечение тома после пересоздания/чисток.
  Ошибки — в лог, старт не блокировать.

Тест: viewer/editor → 403, admin → 200 и файлы на месте.

### A2.6. content-sync: не трогать annotations, выдать права

В `content-sync/content_sync.py`, функция `publish_from_parent`:

1. Ко **второму** rsync (staging → public) добавь `--exclude=/*/annotations/`
   (анкер от корня передачи; без `--delete-excluded` исключённые пути также
   защищены от удаления — это нам и нужно).
2. Для ясности добавь тот же exclude и первому rsync.
3. После rsync — обеспечить каталоги и права для API (content-sync работает
   под root):

   ```python
   for doc_dir in [p for p in public_dir.iterdir() if p.is_dir()]:
       ann = doc_dir / "annotations"
       ann.mkdir(exist_ok=True)
       subprocess.call(["chown", "-R", "10001:10001", str(ann)])
   ```

   (создаёт каталог для новых docId и чинит владельца у уже существующих
   root-овых файлов — одноразовая миграция происходит сама при первом publish).

Проверь `entrypoint.sh`/Dockerfile content-sync: если publish вызывается ещё
откуда-то — правка одна, в `publish_from_parent`.

Тестов на content-sync в репо нет — проверка руками на деплое (см. runbook A).

### A2.7. Экспорт снапшота для git / офлайн-дистрибуции

Git-репозиторий `redpen-publish` остаётся переносимым артефактом (флешки,
приложение). Теперь его аннотации — производные от БД:

- Новый скрипт `scripts/api/export_annotations.py`:
  `python export_annotations.py --to <dir>` — пишет голые массивы из БД
  (только published) в `<dir>/<docId>/annotations/page_*.json` тем же
  publisher-рендером. Запускается там, где есть БД (в контейнере:
  `docker compose exec api python scripts/api/export_annotations.py --to /var/redpen-data`
  — это примонтированная рабочая копия `redpen-publish`; дальше обычный
  git commit/push).
- `scripts/build_website.py`: конвертация md→json аннотаций при сборке
  **по умолчанию отключается** (md — архив; иначе сборка затрёт свежие
  экспортированные из БД файлы устаревшими). Добавь флаг
  `--annotations-from-md` для явного легаси-запуска. Найди в build_website.py
  вызовы annotation_converter и обойди их по флагу; убедись, что копирование
  прочих артефактов (`images/`, `text/`, шаблоны, index) не задевает
  `annotations/`.

Тесты: `test_build_website.py` — дефолтный запуск не трогает
`annotations/*.json`; с флагом — конвертирует (существующие тесты адаптировать).

### A2.8. Документация и конфиги

- `docker-compose.yml`: сервису `api` добавь том `redpen_public:/srv/public`
  (**без** `:ro`!) и env `PUBLISH_DIR=/srv/public`.
- `.env.sample`-ы: `PUBLISH_DIR` с комментарием.
- `scripts/api/README.md`: новые эндпоинты (DELETE, publish-all), удалённый
  rebuild, схема «БД — канон, статика — рендер».
- `docs/STATE_OVERVIEW.md`: обнови разделы про бэкенд и «Отправить».
- `docs/editor-improvement-plan.md`: отметь пункты этапа 2.

---

# Часть B — Адресация страниц (по `docs/page-addressing-proposal.md`)

Выполняется **после части A** (B.4 опирается на TEXT-`page_num` в БД).
Исключение: B.1 (фикс редактора) можно сделать в любой момент, в т.ч. до части A.

### B.1. Фикс латентного бага редактора (P2): файловый ключ вместо логического номера

Маленький самостоятельный коммит.

- `templates/js/main.js`: в `loadPage()` рядом с `currentPageId` пробрасывай
  в редактор файловый ключ:
  `window.RedPenEditor.state.page.pageKey = currentPageId.split('_')[1]`
  (строка `"006"`; поле `pageNum` оставь для совместимости, но новый код его
  не использует).
- `templates/js/redpen-editor-bootstrap.js`: всюду, где строится URL API
  (`saveAnnotationToServer`, лёгкий GET-сидинг sha из шага 0.6, DELETE в
  будущем), использовать `st.page.pageKey`; fallback на старое поведение,
  если `pageKey` пуст.
- Поведение сейчас не меняется (`physicalStart=1` → ключ совпадает с номером),
  но исчезает мина замедленного действия.
- Проверка: e2e-сценарий сохранения работает как раньше (smoke руками или
  playwright `editor_mode_tests.py`).

### B.2. Генератор манифеста страниц

Новый скрипт `scripts/generate_page_manifest.py`:

- Вход: каталог документа в `redpen-publish/<docId>` + правило нумерации из
  `redpen-content/<docId>/meta.json`, секция:

  ```json
  "pageNumbering": {
    "frontMatter": ["-01", "000"],
    "printedStartFile": "001",
    "printedStartNumber": 1
  }
  ```

  Если секции нет — генератор не пишет `pages` (документ остаётся в
  легаси-режиме) и завершает работу с предупреждением.
- Сканирует `images/page_*.png`, извлекает файловые ключи, сортирует как числа
  (int: `-1 < 0 < 1 < …`; служебные файлы вроде `202-afghanistan.jpeg`
  игнорировать по маске `page_*`).
- Присваивает label'ы: ключи из `frontMatter` в заданном порядке → `A1, A2, …`;
  начиная с `printedStartFile` → `printedStartNumber, +1, +2, …`.
- Валидация (fail при нарушении): каждый файл из `frontMatter` существует;
  каждый `page_*.png` получил label (или явно перечислен в опц. `ignore`);
  labels уникальны; непрерывность файловых ключей печатной части.
- Пишет секцию `pages: [{file, label, name?}]` в
  `redpen-publish/<docId>/metadata.json` (сохраняя остальные поля файла),
  обновляет `totalPages = len(pages)`.
- Встрой вызов в `build_website.py` (после копирования артефактов); отдельный
  запуск — вручную.

Тесты: `tests/test_page_manifest.py` — генерация для фикстуры с `-01/000/001…`,
уникальность, отсутствие секции → нет `pages`, ignore-маска, ошибки валидации.

### B.3. Просмотрщик: манифест + `?p=` с полным fallback'ом

Правки в `templates/js/main.js` (и точечно `mobile.js`):

- После загрузки metadata: `manifestMode = Array.isArray(metadata.pages) &&
  metadata.pages.length > 0`. **Если манифеста нет — ни одно из изменений ниже
  не активируется, поведение бит-в-бит текущее** (это главная гарантия).
- Внутренняя единица навигации — порядковый индекс `1..pages.length`
  (нынешний «логический номер»). `currentPageId = 'page_' + pages[i-1].file`.
- Резолюция стартового адреса (по приоритету):
  1. `?p=<label>` → поиск label в манифесте (регистронезависимо);
     не найден → дефолт;
  2. `?page=N` (легаси) → файл по СТАРОЙ арифметике
     (`physicalStart + N - 1` → ключ `NNN`) → индекс этого файла в манифесте;
     файла нет в манифесте → clamp индекса N;
  3. `#pageN` — как `?page=N` (легаси);
  4. дефолт: label `"1"`, если есть, иначе первый элемент манифеста.
- `buildPageUrl(i)` в manifest-режиме генерирует `?p=<label>` (сохраняя
  `editor=1`); `?page` из адреса убирается при первой навигации (pushState).
- Пагинация: `textContent = label` (окно ±2 по индексу, первый/последний —
  как сейчас); `goToPage()` принимает строку-label (trim, без регистра),
  fallback на число-как-индекс, если label не найден; `prevPage/nextPage` —
  по индексу.
- `clampPage` остаётся только для легаси-веток.
- `mobile.js`: проверь `updateMobilePagination` — если он рисует номера из
  `totalPages`, переведи на label'ы тем же принципом (в легаси-режиме — не трогать).
- Редактор получает `pageKey` из `currentPageId` (уже сделано в B.1) — в
  manifest-режиме это автоматически правильный файловый ключ.

Проверки (playwright в `tests/`, по образцу `annotation_position_tests.py`,
+ ручной чек-лист в runbook B):

- документ С манифестом: `?page=6` открывает тот же файл `page_006.png`, что
  и до изменения; `?p=A1` открывает `page_-01.png`; пагинация показывает
  `A1 A2 1 2 3…`; Prev с страницы «1» уходит на A2;
- документ БЕЗ манифеста (фикстура): вся навигация и URL ведут себя как сейчас;
- ноль запросов к API в просмотре в обоих режимах.

### B.4. API: расширение ключа страницы (закрывает P3)

- `_validate_page_num` → `_validate_page_key(key: str) -> Optional[str]`:
  принимает строку по маске `^-?\d{1,3}$`, возвращает **нормализованный
  файловый ключ**: неотрицательные → `zfill(3)` (`"6"`→`"006"`, `"000"`→`"000"`),
  отрицательные → `"-" + str(abs).zfill(2)` (`"-1"`→`"-01"`). Невалидное → None → 400.
- Применить в GET/POST/PUT/DELETE `/api/editor/...`; в БД ключ хранится
  нормализованным; publisher использует его в имени файла как есть.
- Легаси `GET /api/pages/{pageId}` — тот же нормализатор.
- Тесты: `"6"`≡`"006"` (одна и та же страница), `"000"` и `"-01"` проходят
  полный CRUD-цикл + публикацию, `"abc"`/`"1000"`/`"--1"` → 400.

### B.5. Мелочи и документация

- Корневой `redpen-publish/metadata.json` устарел (`totalPages: 10` — P4):
  проверь, кто его читает (grep по `metadata.json` в `templates/` и корневом
  `index.html`); если только доковский `main.js` per-doc — удали корневой файл
  из шаблонов/сборки; если используется корневым индексом — регенерируй в
  `build_website.py` честными данными.
- `docs/page-addressing-proposal.md`: отметь реализованные пункты.
- `docs/STATE_OVERVIEW.md`: раздел «Логика URL/режимов» — добавь `?p=`.
- `TODO`: пункт о нумерации — отметить выполненным (после деплоя).

---

# Runbook деплоя

Деплой меняет прод и включает одноразовую миграцию данных — **покажи пользователю
готовое локально состояние и дождись подтверждения перед этим разделом.**
Часть A деплоится и проверяется полностью, затем часть B (можно другим днём).

## Runbook A (этап 2)

1. **Бэкапы на сервере** (до любых изменений):
   ```bash
   ssh root@70.34.202.231
   cd /root/apps/redpen/infra
   tar czf /root/redpen-backup-$(date +%Y%m%d)-annotations.tgz redpen-publish/*/annotations
   docker compose exec api python -c "import sqlite3; sqlite3.connect('/var/redpen-db/redpen.db').execute(\"VACUUM INTO '/var/redpen-db/pre-stage2.db'\")"
   ```
2. **Свежесть данных**: `git -C redpen-publish status` — если есть локальные
   правки (это следы editor-записей старого API, они самые свежие) — НЕ
   затирать pull'ом; импорт заберёт их из файлов. Если рабочая копия чистая —
   сделай `git pull`, чтобы импорт увидел последние опубликованные версии.
   (Стэш от деплоя 0/1 уже разобран и удалён — см. deployment-log, резолюция
   2026-07-08.)
3. Скопировать код (scripts/api/*, content-sync/content_sync.py,
   docker-compose.yml) в `/root/apps/redpen/infra`, дописать `PUBLISH_DIR` в `.env`.
4. `docker compose up -d --build api content-sync`.
5. **Импорт**: сначала `--dry-run`, сверить счётчики с
   `find redpen-publish -name 'page_*.json' | wc -l`, затем боевой запуск:
   ```bash
   docker compose exec api python scripts/api/import_annotations.py /var/redpen-data --dry-run
   docker compose exec api python scripts/api/import_annotations.py /var/redpen-data
   ```
6. `docker restart redpen-content-sync-1` (перепубликация статики с новым
   exclude + chown), затем publish-all уже случился на старте api — проверь:
   ```bash
   docker compose exec api ls -la /srv/public/medinsky11klass/annotations | head
   curl -s https://medinsky.net/medinsky11klass/annotations/page_006.json | head -3
   ```
   (владелец файлов — app/10001, curl возвращает массив).
7. **E2E-проверка**: в браузере `?editor=1`, войти, изменить аннотацию,
   `curl` того же page-файла — правка видна немедленно; обычный просмотрщик
   показывает её после перезагрузки страницы.
8. **Снапшот в git**:
   ```bash
   docker compose exec api python scripts/api/export_annotations.py --to /var/redpen-data
   cd redpen-publish && git add -A '*/annotations' && git commit -m "Sync annotations from DB (stage 2 cutover)" && git push
   ```
9. **Бэкап БД по расписанию** (теперь в ней контент — обязательно):
   ```bash
   cat >/etc/cron.daily/redpen-db-backup <<'EOF'
   #!/bin/sh
   d=/root/backups/redpen-db; mkdir -p $d
   docker compose -f /root/apps/redpen/infra/docker-compose.yml exec -T api \
     python -c "import sqlite3,datetime; sqlite3.connect('/var/redpen-db/redpen.db').execute(\"VACUUM INTO '/var/redpen-db/backup.db'\")"
   docker cp $(docker compose -f /root/apps/redpen/infra/docker-compose.yml ps -q api):/var/redpen-db/backup.db \
     $d/redpen-$(date +%Y%m%d).db
   find $d -name 'redpen-*.db' -mtime +14 -delete
   EOF
   chmod +x /etc/cron.daily/redpen-db-backup && /etc/cron.daily/redpen-db-backup && ls -la /root/backups/redpen-db
   ```
10. Запись в `docs/deployment-log.md` по образцу записи 2026-07-08.

## Runbook B (адресация)

1. Бэкенд (B.4): scp `scripts/api/*` → `docker compose up -d --build api`;
   smoke: `curl https://api.medinsky.net/api/editor/medinsky11klass/006` ≡
   `.../6`; `.../000` и `.../-01` → 200.
2. Фронтенд: локально `python scripts/build_website.py --skip-tests`
   (генератор манифеста отработает внутри сборки) → просмотреть diff
   `metadata.json` → push `redpen-publish` → на сервере
   `docker restart redpen-content-sync-1`.
3. Ручной чек-лист в браузере:
   - старый URL `?page=6` открывает ту же страницу, что вчера;
   - `?p=A1` — обложка; пагинация `A1 A2 1 2 3…`; ввод «17» и «A2» в поле
     перехода работает;
   - `?editor=1` на странице `A1`: правка сохраняется и появляется в
     `annotations/page_-01.json` (curl);
   - мобильная вёрстка: пагинация с label'ами.
4. Запись в `docs/deployment-log.md`; отметить пункт в `TODO`.

---

# Критерии приёмки

## Часть A

- [ ] `pytest` зелёный; новые тесты: annotations DB, импорт (оба формата,
      идемпотентность), publisher, CRUD+DELETE через API с проверкой файлов,
      publish-all, build_website не трогает annotations.
- [ ] Правка через редактор видна на `https://medinsky.net/.../annotations/page_NNN.json`
      немедленно (без git-цикла).
- [ ] `docker restart redpen-content-sync-1` НЕ затирает аннотации в томе
      (проверить после шага A7 повторным curl).
- [ ] Пересоздание контейнера api (`docker compose up -d --force-recreate api`)
      самовосстанавливает статику (publish_all на старте).
- [ ] Просмотрщик без `?editor=1` — по-прежнему ноль запросов к API.
- [ ] История пишется на каждую мутацию (проверить `SELECT count(*) FROM
      annotation_history` до/после E2E-правки).
- [ ] Ежедневный бэкап БД создан и содержит таблицу annotations.
- [ ] Старые страницы `page_000` / `page_-01` импортированы и публикуются.

## Часть B

- [ ] Старые ссылки `?page=N` открывают те же файлы, что до внедрения
      (легаси-арифметика сохранена).
- [ ] `?p=A1` открывает обложку (`page_-01`); пагинация показывает
      `A1, A2, 1, 2, …`; поле перехода принимает label'ы.
- [ ] Документ без манифеста ведёт себя бит-в-бит как раньше (fallback).
- [ ] Редактор пишет в тот же файл, который отображается (ключ из
      `currentPageId`), включая страницы `A1`/`A2` (`-01`/`000`) — их CRUD
      работает через API после B.4.
- [ ] `"6"` и `"006"` в API — одна страница (нормализация ключа).
- [ ] Просмотр — по-прежнему ноль запросов к API в обоих режимах.

# Что НЕ входит (не делать)

- UI: кнопка удаления, списки аннотаций, toast'ы — этап 4.
- Кабинет, просмотр истории, откаты, черновики (status='draft') — этап 3
  (поле status уже заложено).
- Переименование файлов страниц — отвергнуто в proposal (раздел 5).
- Оглавление по `chapters` в UI — опциональный бонус, только если всё
  остальное готово и осталось время.
- Правка форматов md / annotation_converter — md объявлен архивом.
