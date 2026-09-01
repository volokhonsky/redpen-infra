# RedPen — тесты

Тесты делятся на два уровня: **основной набор pytest** (быстрый, без браузера)
и **опциональные end-to-end скрипты** на Playwright.

## Основной набор (pytest)

Запускается из корня репозитория и не требует браузера, сервера или Docker.
Сторедж и логи автоматически перенаправляются во временный каталог
(`tests/conftest.py`), поэтому реальные данные не затрагиваются.

```bash
pip install -r tests/requirements.txt
pytest            # из корня репозитория; конфиг в pytest.ini
```

Модули:

| Файл | Что покрывает |
|---|---|
| `test_remark_converter.py` | Конвертация Markdown ↔ JSON (`scripts/remark_converter.py`), round-trip |
| `test_sanitize_bucket.py` | Санитизация bucket/pageId (`scripts/api/storage.py`) |
| `test_db.py` | Users/sessions/allowlist в SQLite (`scripts/api/db.py`, стадия 1) |
| `test_remarks_db.py` | Таблицы `remarks`/`remark_history` в SQLite (`scripts/api/db.py`, стадия 2) |
| `test_publisher.py` | Рендер БД → голый массив, sha, запись `page_NNN.json` (`scripts/api/publisher.py`) |
| `test_import_remarks.py` | CLI-импорт файлов в БД (`scripts/api/import_remarks.py`) |
| `test_export_remarks.py` | CLI-экспорт БД в файлы (`scripts/api/export_remarks.py`) |
| `test_api.py` | Эндпоинты FastAPI через `TestClient`: health, store, store-raw, editor GET/POST/PUT/DELETE, publish-all, auth |
| `test_auth.py` | Google-вход, роли, allowlist, publish-all (стадии 1–2) |
| `test_survey.py` | Опрос `/survey/`: анонимный респондент, пул, ответы, лента (`scripts/api/main.py`, `db.py`) |
| `test_build_website.py` | Сборка сайта (`scripts/build_website.py`): per-document раскладка, `--remarks-from-md`, индексная страница |

`test_build_website.py` строит сайт из синтетического мини-контента во временном
каталоге (реальный `redpen-content` не нужен), поэтому проходит за доли секунды.

## End-to-end скрипты (Playwright, опционально)

Эти скрипты **специально не** названы `test_*.py`, поэтому pytest их не собирает.
Им нужен установленный браузер и, как правило, опубликованный сайт
(`redpen-publish`). Запускаются вручную:

```bash
pip install -r tests/requirements.txt
python -m playwright install chromium

python tests/manual/page_view_markers.py ./out    # геометрия маркеров у читателя
```

- `manual/page_view_markers.py` — измеряет центры `.circle` на настоящих
  страницах сборки при ширине 1280px и 800px и сверяет их с `coords` из
  инлайнового `redpen-page-data`, умноженными на масштаб картинки. Бейзлайна
  нет намеренно: ожидание выводится из данных, поэтому проверка не устаревает
  от правки шапки или полей страницы. Заодно ловит любой запрос наружу —
  офлайн-инвариант просмотрщика.

  Прежние `remark_position_tests.py` (с `baseline_positions.json`) и
  `editor_mode_tests.py` удалены 2026-08-30 вместе со старым SPA: первый строил
  синтетический `document_index.html` с зашитыми кружками и проверял, по сути,
  окружение, второй — режим `?editor=1`, которого больше нет.
- `manual/editor_app_stand.py` + `manual/editor_app_acceptance.py` — приёмка
  экрана страницы в рабочем месте `/work/`: создание замечания кликом по скану,
  перенос маркера, оптимистическая блокировка по `serverPageSha` и проверка,
  что правка доезжает до читателя (JSON страницы и инлайновый
  `redpen-page-data`). Стенд поднимает API на временной БД и статику на одном
  источнике; ничего боевого не трогает.

  ```bash
  python scripts/build_website.py --skip-tests --skip-push --target-dir ./out
  python tests/manual/editor_app_stand.py ./out &   # ждать строку READY
  python tests/manual/editor_app_acceptance.py
  ```

  Стенд поднимает базу с нуля, поэтому для повторного прогона его надо
  перезапустить: после приёмки в базе уже лежит созданное замечание.

- `manual/reproduce_404.py` — отладочный скрипт, не тест; там же
  `manual/offline_bundle_browser_test.py`.

Сборка сайта браузерных проверок больше не гоняет (те, что гоняла, целились в
SPA): флаг `--skip-tests` принимается для совместимости и ничего не выключает.

## Известные ограничения

- Браузерные скрипты требуют playwright с chromium, но не требуют сети:
  просмотрщик не делает ни одного запроса наружу, и `page_view_markers.py`
  на этом настаивает.
