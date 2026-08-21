# Структура проекта RedPen

Проект состоит из инфраструктурного репозитория и двух каталогов с данными.
Они независимы; **git submodule не используются** — используются соседние
каталоги `redpen-content` и `redpen-publish` (либо одноимённые внутри дерева).

## Обзор

1. **Инфраструктура (`redpen-infra`, этот репозиторий)** — скрипты обработки
   PDF, конвертер аннотаций, сборщик сайта, шаблоны фронтенда, API, тесты.
2. **Контент (`redpen-content`)** — исходные данные по документам.
3. **Публикация (`redpen-publish`)** — собранный статический сайт.

## Инфраструктура

- `scripts/`
  - `process_pdf.py` — оркестратор: извлечение изображений и текста, генерация
    **пустых** шаблонов аннотаций (встроена в скрипт), публикация данных
  - `extract_images.py` — изображения из PDF
  - `extract_text.py` — текст из PDF в JSON
  - `annotation_converter.py` — Markdown ↔ JSON для аннотаций
  - `make_grid_images.py` — картинки с координатной сеткой для агента-аннотатора
  - `generate_page_manifest.py` — манифест страниц (label'ы A1/A2/1/2…) в metadata.json
  - `build_website.py` — сборка и публикация сайта (md→json аннотаций — только
    по флагу `--annotations-from-md`; канон аннотаций — БД)
  - `publish_data.py` — копирование данных в целевой каталог
  - `api/` — FastAPI-сервис: `main.py` (эндпоинты, auth/роли/CSRF), `db.py`
    (SQLite: users/sessions/annotations/history), `publisher.py` (рендер статики
    из БД), `import_annotations.py` / `export_annotations.py` (миграции данных);
    см. `scripts/api/README.md`
- `templates/` — `document_index.html` (старый SPA, сегодня носитель режима
  редактора), `cabinet/` (страница кабинета), `css/`, `js/` (просмотрщик
  `page-view.js`, старый SPA, редактор, `redpen-auth.js`), `favicon.svg`.
  Постраничные страницы и оглавление генерирует `scripts/page_html.py`
- `tests/` — pytest-набор и опциональные e2e (см. `tests/README.md`)

> Историческая заметка: отдельный модуль `generate_annotations.py` удалён при
> переходе на аннотации в Markdown. Актуальный конвейер другой: черновики пишет
> агент-аннотатор (`docs/annotation-agent-prompt.md`), канон — SQLite, правки
> редактора идут через API. Конвертация md→JSON в `build_website.py` по
> умолчанию выключена (флаг `--annotations-from-md`), см. корневой `CLAUDE.md`.

## Контент (`redpen-content`)

Раскладка **по документам** (`<docId>` — например, `medinsky11klass`):

```
redpen-content/<docId>/
  annotations/        page_NNN.md  # опубликованные md-аннотации (архив-исходники)
  annotations_draft/  page_NNN.md  # черновики агента-аннотатора (~~~meta-формат)
                      _report_*.md, _check_*.md, _typical_comments.md  # служебные
  images/             page_NNN.png # изображения страниц
  images_with_grid/   page_NNN.png # то же с координатной сеткой (для аннотатора)
  text/               page_NNN.json# извлечённый текст (сдвиг нумерации +1!)
  illustrations/                   # доп. иллюстрации (опционально)
  paragraphs_list.txt              # список параграфов (задания аннотатору)
  meta.json                        # метаданные (title, pageNumbering для манифеста)
```

## Публикация (`redpen-publish`)

Готовый к развёртыванию статический сайт:

```
redpen-publish/
  index.html                       # выбор документа (генерируется)
  css/  js/  favicon.svg           # общие ассеты
  <docId>/
    index.html                     # страница документа (из document_index.html)
    metadata.json                  # копия meta.json
    annotations/  page_NNN.json    # аннотации в JSON (из Markdown)
    images/                        # изображения + иллюстрации
    text/         page_NNN.json    # текст
```

## Рабочий процесс

1. **Обработка PDF**: `process_pdf.py` извлекает изображения и текст, создаёт
   пустые шаблоны аннотаций; `make_grid_images.py` готовит картинки с сеткой.
2. **Аннотирование**: агент-аннотатор (промпт `docs/annotation-agent-prompt.md`,
   один запуск = один параграф) пишет черновики в
   `redpen-content/<docId>/annotations_draft/`; ручные правки — через
   веб-редактор (`?editor=1`) и кабинет (`/cabinet/`).
3. **Канон аннотаций — SQLite на сервере** (`scripts/api/db.py`): черновики
   импортируются `import_annotations.py` (аддитивно), правки редактора пишутся
   туда напрямую; `publisher.py` на каждое изменение рендерит статические
   `annotations/page_NNN.json` прямо в том, который раздаёт nginx.
4. **Сборка** (`build_website.py`) отвечает за всё остальное: шаблоны, картинки,
   текст, манифест страниц, индексные страницы, `/cabinet/`. Конвертация md→JSON
   аннотаций — только по флагу `--annotations-from-md` (легаси/бутстрап).
5. **Публикация/развёртывание**: push в `redpen-publish` → content-sync
   раскладывает статику (кроме `*/annotations/` — их владелец API);
   git-снапшот аннотаций синхронизируется из опубликованного рендера
   (процедура — `docs/deployment-log.md`, 2026-07-10). Прод — за Caddy,
   API — отдельный сервис (`docker-compose.yml`).

## Настройка разработки

```bash
cd redpen-infra
pip install -r scripts/requirements.txt

# сборка в отдельный каталог, без тестов и push:
python scripts/build_website.py --target-dir ./out --skip-tests --skip-push
```
