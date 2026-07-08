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
  - `build_website.py` — сборка и публикация сайта
  - `publish_data.py` — копирование данных в целевой каталог
  - `api/` — FastAPI-сервис (см. `scripts/api/README.md`)
- `templates/` — `index.html`, `document_index.html`, `css/`, `js/`, `favicon.svg`
- `tests/` — pytest-набор и опциональные e2e (см. `tests/README.md`)

> Историческая заметка: отдельный модуль `generate_annotations.py` удалён при
> переходе на аннотации в Markdown. Аннотации теперь пишутся вручную и
> конвертируются в JSON (`annotation_converter.py` / `build_website.py`).

## Контент (`redpen-content`)

Раскладка **по документам** (`<docId>` — например, `medinsky11klass`):

```
redpen-content/<docId>/
  annotations/   page_NNN.md      # аннотации в Markdown (исходники)
  images/        page_NNN.png      # изображения страниц
  text/          page_NNN.json     # извлечённый текст (блоки)
  illustrations/                   # доп. иллюстрации (опционально)
  meta.json                        # метаданные (в т.ч. title)
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
   пустые шаблоны аннотаций.
2. **Аннотирование**: аннотации пишутся вручную как Markdown в
   `redpen-content/<docId>/annotations`.
3. **Сборка**: `build_website.py` конвертирует Markdown → JSON, копирует данные и
   шаблоны в `redpen-publish`, генерирует индексную страницу.
4. **Публикация/развёртывание**: `redpen-publish` разворачивается как статический
   сайт (в проде — за Caddy; API — отдельный сервис, см. `docker-compose.yml`).

## Настройка разработки

```bash
cd redpen-infra
pip install -r scripts/requirements.txt

# сборка в отдельный каталог, без тестов и push:
python scripts/build_website.py --target-dir ./out --skip-tests --skip-push
```
