# Репозиторий инфраструктуры RedPen

Основной репозиторий проекта RedPen: обрабатывает PDF-учебники и публикует
статический сайт, показывающий страницы с замечаниями.

**Названия.** `RedPen` — внутреннее имя движка (репозитории, код, служебные
идентификаторы вроде `REDPEN_API_BASE`). Публичное название сайта —
**Мединский.нет**, подпись: «Антимифы к единому учебнику. Проверяем избыточные
победы». Прежний публичный бренд «Красной ручкой» не используется. В разметке
имя сайта появляется в шапке (`templates/document_index.html`,
`templates/cabinet/index.html`, генератор титульной в `scripts/build_website.py`,
страницы блога в `scripts/blog.py`) и в `setDocumentTitle()`
(`templates/js/main.js`); подпись выводится только в hero титульной, чтобы
шапка оставалась однострочной (высота шапки заложена в
`#layout { min-height: calc(100vh - 98px) }`).

## Ключевое ограничение: сайт полностью статический

Собранный сайт (`redpen-publish`) — самодостаточный набор файлов с относительными
путями. Это требование продукта, а не случайность реализации: сайт должен
открываться с любого статического хостинга, с флешки, офлайн, или встраиваться
как контент в простое приложение. Поэтому:

- **просмотрщик никогда не обращается к API** — изображения, текст и замечания
  читаются только из статических файлов;
- API и любые серверные хранилища обслуживают исключительно редактирование
  (`?editor=1`) и кабинет; результат редактирования всегда материализуется
  обратно в статические `remarks/*.json`;
- любая доработка, добавляющая просмотрщику сетевую зависимость, нарушает
  это ограничение и не должна приниматься.

## Структура репозитория

- `scripts/` — Python-скрипты обработки и сборки
  - `extract_images.py` — извлекает изображения страниц из PDF
  - `extract_text.py` — извлекает текст из PDF в JSON (`page_NNN.json` с блоками)
  - `process_pdf.py` — оркестрирует пайплайн: изображения → текст → пустые
    шаблоны замечаний → публикация. (Шаг генерации шаблонов встроен в сам
    скрипт; отдельного `generate_remarks.py` больше нет.)
  - `remark_converter.py` — конвертация замечаний Markdown ↔ JSON
  - `build_website.py` — собирает и публикует сайт (конвертация замечаний,
    копирование данных/шаблонов, генерация индексной страницы, опциональные
    тесты и push)
  - `blog.py` — статический блог: рендерит `content/blog/*.md` в `blog/index.html`
    и `blog/<slug>/index.html`, отдаёт секцию с последней записью титульной
    (вызывается из `build_website.py`; собственный минимальный markdown, без
    зависимостей и без обращений к сети в рантайме)
  - `publish_data.py` — копирует изображения/текст/замечания в целевой каталог
  - `api/` — FastAPI-сервис (см. `scripts/api/README.md`)
  - `requirements.txt` — зависимости для обработки PDF
- `templates/` — шаблоны статического сайта
  - `css/`, `js/`, `document_index.html`, `index.html`, `favicon.svg`
  - `css/landing.css` — каркас текстовых страниц (титульная + блог),
    `css/blog.css` — стили блога
- `content/blog/*.md` — исходники записей блога (frontmatter `title`/`date`/
  `summary` + markdown-тело; имя файла задаёт slug)
- `tests/` — тесты (pytest + опциональные e2e на Playwright), см. `tests/README.md`
- `docs/` — документация
- `docker-compose.yml`, `caddy/`, `content-sync/`, `frontend/` — инфраструктура развёртывания

## Соседние каталоги с данными

Скрипты используют два каталога рядом с этим репозиторием (или внутри дерева):

- `redpen-content/` — исходники. Раскладка **по документам**:
  `redpen-content/<docId>/{remarks/*.md, remarks_draft/*.md,
  images/*.png, images_with_grid/*.png, text/*.json, illustrations/,
  paragraphs_list.txt, meta.json}` (например, `redpen-content/medinsky11klass/…`).
  `remarks_draft/` наполняет агент-аннотатор (промпт и регламент —
  `docs/remark-agent-prompt.md`); детальная раскладка —
  `docs/PROJECT_STRUCTURE.md`.
- `redpen-publish/` — собранный статический сайт:
  `redpen-publish/<docId>/{remarks/*.json, images/, text/, index.html,
  metadata.json}` плюс общие `css/`, `js/`, `cabinet/`, `favicon.svg`,
  `index.html` в корне.

Репозитории независимы; **git submodule не используются**.

## Функции

- Извлечение изображений и текста из PDF
- Конвертация замечаний Markdown ↔ JSON
- Публикация статического сайта с замечаниями и адаптивной вёрсткой
- Режим редактора замечаний (`?editor=1`) поверх опубликованного сайта
- API для приёма данных и редактирования замечаний

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
python scripts/build_website.py                    # все документы, с тестами и push
python scripts/build_website.py --skip-tests --skip-push --target-dir ./out
python scripts/build_website.py --document medinsky11klass --skip-tests --skip-push
```

Просмотр: откройте `redpen-publish/index.html` (или `.../<docId>/index.html`).

### Тесты

```bash
pip install -r tests/requirements.txt
pytest                       # быстрый набор без браузера
```

Подробнее (и про опциональные e2e-тесты) — в `tests/README.md`.

## Рабочий процесс

1. PDF обрабатывается скриптами (`process_pdf.py` → изображения/текст/шаблоны;
   `make_grid_images.py` — картинки с сеткой для аннотатора).
2. Замечания пишет агент-аннотатор (`docs/remark-agent-prompt.md`; один
   запуск = один параграф) в `remarks_draft/`, либо люди — через
   веб-редактор (`?editor=1`) и кабинет (`/cabinet/`).
3. Канон замечаний — SQLite на сервере: черновики импортируются
   `scripts/api/import_remarks.py` (аддитивно), правки редактора пишутся
   напрямую; каждая мутация сразу рендерится в статические
   `remarks/page_NNN.json` (`scripts/api/publisher.py`).
4. `build_website.py` собирает всё остальное (шаблоны, картинки, текст, манифест
   страниц, кабинет) в `redpen-publish`; конвертация md→JSON замечаний — только
   по флагу `--remarks-from-md`.
5. Просмотрщик работает только на статике; API обслуживает исключительно
   редактор и кабинет. Быстрый вход в проект для агентов — корневой `CLAUDE.md`.

## API

Каноническая реализация — `scripts/api` (FastAPI + Uvicorn), запуск через Docker
Compose (сервис `api`) за прокси Caddy. Полное описание конфигурации и всех
эндпоинтов: **`scripts/api/README.md`**.

Кратко:
- Переменные окружения читаются из корневого `.env`: `STORAGE_DIR`,
  `LOG_DIR`, `LOG_LEVEL`, `CORS_ALLOW_ORIGINS`, `DB_PATH`, `PUBLISH_DIR`.
- `GET /api/health` → `{"status":"ok"}`
- `POST /api/store` / `POST /api/store-raw` — приём JSON в `${STORAGE_DIR}/inbox/…`
- `GET|POST|PUT|DELETE /api/editor/{docId}/{pageNum}[/{remarkId}]` — редактор
  замечаний; канон — SQLite (`db.py`), каждая запись публикуется в
  `PUBLISH_DIR` немедленно (`publisher.py`)
