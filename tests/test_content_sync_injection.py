"""Скрипт с адресом API внедряется только туда, где API нужен.

Страницы читателя к API не обращаются вовсе — у них инвариант офлайна: ни
одного запроса, аннотации приходят инлайновым блоком внутри HTML. Лишний тег
там не просто мусор: он уезжал в git-снапшот, то есть артефакт развёртывания
попадал в переносимую копию сайта, а в офлайн-архиве абсолютный путь
`/app-config.js` не разрешается.
"""

import importlib.util
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location(
    "content_sync", os.path.join(ROOT, "content-sync", "content_sync.py"))
content_sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(content_sync)

PAGE = "<html><head><title>t</title></head><body>x</body></html>"


@pytest.fixture
def staging(tmp_path):
    from pathlib import Path

    layout = [
        "document_index.html",                      # SPA в корне
        "medinsky11klass/document_index.html",      # SPA документа
        "cabinet/index.html",                       # кабинет
        "index.html",                               # титульная
        "medinsky11klass/index.html",               # оглавление
        "medinsky11klass/pages/6/index.html",       # страница читателя
        "medinsky11klass/pages/200/index.html",
        "blog/index.html",
    ]
    for rel in layout:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(PAGE, encoding="utf-8")
    return Path(tmp_path)


def _has_tag(staging, rel):
    return "app-config.js" in (staging / rel).read_text(encoding="utf-8")


def test_injected_into_api_clients(staging):
    content_sync.inject_app_config_script(staging)
    assert _has_tag(staging, "document_index.html")
    assert _has_tag(staging, "medinsky11klass/document_index.html")
    assert _has_tag(staging, "cabinet/index.html")


def test_reader_pages_stay_clean(staging):
    content_sync.inject_app_config_script(staging)
    assert not _has_tag(staging, "medinsky11klass/pages/6/index.html")
    assert not _has_tag(staging, "medinsky11klass/pages/200/index.html")


def test_contents_and_blog_stay_clean(staging):
    # Оглавление и блог тоже ничего не запрашивают.
    content_sync.inject_app_config_script(staging)
    assert not _has_tag(staging, "index.html")
    assert not _has_tag(staging, "medinsky11klass/index.html")
    assert not _has_tag(staging, "blog/index.html")


def test_injection_is_idempotent(staging):
    content_sync.inject_app_config_script(staging)
    once = (staging / "cabinet/index.html").read_text(encoding="utf-8")
    content_sync.inject_app_config_script(staging)
    assert (staging / "cabinet/index.html").read_text(encoding="utf-8") == once
    assert once.count("app-config.js") == 1


def test_missing_files_are_not_an_error(tmp_path):
    from pathlib import Path
    content_sync.inject_app_config_script(Path(tmp_path))  # пустой каталог
