"""
Unit tests for the cabinet query functions in scripts/api/db.py (stage 3,
C.1): list_annotations/count_annotations, list_history/get_history_record,
list_users, get_stats.
"""

import os

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db_path = os.path.join(tmp_path, "redpen.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "IDENTITY_PEPPER", "unit-test-pepper")
    monkeypatch.setattr(config, "BOOTSTRAP_INVITE_CODE", "")
    db.init_db()
    yield
    db._conn.close()
    db._conn = None


def _user(sub, name="Alice", role="editor"):
    """Участник, опознаваемый только хешем `sub` и своим псевдонимом."""
    user = db.login_with_google_sub(sub, invite_code=db.create_invite(role=role)[0])
    return db.set_display_name(user["id"], name)


# ---------------------------------------------------------------------------
# list_annotations / count_annotations
# ---------------------------------------------------------------------------


def test_list_annotations_shows_the_author_pseudonym():
    u = _user("sub-author", name="Author One")
    db.upsert_annotation_db("doc1", "006", "a1", "comment", "hello", author_id=u["id"])

    items = db.list_annotations(doc_id="doc1")
    assert len(items) == 1
    assert items[0]["authorName"] == "Author One"


def test_list_annotations_never_exposes_an_email():
    # Поле authorEmail упразднено вместе с самим хранением email.
    u = _user("sub-author-2", name="Author Two")
    db.upsert_annotation_db("doc1", "006", "a1", "comment", "hello", author_id=u["id"])
    assert "authorEmail" not in db.list_annotations(doc_id="doc1")[0]


def test_list_annotations_author_fields_null_for_imported():
    db.upsert_annotation_db("doc1", "006", "a1", "comment", "hello", author_id=None)
    items = db.list_annotations(doc_id="doc1")
    assert items[0]["authorName"] is None


def test_list_annotations_filters_by_doc_page_type_status_author():
    u1 = _user("sub-u1")
    u2 = _user("sub-u2")
    db.upsert_annotation_db("doc1", "006", "a1", "comment", "x", author_id=u1["id"])
    db.upsert_annotation_db("doc1", "007", "a2", "main", "y", author_id=u2["id"], status="draft")
    db.upsert_annotation_db("doc2", "006", "a3", "comment", "z", author_id=u1["id"])

    assert [a["annId"] for a in db.list_annotations(doc_id="doc1")] == ["a2", "a1"]
    assert [a["annId"] for a in db.list_annotations(page_num="007")] == ["a2"]
    assert [a["annId"] for a in db.list_annotations(ann_type="main")] == ["a2"]
    assert [a["annId"] for a in db.list_annotations(status="draft")] == ["a2"]
    assert [a["annId"] for a in db.list_annotations(author_id=u1["id"])] == ["a3", "a1"]


def test_list_annotations_search_text_escapes_percent_and_underscore():
    db.upsert_annotation_db("doc1", "006", "a1", "comment", "100% discount_code")
    db.upsert_annotation_db("doc1", "006", "a2", "comment", "unrelated text")

    assert [a["annId"] for a in db.list_annotations(q="100%")] == ["a1"]
    assert [a["annId"] for a in db.list_annotations(q="discount_code")] == ["a1"]
    assert [a["annId"] for a in db.list_annotations(q="nomatch_xyz")] == []


def test_list_annotations_pagination():
    for i in range(5):
        db.upsert_annotation_db("doc1", "006", f"a{i}", "comment", f"text {i}")

    page1 = db.list_annotations(doc_id="doc1", limit=2, offset=0)
    page2 = db.list_annotations(doc_id="doc1", limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    assert {a["annId"] for a in page1}.isdisjoint({a["annId"] for a in page2})


def test_count_annotations_matches_filters():
    db.upsert_annotation_db("doc1", "006", "a1", "comment", "x", status="draft")
    db.upsert_annotation_db("doc1", "006", "a2", "comment", "y", status="published")
    assert db.count_annotations(doc_id="doc1") == 2
    assert db.count_annotations(doc_id="doc1", status="draft") == 1


# ---------------------------------------------------------------------------
# list_history / get_history_record
# ---------------------------------------------------------------------------


def test_list_history_filters_and_order():
    u = _user("sub-hist", name="Historian")
    db.upsert_annotation_db("doc1", "006", "a1", "comment", "v1", author_id=u["id"], action="create")
    db.upsert_annotation_db("doc1", "006", "a1", "comment", "v2", author_id=u["id"], action="update")
    db.soft_delete_annotation("doc1", "006", "a1", author_id=u["id"])

    all_hist = db.list_history(doc_id="doc1")
    assert [h["action"] for h in all_hist] == ["delete", "update", "create"]
    assert all_hist[0]["authorName"] == "Historian"
    assert all_hist[0]["snapshot"]["text"] == "v2"

    only_updates = db.list_history(action="update")
    assert len(only_updates) == 1
    assert only_updates[0]["action"] == "update"

    only_ann = db.list_history(ann_id="a1", author_id=u["id"])
    assert len(only_ann) == 3


def test_list_history_pagination():
    for i in range(5):
        db.upsert_annotation_db("doc1", "006", "a1", "comment", f"v{i}", action="update")
    page1 = db.list_history(limit=2, offset=0)
    page2 = db.list_history(limit=2, offset=2)
    assert len(page1) == 2 and len(page2) == 2
    assert page1[0]["id"] != page2[0]["id"]


def test_get_history_record_returns_snapshot_or_none():
    db.upsert_annotation_db("doc1", "006", "a1", "comment", "hello")
    hist = db.list_history(doc_id="doc1")
    rec = db.get_history_record(hist[0]["id"])
    assert rec is not None
    assert rec["snapshot"]["text"] == "hello"
    assert db.get_history_record(999999) is None


# ---------------------------------------------------------------------------
# list_users
# ---------------------------------------------------------------------------


def test_list_users_exposes_no_google_identity():
    _user("sub-someone", name="Someone", role="viewer")
    users = db.list_users()
    assert len(users) == 1
    assert set(users[0]) == {"id", "kind", "displayName", "role", "createdAt", "lastLoginAt"}
    assert users[0]["displayName"] == "Someone"
    assert users[0]["role"] == "viewer"


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


def test_get_stats_counts_per_doc_and_status():
    db.upsert_annotation_db("doc1", "006", "a1", "comment", "x", status="published")
    db.upsert_annotation_db("doc1", "006", "a2", "comment", "y", status="draft")
    db.upsert_annotation_db("doc1", "006", "a3", "comment", "z", status="published")
    db.soft_delete_annotation("doc1", "006", "a3")
    db.upsert_annotation_db("doc2", "001", "b1", "comment", "w", status="published")

    stats = db.get_stats()
    by_doc = {d["docId"]: d for d in stats["docs"]}
    assert by_doc["doc1"] == {"docId": "doc1", "published": 1, "draft": 1, "deleted": 1}
    assert by_doc["doc2"] == {"docId": "doc2", "published": 1, "draft": 0, "deleted": 0}


def test_get_stats_recent_activity_last_ten():
    for i in range(12):
        db.upsert_annotation_db("doc1", "006", f"a{i}", "comment", f"text {i}")
    stats = db.get_stats()
    assert len(stats["recentActivity"]) == 10
    assert stats["recentActivity"][0]["annId"] == "a11"
