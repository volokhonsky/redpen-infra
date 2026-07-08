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
| `test_annotation_converter.py` | Конвертация Markdown ↔ JSON (`scripts/annotation_converter.py`), round-trip |
| `test_sanitize_bucket.py` | Санитизация bucket/pageId (`scripts/api/storage.py`) |
| `test_storage.py` | Хранение страниц: `load_page`/`save_page`/`compute_sha`/`upsert`/`update` |
| `test_api.py` | Эндпоинты FastAPI через `TestClient`: health, store, store-raw, editor GET/POST/PUT, rebuild-валидация, auth |
| `test_build_website.py` | Сборка сайта (`scripts/build_website.py`): per-document раскладка, конвертация аннотаций, индексная страница |

`test_build_website.py` строит сайт из синтетического мини-контента во временном
каталоге (реальный `redpen-content` не нужен), поэтому проходит за доли секунды.

## End-to-end скрипты (Playwright, опционально)

Эти скрипты **специально не** названы `test_*.py`, поэтому pytest их не собирает.
Им нужен установленный браузер и, как правило, опубликованный сайт
(`redpen-publish`). Запускаются вручную:

```bash
pip install -r tests/requirements.txt
python -m playwright install chromium

python tests/annotation_position_tests.py         # позиционирование кружков
python tests/annotation_position_tests.py --update-baseline
python tests/editor_mode_tests.py                 # режим редактора (?editor=1)
python tests/simple_test.py                        # смоук рендера страницы
```

- `annotation_position_tests.py` — измеряет позиции элементов `.circle` при
  ширине 1280px / 800px и при ресайзе, сравнивает с `baseline_positions.json`.
  Использует синтетический `document_index.html`, а не реальное приложение —
  это скорее проверка окружения, чем логики.
- `editor_mode_tests.py` — проверяет, что панель редактора (`.redpen-editor`)
  и `window.RedPenEditor.state.editorMode` появляются только с `?editor=1`.
  Работает против опубликованного `redpen-publish/medinsky11klass/`.
- `simple_test.py`, `reproduce_404.py` — отладочные скрипты, не тесты.

`build_website.py` вызывает часть этих e2e-проверок автоматически на шаге сборки;
пропустить их можно флагом `--skip-tests`.

## Известные ограничения

- E2E-скрипты требуют сети (подключают `marked` с CDN в шаблоне документа).
- `baseline_positions.json` привязан к синтетической тестовой странице.
