"""Комментарии участников к замечанию: рабочее обсуждение внутри редактора.

До них единственным местом для человеческого текста было `summary` ревизии —
одна строка в 200 символов, без ответа. Проверяется тред в один уровень, мягкое
удаление, «решено» на корне и то, что комментарии не доезжают до статики.
"""

import os

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402
import publisher  # noqa: E402
from _auth_helpers import anon, login  # noqa: E402

DOC = "notedoc"
PAGE = "42"
PAGE_KEY = "042"


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setattr(config, "BOOTSTRAP_INVITE_CODE", "")
    monkeypatch.setattr(config, "DB_PATH", os.path.join(tmp_path, "redpen.db"))
    if db._conn is not None:
        db._conn.close()
    db._conn = None
    db.init_db()
    yield
    if db._conn is not None:
        db._conn.close()
    db._conn = None


def _editor(monkeypatch, sub="note-editor"):
    return login(monkeypatch, sub, role="editor")


def _create(client, remark_id="n-1"):
    r = client.post(f"/api/editor/{DOC}/{PAGE}",
                    json={"id": remark_id, "kind": "minor", "text": "текст",
                          "coords": [10, 20], "status": "published"})
    assert r.status_code == 200, r.text
    return remark_id


NOTES = f"/api/remarks/{DOC}/{PAGE}/n-1/notes"


def _post(client, body, parent_id=None):
    payload = {"body": body}
    if parent_id is not None:
        payload["parentId"] = parent_id
    return client.post(NOTES, json=payload)


# --- тред ------------------------------------------------------------------

def test_note_is_stored_and_listed(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    r = _post(editor, "Источник не подтверждает цифру.")
    assert r.status_code == 200, r.text
    assert r.json()["note"]["parentId"] is None

    items = editor.get(NOTES).json()["items"]
    assert [n["body"] for n in items] == ["Источник не подтверждает цифру."]


def test_reply_attaches_to_the_root(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    root = _post(editor, "Проверить по переписи.").json()["note"]
    reply = _post(editor, "Проверил, цифра из другого года.", parent_id=root["id"])
    assert reply.status_code == 200
    assert reply.json()["note"]["parentId"] == root["id"]


def test_thread_is_one_level_deep(monkeypatch):
    """Ответ на ответ отклоняется: глубже дерево пришлось бы рендерить
    рекурсивно, а обсуждению одного замечания это не нужно."""
    editor = _editor(monkeypatch)
    _create(editor)
    root = _post(editor, "корень").json()["note"]
    reply = _post(editor, "ответ", parent_id=root["id"]).json()["note"]
    assert _post(editor, "ответ на ответ", parent_id=reply["id"]).status_code == 400


def test_reply_to_a_note_of_another_remark_is_refused(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor, "n-1")
    _create(editor, "n-2")
    root = _post(editor, "корень").json()["note"]
    r = editor.post(f"/api/remarks/{DOC}/{PAGE}/n-2/notes",
                    json={"body": "чужой ответ", "parentId": root["id"]})
    assert r.status_code == 400


def test_empty_and_overlong_bodies_are_refused(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    assert _post(editor, "   ").status_code == 400
    assert _post(editor, "x" * 4001).status_code == 400


# --- решено ----------------------------------------------------------------

def test_resolving_a_thread(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    root = _post(editor, "надо проверить").json()["note"]
    assert editor.get(NOTES).json()["open"] == 1

    r = editor.patch(f"/api/notes/{root['id']}", json={"resolved": True})
    assert r.status_code == 200
    assert r.json()["note"]["resolved"] is True
    assert editor.get(NOTES).json()["open"] == 0

    editor.patch(f"/api/notes/{root['id']}", json={"resolved": False})
    assert editor.get(NOTES).json()["open"] == 1


def test_only_the_root_of_a_thread_can_be_resolved(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    root = _post(editor, "корень").json()["note"]
    reply = _post(editor, "ответ", parent_id=root["id"]).json()["note"]
    assert editor.patch(f"/api/notes/{reply['id']}",
                        json={"resolved": True}).status_code == 400


def test_anyone_with_edit_rights_can_close_a_thread(monkeypatch):
    """Решение — про работу, а не про авторство: закрывает любой редактор."""
    author = _editor(monkeypatch, "note-author")
    _create(author)
    root = _post(author, "вопрос").json()["note"]
    other = _editor(monkeypatch, "note-other")
    assert other.patch(f"/api/notes/{root['id']}",
                       json={"resolved": True}).status_code == 200


# --- правка и удаление -----------------------------------------------------

def test_author_can_edit_own_note(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    note = _post(editor, "первая редакция").json()["note"]
    r = editor.patch(f"/api/notes/{note['id']}", json={"body": "вторая редакция"})
    assert r.status_code == 200
    assert r.json()["note"]["body"] == "вторая редакция"


def test_someone_elses_note_cannot_be_edited(monkeypatch):
    author = _editor(monkeypatch, "note-owner")
    _create(author)
    note = _post(author, "моё").json()["note"]
    other = _editor(monkeypatch, "note-stranger")
    assert other.patch(f"/api/notes/{note['id']}", json={"body": "не моё"}).status_code == 403
    assert other.delete(f"/api/notes/{note['id']}").status_code == 403


def test_soft_delete_wipes_the_body_but_keeps_the_thread(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    root = _post(editor, "корень").json()["note"]
    _post(editor, "ответ", parent_id=root["id"])
    assert editor.delete(f"/api/notes/{root['id']}").status_code == 200

    # Из выдачи корень ушёл, ответ остался: тред не осыпается.
    listed = editor.get(NOTES).json()["items"]
    assert [n["body"] for n in listed] == ["ответ"]
    # Строка на месте, но тела в ней больше нет — «удалить» значит удалить.
    hidden = db.list_notes(DOC, PAGE_KEY, "n-1", include_deleted=True)
    deleted = [n for n in hidden if n["deleted"]][0]
    assert deleted["body"] == ""


def test_deleting_twice_is_404(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    note = _post(editor, "текст").json()["note"]
    editor.delete(f"/api/notes/{note['id']}")
    assert editor.delete(f"/api/notes/{note['id']}").status_code == 404


def test_cannot_reply_to_a_deleted_note(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    note = _post(editor, "текст").json()["note"]
    editor.delete(f"/api/notes/{note['id']}")
    assert _post(editor, "ответ", parent_id=note["id"]).status_code == 400


def test_patch_without_body_or_resolved_is_refused(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    note = _post(editor, "текст").json()["note"]
    assert editor.patch(f"/api/notes/{note['id']}", json={}).status_code == 400


# --- права -----------------------------------------------------------------

def test_viewer_cannot_comment(monkeypatch):
    editor = _editor(monkeypatch, "note-perm-owner")
    _create(editor)
    viewer = login(monkeypatch, "note-viewer", role="viewer")
    assert viewer.post(NOTES, json={"body": "нельзя"}).status_code == 403
    assert anon().post(NOTES, json={"body": "нельзя"}).status_code == 401
    # Читать обсуждение тоже нельзя: это рабочая кухня, а не публичная ветка.
    assert viewer.get(NOTES).status_code == 403


# --- главный инвариант -----------------------------------------------------

def test_notes_never_reach_the_static_site(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    before = publisher.compute_page_sha(publisher.render_page(DOC, PAGE_KEY))
    _post(editor, "внутренняя кухня: спорная формулировка")

    rendered = publisher.render_page(DOC, PAGE_KEY)
    assert publisher.compute_page_sha(rendered) == before
    assert "внутренняя кухня" not in repr(rendered)
    for item in rendered:
        assert "notes" not in item
