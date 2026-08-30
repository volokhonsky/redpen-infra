"""Лента событий замечания: ревизии, оценки и комментарии одним списком.

Три источника живут в трёх таблицах намеренно — ревизия это снимок состояния, а
оценка и комментарий состояния не меняют, — и сводятся только на чтении.
Проверяется, что сводятся все три и что тип события различим.
"""

import os

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402
from _auth_helpers import login  # noqa: E402

DOC = "tldoc"
PAGE = "42"
TIMELINE = f"/api/remarks/{DOC}/{PAGE}/t-1/timeline"


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


def _editor(monkeypatch, sub="tl-editor"):
    return login(monkeypatch, sub, role="editor")


def _full_history(client):
    client.post(f"/api/editor/{DOC}/{PAGE}",
                json={"id": "t-1", "kind": "minor", "text": "первая редакция",
                      "coords": [10, 20], "status": "draft"})
    client.put(f"/api/editor/{DOC}/{PAGE}/t-1",
               json={"kind": "minor", "text": "вторая редакция", "coords": [10, 20],
                     "status": "draft"})
    client.patch(f"/api/editor/{DOC}/{PAGE}/t-1/status", json={"status": "published"})
    client.patch(f"/api/editor/{DOC}/{PAGE}/t-1/category", json={"category": "omission"})
    client.put(f"/api/remarks/{DOC}/{PAGE}/t-1/ratings/interest", json={"value": 4})
    client.post(f"/api/remarks/{DOC}/{PAGE}/t-1/notes", json={"body": "обсудить формулировку"})


def test_timeline_merges_all_three_sources(monkeypatch):
    editor = _editor(monkeypatch)
    _full_history(editor)

    items = editor.get(TIMELINE).json()["items"]
    kinds = [i["kind"] for i in items]
    assert kinds.count("revision") == 4
    assert kinds.count("rating") == 1
    assert kinds.count("note") == 1


def test_every_event_carries_its_action_type(monkeypatch):
    editor = _editor(monkeypatch)
    _full_history(editor)
    items = editor.get(TIMELINE).json()["items"]
    by_kind = {}
    for item in items:
        by_kind.setdefault(item["kind"], []).append(item)

    assert by_kind["rating"][0]["actions"] == ["rate"]
    assert by_kind["note"][0]["actions"] == ["note"]
    revision_actions = [tuple(i["actions"]) for i in by_kind["revision"]]
    assert ("publish",) in revision_actions
    assert ("category",) in revision_actions
    assert ("text",) in revision_actions
    assert ("create",) in revision_actions


def test_timeline_labels_come_from_the_server(monkeypatch):
    editor = _editor(monkeypatch)
    _full_history(editor)
    labels = {i["actionLabel"] for i in editor.get(TIMELINE).json()["items"]}
    assert {"публикация", "смена категории", "оценка", "комментарий"} <= labels


def test_rating_appears_once_after_re_rating(monkeypatch):
    """Оценка перезаписывается, а не копится: в ленте она одна, по времени
    последней правки."""
    editor = _editor(monkeypatch)
    _full_history(editor)
    editor.put(f"/api/remarks/{DOC}/{PAGE}/t-1/ratings/interest", json={"value": 1})
    ratings = [i for i in editor.get(TIMELINE).json()["items"] if i["kind"] == "rating"]
    assert len(ratings) == 1
    assert ratings[0]["value"] == 1


def test_timeline_of_unknown_remark_404(monkeypatch):
    editor = _editor(monkeypatch)
    assert editor.get(f"/api/remarks/{DOC}/{PAGE}/nope/timeline").status_code == 404


def test_viewer_cannot_read_the_timeline(monkeypatch):
    editor = _editor(monkeypatch, "tl-owner")
    _full_history(editor)
    viewer = login(monkeypatch, "tl-viewer", role="viewer")
    assert viewer.get(TIMELINE).status_code == 403
