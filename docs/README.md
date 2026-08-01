# Репозиторий инфраструктуры RedPen

Основной репозиторий проекта RedPen: обрабатывает PDF-учебники и публикует
статический сайт, показывающий страницы с аннотациями («красной ручкой»).

## Ключевое ограничение: сайт полностью статический

Собранный сайт (`redpen-publish`) — самодостаточный набор файлов с относительными
путями. Это требование продукта, а не случайность реализации: сайт должен
открываться с любого статического хостинга, с флешки, офлайн, или встраиваться
как контент в простое приложение. Поэтому:

- **просмотрщик никогда не обращается к API** — изображения, текст и аннотации
  читаются только из статических файлов;
- API и любые серверные хранилища обслуживают исключительно редактирование
  (`?editor=1`) и кабинет; результат редактирования всегда материализуется
  обратно в статические `annotations/*.json`;
- любая доработка, добавляющая просмотрщику сетевую зависимость, нарушает
  это ограничение и не должна приниматься.

## Структура репозитория

- `scripts/` — Python-скрипты обработки и сборки
  - `extract_images.py` — извлекает изображения страниц из PDF
  - `extract_text.py` — извлекает текст из PDF в JSON (`page_NNN.json` с блоками)
  - `process_pdf.py` — оркестрирует пайплайн: изображения → текст → пустые
    шаблоны аннотаций → публикация. (Шаг генерации шаблонов встроен в сам
    скрипт; отдельного `generate_annotations.py` больше нет.)
  - `annotation_converter.py` — конвертация аннотаций Markdown ↔ JSON
  - `build_website.py` — собирает и публикует сайт (конвертация аннотаций,
    копирование данных/шаблонов, генерация индексной страницы, опциональные
    тесты и push)
  - `publish_data.py` — копирует изображения/текст/аннотации в целевой каталог
  - `api/` — FastAPI-сервис (см. `scripts/api/README.md`)
  - `requirements.txt` — зависимости для обработки PDF
- `templates/` — шаблоны статического сайта
  - `css/`, `js/`, `document_index.html`, `index.html`, `favicon.svg`
- `tests/` — тесты (pytest + опциональные e2e на Playwright), см. `tests/README.md`
- `docs/` — документация
- `docker-compose.yml`, `caddy/`, `content-sync/`, `frontend/` — инфраструктура развёртывания

## Соседние каталоги с данными

Скрипты используют два каталога рядом с этим репозиторием (или внутри дерева):

- `redpen-content/` — исходники. Раскладка **по документам**:
  `redpen-content/<docId>/{annotations/*.md, annotations_draft/*.md,
  images/*.png, images_with_grid/*.png, text/*.json, illustrations/,
  paragraphs_list.txt, meta.json}` (например, `redpen-content/medinsky11klass/…`).
  `annotations_draft/` наполняет агент-аннотатор (промпт и регламент —
  `docs/annotation-agent-prompt.md`); детальная раскладка —
  `docs/PROJECT_STRUCTURE.md`.
- `redpen-publish/` — собранный статический сайт:
  `redpen-publish/<docId>/{annotations/*.json, images/, text/, index.html,
  metadata.json}` плюс общие `css/`, `js/`, `cabinet/`, `favicon.svg`,
  `index.html` в корне.

Репозитории независимы; **git submodule не используются**.

## Функции

- Извлечение изображений и текста из PDF
- Конвертация аннотаций Markdown ↔ JSON
- Публикация статического сайта с аннотациями и адаптивной вёрсткой
- Режим редактора аннотаций (`?editor=1`) поверх опубликованного сайта
- API для приёма данных и редактирования аннотаций

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

### Конвертация аннотаций

```bash
python scripts/annotation_converter.py md_to_json \
    redpen-content/medinsky11klass/annotations redpen-publish/medinsky11klass/annotations
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
2. Аннотации пишет агент-аннотатор (`docs/annotation-agent-prompt.md`; один
   запуск = один параграф) в `annotations_draft/`, либо люди — через
   веб-редактор (`?editor=1`) и кабинет (`/cabinet/`).
3. Канон аннотаций — SQLite на сервере: черновики импортируются
   `scripts/api/import_annotations.py` (аддитивно), правки редактора пишутся
   напрямую; каждая мутация сразу рендерится в статические
   `annotations/page_NNN.json` (`scripts/api/publisher.py`).
4. `build_website.py` собирает всё остальное (шаблоны, картинки, текст, манифест
   страниц, кабинет) в `redpen-publish`; конвертация md→JSON аннотаций — только
   по флагу `--annotations-from-md`.
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
- `GET|POST|PUT|DELETE /api/editor/{docId}/{pageNum}[/{annId}]` — редактор
  аннотаций; канон — SQLite (`db.py`), каждая запись публикуется в
  `PUBLISH_DIR` немедленно (`publisher.py`)
