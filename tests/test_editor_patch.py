"""Узкие операции над замечанием: статус, категория, теги.

До них публикация черновика ездила в общем PUT вместе с текстом: чтобы
опубликовать замечание, очередь приёмки была обязана прислать его целиком.
Проверяется, что операция делает ровно одно, оставляет в журнале ровно один
состав изменения и не требует serverPageSha.
"""

import os

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import config  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402
import publisher  # noqa: E402
from _auth_helpers import anon, login, with_csrf  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    """Своя база на тест: журнал ревизий здесь и есть предмет проверки, а общая
    база копила бы чужие правки и ломала счёт."""
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


def _editor(monkeypatch, sub="patch-editor"):
    return login(monkeypatch, sub, role="editor")


DOC = "patchdoc"
#: Что шлёт клиент и что после нормализации лежит в базе — разные строки:
#: ключ страницы файловый, трёхзначный.
PAGE = "42"
PAGE_KEY = "042"


def _create(client, text="исходный текст", status="draft", remark_id="p-1"):
    r = client.post(f"/api/editor/{DOC}/{PAGE}",
                    json={"id": remark_id, "kind": "minor", "text": text,
                          "coords": [10, 20], "status": status})
    assert r.status_code == 200, r.text
    return remark_id


def _last_changes(remark_id="p-1"):
    return db.list_history(doc_id=DOC, remark_id=remark_id, limit=1)[0]["changes"]


# --- статус ----------------------------------------------------------------

def test_publish_draft_records_exactly_one_action(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    r = editor.patch(f"/api/editor/{DOC}/{PAGE}/p-1/status", json={"status": "published"})
    assert r.status_code == 200, r.text
    assert r.json()["remark"]["status"] == "published"
    assert _last_changes() == ["publish"]


def test_unpublish_is_its_own_action(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor, status="published")
    editor.patch(f"/api/editor/{DOC}/{PAGE}/p-1/status", json={"status": "draft"})
    assert _last_changes() == ["unpublish"]


def test_patch_status_does_not_touch_the_text(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor, text="важная формулировка")
    editor.patch(f"/api/editor/{DOC}/{PAGE}/p-1/status", json={"status": "published"})
    assert db.get_remark(DOC, PAGE_KEY, "p-1")["text"] == "важная формулировка"


def test_patch_status_needs_no_server_page_sha(monkeypatch):
    """Оптимистическая блокировка защищает текст, а не переход статуса."""
    editor = _editor(monkeypatch)
    _create(editor)
    r = editor.patch(f"/api/editor/{DOC}/{PAGE}/p-1/status", json={"status": "published"})
    assert r.status_code == 200


def test_publishing_a_draft_moves_the_page_sha(monkeypatch):
    """Черновики не входят в render_page(), поэтому публикация страницу меняет —
    и открытая сессия редактора получит 409 на следующем сохранении. Так и надо:
    страница действительно стала другой."""
    editor = _editor(monkeypatch)
    _create(editor)
    before = publisher.compute_page_sha(publisher.render_page(DOC, PAGE_KEY))
    editor.patch(f"/api/editor/{DOC}/{PAGE}/p-1/status", json={"status": "published"})
    after = publisher.compute_page_sha(publisher.render_page(DOC, PAGE_KEY))
    assert before != after


def test_deleted_is_not_reachable_through_status(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    r = editor.patch(f"/api/editor/{DOC}/{PAGE}/p-1/status", json={"status": "deleted"})
    assert r.status_code == 400


def test_patch_status_unknown_remark_404(monkeypatch):
    editor = _editor(monkeypatch)
    r = editor.patch(f"/api/editor/{DOC}/{PAGE}/nope/status", json={"status": "draft"})
    assert r.status_code == 404


# --- категория -------------------------------------------------------------

def test_patch_category_marks_the_decision_as_human(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    r = editor.patch(f"/api/editor/{DOC}/{PAGE}/p-1/category", json={"category": "omission"})
    assert r.status_code == 200, r.text
    ann = db.get_remark(DOC, PAGE_KEY, "p-1")
    assert ann["category"] == "omission"
    assert ann["categorySource"] == "human"
    assert _last_changes() == ["category"]


def test_patch_category_null_resets_to_other(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    editor.patch(f"/api/editor/{DOC}/{PAGE}/p-1/category", json={"category": "omission"})
    editor.patch(f"/api/editor/{DOC}/{PAGE}/p-1/category", json={"category": None})
    assert db.get_remark(DOC, PAGE_KEY, "p-1")["category"] == "other"


def test_patch_category_requires_the_key(monkeypatch):
    """Здесь отсутствие ключа — не «не трогать», а ошибка: смена категории
    единственное, зачем этот маршрут зовут."""
    editor = _editor(monkeypatch)
    _create(editor)
    assert editor.patch(f"/api/editor/{DOC}/{PAGE}/p-1/category", json={}).status_code == 400


def test_patch_category_rejects_unknown_slug(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    r = editor.patch(f"/api/editor/{DOC}/{PAGE}/p-1/category", json={"category": "выдумка"})
    assert r.status_code == 400


# --- теги ------------------------------------------------------------------

def test_patch_tags_replaces_the_set(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    editor.patch(f"/api/editor/{DOC}/{PAGE}/p-1/tags", json={"tags": ["alpha", "beta"]})
    assert db.get_remark(DOC, PAGE_KEY, "p-1")["tags"] == ["alpha", "beta"]
    assert _last_changes() == ["tags"]


def test_patch_tags_empty_list_clears(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    editor.patch(f"/api/editor/{DOC}/{PAGE}/p-1/tags", json={"tags": ["alpha"]})
    editor.patch(f"/api/editor/{DOC}/{PAGE}/p-1/tags", json={"tags": []})
    assert db.get_remark(DOC, PAGE_KEY, "p-1")["tags"] == []


def test_patch_tags_rejects_reserved_names(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    r = editor.patch(f"/api/editor/{DOC}/{PAGE}/p-1/tags", json={"tags": ["draft"]})
    assert r.status_code == 400


# --- права -----------------------------------------------------------------

@pytest.mark.parametrize("suffix,body", [
    ("status", {"status": "published"}),
    ("category", {"category": "omission"}),
    ("tags", {"tags": ["x"]}),
])
def test_patch_requires_editor(monkeypatch, suffix, body):
    editor = _editor(monkeypatch, sub="patch-owner")
    _create(editor)

    viewer = login(monkeypatch, "patch-viewer", role="viewer")
    assert viewer.patch(f"/api/editor/{DOC}/{PAGE}/p-1/{suffix}", json=body).status_code == 403
    assert anon().patch(f"/api/editor/{DOC}/{PAGE}/p-1/{suffix}", json=body).status_code == 401


def test_patch_without_csrf_403(monkeypatch):
    editor = _editor(monkeypatch, sub="patch-csrf")
    _create(editor)
    editor.headers.pop("X-CSRF-Token", None)
    r = editor.patch(f"/api/editor/{DOC}/{PAGE}/p-1/status", json={"status": "published"})
    assert r.status_code == 403


# --- резюме правки ---------------------------------------------------------

def test_patch_carries_the_edit_summary_into_history(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    editor.patch(f"/api/editor/{DOC}/{PAGE}/p-1/status",
                 json={"status": "published", "summary": "принято при приёмке §24"})
    assert db.list_history(doc_id=DOC, remark_id="p-1", limit=1)[0]["summary"] == \
        "принято при приёмке §24"


def test_patch_rejects_overlong_summary(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    r = editor.patch(f"/api/editor/{DOC}/{PAGE}/p-1/status",
                     json={"status": "published", "summary": "x" * 500})
    assert r.status_code == 400


# --- фильтр по составу изменения ------------------------------------------

def test_history_filter_separates_text_edits_from_the_rest(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor, text="первая редакция")
    editor.put(f"/api/editor/{DOC}/{PAGE}/p-1",
               json={"kind": "minor", "text": "вторая редакция", "coords": [10, 20],
                     "status": "draft"})
    editor.patch(f"/api/editor/{DOC}/{PAGE}/p-1/status", json={"status": "published"})
    editor.patch(f"/api/editor/{DOC}/{PAGE}/p-1/category", json={"category": "omission"})

    text_only = editor.get("/api/history", params={"docId": DOC, "changed": "text"}).json()
    assert [h["revNo"] for h in text_only["items"]] == [2]

    everything = editor.get("/api/history", params={"docId": DOC}).json()
    assert len(everything["items"]) == 4


def test_history_rejects_unknown_change_token(monkeypatch):
    editor = _editor(monkeypatch)
    r = editor.get("/api/history", params={"docId": DOC, "changed": "выдумка"})
    assert r.status_code == 400
