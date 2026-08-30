# Репозиторий инфраструктуры RedPen

Основной репозиторий проекта RedPen: обрабатывает PDF-учебники и публикует
статический сайт, показывающий страницы с замечаниями.

**Названия.** `RedPen` — внутреннее имя движка (репозитории, код, служебные
идентификаторы вроде `REDPEN_API_BASE`). Публичное название сайта —
**Мединский.нет**, подпись: «Антимифы к единому учебнику. Проверяем избыточные
победы». Прежний публичный бренд «Красной ручкой» не используется. В разметке
имя сайта появляется в шапке страниц (`scripts/page_html.py`), в генераторе
титульной (`scripts/build_website.py`), на страницах блога (`scripts/blog.py`)
и в шапке кабинета и редактора (`templates/cabinet/index.html`,
`templates/app/index.html`).

## Ключевое ограничение: сайт полностью статический

Собранный сайт (`redpen-publish`) — самодостаточный набор файлов с относительными
путями. Это требование продукта, а не случайность реализации: сайт должен
открываться с любого статического хостинга, с флешки, офлайн, или встраиваться
как контент в простое приложение. Поэтому:

- **просмотрщик никогда не обращается к API** — ни `fetch`, ни
  `XMLHttpRequest`, ни одного внешнего адреса. На это есть проверка
  (`tests/manual/page_view_markers.py`);
- замечания приезжают к читателю **не JSON-файлом, а инлайновым блоком**
  `<script type="application/json" id="redpen-page-data">` внутри самой
  страницы. Поэтому `publisher.py` на каждую правку обязан и переписать
  `remarks/page_NNN.json`, и перерисовать `pages/<label>/index.html`;
- API и любые серверные хранилища обслуживают исключительно редактор (`/app/`)
  и кабинет (`/cabinet/`);
- любая доработка, добавляющая просмотрщику сетевую зависимость, нарушает
  это ограничение и не должна приниматься.

## Три репозитория

Независимы; **git submodule не используются** — рядом (или внутри дерева)
лежат каталоги `redpen-content` и `redpen-publish`.

1. **`redpen-infra`** (этот) — скрипты обработки PDF, конвертер замечаний,
   сборщик сайта, шаблоны фронтенда, API, тесты, документация.
2. **`redpen-content`** — исходные данные по документам.
3. **`redpen-publish`** — собранный статический сайт, он же офлайн-артефакт.

## Структура репозитория

- `scripts/` — Python-скрипты обработки и сборки
  - `process_pdf.py` — оркестратор конвейера PDF: изображения → текст →
    пустые шаблоны замечаний → публикация. Шаг генерации шаблонов встроен в
    сам скрипт; отдельного `generate_remarks.py` больше нет
  - `extract_images.py` — изображения страниц из PDF
  - `extract_text.py` — текст из PDF в JSON (`page_NNN.json` с блоками)
  - `make_grid_images.py` — картинки с координатной сеткой для агента-аннотатора
  - `make_agent_prompt.py` — задание агенту-аннотатору из шаблона
  - `remark_converter.py` — конвертация замечаний Markdown ↔ JSON
  - `remark_kinds.py` — виды замечаний (`major`/`minor`) и их прежние имена
  - `annotation_categories.py` — семь категорий приёмов (имя историческое:
    модуль про категории, а не про замечания)
  - `page_html.py` — генератор страниц читателя и оглавления документа
  - `generate_page_manifest.py` — манифест страниц (метки A1/A2/1/2…) в metadata.json
  - `page_sections.py`, `chapters.py` — параграфы и главы
  - `build_website.py` — собирает и публикует сайт
  - `blog.py` — статический блог из `content/blog/*.md` (собственный
    минимальный markdown, без зависимостей и без обращений к сети)
  - `sitemap.py` — `sitemap.xml` и `robots.txt`
  - `publish_data.py` — копирует изображения/текст в целевой каталог
  - `make_offline_bundle.py` — офлайн-архив книги одним zip
  - `analytics.py`, `matomo_export.py`, `log_files.py`, `ops/` — аналитика по
    access-логам и операционные задачи крона
  - `nightly_snapshot.py` — ночной снапшот БД в git
  - `api/` — FastAPI-сервис (см. `scripts/api/README.md`)
- `templates/` — шаблоны статического сайта
  - `js/` — просмотрщик `page-view.js`, общий модуль маркеров
    `redpen-markers.js`, таблица категорий `redpen-categories.js`,
    переадресация со старых адресов `legacy-page-redirect.js`, общий модуль
    авторизации `redpen-auth.js`
  - `app/` — редактор (карточка замечания, доска параграфов, очередь приёмки,
    экран страницы со сканом)
  - `cabinet/` — кабинет
  - `css/`, `favicon.svg`
- `content/blog/*.md` — исходники записей блога (frontmatter `title`/`date`/
  `summary` + markdown-тело; имя файла задаёт slug)
- `tests/` — тесты (pytest + браузерные сценарии в `tests/manual/`),
  см. `tests/README.md`
- `docs/` — документация; историческое — в `docs/history/`
- `docker-compose.yml`, `caddy/`, `content-sync/`, `frontend/` — инфраструктура
  развёртывания

## Раскладка данных

### Контент (`redpen-content`)

`<docId>` — например, `medinsky11klass`:

```
redpen-content/<docId>/
  remarks/        page_NNN.md      # опубликованные md-замечания (архив-исходники)
  remarks_draft/  page_NNN.md      # черновики агента-аннотатора (~~~meta-формат)
                  _report_*.md, _check_*.md, _typical_remarks.md  # служебные
  images/             page_NNN.png # изображения страниц
  images_with_grid/   page_NNN.png # то же с координатной сеткой (в git не идёт)
  text/               page_NNN.json# извлечённый текст (сдвиг нумерации +1!)
  illustrations/                   # доп. иллюстрации (опционально)
  paragraphs_list.txt              # список параграфов (задания аннотатору)
  meta.json                        # метаданные (title, манифест страниц)
```

### Публикация (`redpen-publish`)

```
redpen-publish/
  index.html                       # титульная (генерируется)
  css/  js/  favicon.svg           # общие ассеты
  app/  cabinet/                   # редактор и кабинет (не для читателя)
  blog/                            # блог: index.html + <slug>/index.html
  sitemap.xml  robots.txt
  <docId>/
    index.html                     # оглавление документа (page_html.py)
    pages/<label>/index.html       # страница читателя: скан, панель, замечания
                                   # инлайновым блоком redpen-page-data
    metadata.json                  # копия meta.json + манифест страниц
    remarks/  page_NNN.json        # замечания (владелец — API, не сборка)
    images/                        # изображения + иллюстрации
    text/     page_NNN.json        # текст
```

## Настройка

```bash
git clone git@github.com:volokhonsky/redpen-infra.git
cd redpen-infra
# рядом (или внутри дерева) — данные:
git clone git@github.com:volokhonsky/redpen-content.git
git clone git@github.com:volokhonsky/redpen-publish.git

pip install -r scripts/requirements.txt
```

## Использование

### Обработка PDF

```bash
python scripts/process_pdf.py path/to/textbook.pdf \
    --zoom 2 --output-dir ./output --artifacts-repo ../redpen-publish
```

### Конвертация замечаний

```bash
python scripts/remark_converter.py md_to_json \
    redpen-content/medinsky11klass/remarks redpen-publish/medinsky11klass/remarks
```

### Сборка сайта

```bash
python scripts/build_website.py                    # все документы, с push
python scripts/build_website.py --skip-push --target-dir ./out
python scripts/build_website.py --document medinsky11klass --skip-push
```

Сборка не приносит замечания: их владелец — API. В свежий `--target-dir` их
нужно скопировать из `redpen-publish` до сборки, иначе все страницы выйдут
пустыми и `noindex` (об этом сборка предупреждает вслух).

Просмотр: откройте `redpen-publish/index.html` (или
`.../<docId>/pages/<label>/index.html`).

### Тесты

```bash
pip install -r tests/requirements.txt
pytest                       # быстрый набор без браузера
```

Подробнее (и про браузерные сценарии) — в `tests/README.md`.

## Рабочий процесс

1. PDF обрабатывается скриптами (`process_pdf.py` → изображения/текст/шаблоны;
   `make_grid_images.py` — картинки с сеткой для аннотатора).
2. Замечания пишет агент-аннотатор (`docs/remark-agent-prompt.md`; один
   запуск = один параграф) в `remarks_draft/`, либо люди — в редакторе
   `/app/` и кабинете `/cabinet/`.
3. Канон замечаний — SQLite на сервере: черновики импортируются
   `scripts/api/import_remarks.py` (аддитивно), правки редактора пишутся
   напрямую; каждая мутация сразу рендерится и в статические
   `remarks/page_NNN.json`, и в HTML страницы (`scripts/api/publisher.py`).
4. `build_website.py` собирает всё остальное (шаблоны, картинки, текст, манифест
   страниц, страницы читателя, блог, кабинет, редактор) в `redpen-publish`;
   конвертация md→JSON замечаний — только по флагу `--remarks-from-md`.
5. Публикация: push в `redpen-publish` → content-sync раскладывает статику
   (кроме `*/remarks/` — их владелец API). Прод — за Caddy, API — отдельный
   сервис (`docker-compose.yml`).
6. Просмотрщик работает только на статике; API обслуживает исключительно
   редактор и кабинет. Быстрый вход в проект для агентов — корневой `CLAUDE.md`.

## API

Каноническая реализация — `scripts/api` (FastAPI + Uvicorn), запуск через Docker
Compose (сервис `api`) за прокси Caddy. Полное описание конфигурации и всех
эндпоинтов: **`scripts/api/README.md`** — там канон, здесь только вход.

Кратко:
- Переменные окружения читаются из корневого `.env` и `.env.secrets`:
  `STORAGE_DIR`, `LOG_DIR`, `LOG_LEVEL`, `CORS_ALLOW_ORIGINS`, `DB_PATH`,
  `PUBLISH_DIR`, `IDENTITY_PEPPER`.
- `GET /api/health` → `{"status":"ok"}`
- `GET|POST|PUT|PATCH|DELETE /api/editor/{docId}/{pageNum}[/{remarkId}]` —
  правка замечаний; канон — SQLite (`db.py`), каждая запись публикуется в
  `PUBLISH_DIR` немедленно (`publisher.py`)
- `GET /api/remarks`, `/api/history`, `/api/sections`, `/api/tags` — чтение
  для кабинета и редактора
