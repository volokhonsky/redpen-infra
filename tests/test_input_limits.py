"""Пределы на пользовательский ввод, которых не было.

У резюме правки, тегов, рабочего комментария и открытого ответа опроса пределы
длины стояли давно. У самого текста замечания — нет, хотя каждая правка
копирует его целиком в remark_history и перерисовывает им страницу статики.
Идентификаторы из адреса не проверялись ни по длине (docId), ни вообще
(remarkId), хотя remarkId становится ключом строки в пяти таблицах.
"""
import os

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402
from _auth_helpers import login  # noqa: E402

DOC = "limitsdoc"
PAGE = "42"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(config, "DB_PATH", os.path.join(tmp_path, "redpen.db"))
    if db._conn is not None:
        db._conn.close()
    db._conn = None
    db.init_db()
    yield
    if db._conn is not None:
        db._conn.close()
    db._conn = None


@pytest.fixture
def editor(monkeypatch):
    return login(monkeypatch, "limits-editor", role="editor")


def _create(editor, **extra):
    body = {"kind": "minor", "text": "текст", "coords": [10, 20]}
    body.update(extra)
    return editor.post(f"/api/editor/{DOC}/{PAGE}", json=body)


def test_remark_text_has_a_ceiling(editor):
    too_long = "я" * (main.MAX_REMARK_TEXT_LENGTH + 1)
    response = _create(editor, text=too_long)
    assert response.status_code == 400
    assert "text must be at most" in response.json()["detail"]


def test_a_long_but_sane_text_is_accepted(editor):
    assert _create(editor, text="я" * 5000).status_code == 200


@pytest.mark.parametrize("bad_id", [
    "a" * (main.MAX_ID_LENGTH + 1),
    "пробел внутри",
    "слэш/внутри",
    "процент%внутри",
])
def test_remark_id_from_the_body_is_validated(editor, bad_id):
    response = _create(editor, id=bad_id)
    assert response.status_code == 400


def test_a_normal_remark_id_still_works(editor):
    assert _create(editor, id="p042-r03:a.1_x").status_code == 200


def test_doc_id_length_is_bounded():
    assert main._validate_doc_id("medinsky11klass") is True
    assert main._validate_doc_id("a" * (main.MAX_ID_LENGTH + 1)) is False
    assert main._validate_doc_id("") is False


def test_timeline_limit_is_validated(editor):
    assert _create(editor, id="r-1").status_code == 200
    ok = editor.get(f"/api/remarks/{DOC}/{PAGE}/r-1/timeline?limit=10")
    assert ok.status_code == 200
    for bad in (0, -5, 100000):
        response = editor.get(f"/api/remarks/{DOC}/{PAGE}/r-1/timeline?limit={bad}")
        assert response.status_code == 400, f"limit={bad} прошёл"


def test_log_lines_is_validated(monkeypatch):
    admin = login(monkeypatch, "limits-admin", role="admin")
    assert admin.get("/api/logs?lines=10").status_code == 200
    assert admin.get("/api/logs?lines=1000000").status_code == 400
    assert admin.get("/api/logs?lines=0").status_code == 400
