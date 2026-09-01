"""
Unit tests for the cabinet query functions in scripts/api/db.py (stage 3,
C.1): list_remarks/count_remarks, list_history/get_history_record,
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
# list_remarks / count_remarks
# ---------------------------------------------------------------------------


def test_list_remarks_shows_the_author_pseudonym():
    u = _user("sub-author", name="Author One")
    db.upsert_remark_db("doc1", "006", "a1", "minor", "hello", author_id=u["id"])

    items = db.list_remarks(doc_id="doc1")
    assert len(items) == 1
    assert items[0]["authorName"] == "Author One"


def test_list_remarks_never_exposes_an_email():
    # Поле authorEmail упразднено вместе с самим хранением email.
    u = _user("sub-author-2", name="Author Two")
    db.upsert_remark_db("doc1", "006", "a1", "minor", "hello", author_id=u["id"])
    assert "authorEmail" not in db.list_remarks(doc_id="doc1")[0]


def test_list_remarks_author_fields_null_for_imported():
    db.upsert_remark_db("doc1", "006", "a1", "minor", "hello", author_id=None)
    items = db.list_remarks(doc_id="doc1")
    assert items[0]["authorName"] is None


def test_list_remarks_filters_by_doc_page_type_status_author():
    u1 = _user("sub-u1")
    u2 = _user("sub-u2")
    db.upsert_remark_db("doc1", "006", "a1", "minor", "x", author_id=u1["id"])
    db.upsert_remark_db("doc1", "007", "a2", "major", "y", author_id=u2["id"], status="draft")
    db.upsert_remark_db("doc2", "006", "a3", "minor", "z", author_id=u1["id"])

    assert [a["remarkId"] for a in db.list_remarks(doc_id="doc1")] == ["a2", "a1"]
    assert [a["remarkId"] for a in db.list_remarks(page_num="007")] == ["a2"]
    assert [a["remarkId"] for a in db.list_remarks(kind="major")] == ["a2"]
    assert [a["remarkId"] for a in db.list_remarks(status="draft")] == ["a2"]
    assert [a["remarkId"] for a in db.list_remarks(author_id=u1["id"])] == ["a3", "a1"]


def test_list_remarks_search_text_escapes_percent_and_underscore():
    db.upsert_remark_db("doc1", "006", "a1", "minor", "100% discount_code")
    db.upsert_remark_db("doc1", "006", "a2", "minor", "unrelated text")

    assert [a["remarkId"] for a in db.list_remarks(q="100%")] == ["a1"]
    assert [a["remarkId"] for a in db.list_remarks(q="discount_code")] == ["a1"]
    assert [a["remarkId"] for a in db.list_remarks(q="nomatch_xyz")] == []


def test_list_remarks_pagination():
    for i in range(5):
        db.upsert_remark_db("doc1", "006", f"a{i}", "minor", f"text {i}")

    page1 = db.list_remarks(doc_id="doc1", limit=2, offset=0)
    page2 = db.list_remarks(doc_id="doc1", limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    assert {a["remarkId"] for a in page1}.isdisjoint({a["remarkId"] for a in page2})


def test_count_remarks_matches_filters():
    db.upsert_remark_db("doc1", "006", "a1", "minor", "x", status="draft")
    db.upsert_remark_db("doc1", "006", "a2", "minor", "y", status="published")
    assert db.count_remarks(doc_id="doc1") == 2
    assert db.count_remarks(doc_id="doc1", status="draft") == 1


# ---------------------------------------------------------------------------
# list_history / get_history_record
# ---------------------------------------------------------------------------


def test_list_history_filters_and_order():
    u = _user("sub-hist", name="Historian")
    db.upsert_remark_db("doc1", "006", "a1", "minor", "v1", author_id=u["id"], action="create")
    db.upsert_remark_db("doc1", "006", "a1", "minor", "v2", author_id=u["id"], action="update")
    db.archive_remark("doc1", "006", "a1", author_id=u["id"])

    all_hist = db.list_history(doc_id="doc1")
    assert [h["action"] for h in all_hist] == ["archive", "update", "create"]
    assert all_hist[0]["authorName"] == "Historian"
    assert all_hist[0]["snapshot"]["text"] == "v2"

    only_updates = db.list_history(action="update")
    assert len(only_updates) == 1
    assert only_updates[0]["action"] == "update"

    only_ann = db.list_history(remark_id="a1", author_id=u["id"])
    assert len(only_ann) == 3


def test_list_history_pagination():
    for i in range(5):
        db.upsert_remark_db("doc1", "006", "a1", "minor", f"v{i}", action="update")
    page1 = db.list_history(limit=2, offset=0)
    page2 = db.list_history(limit=2, offset=2)
    assert len(page1) == 2 and len(page2) == 2
    assert page1[0]["id"] != page2[0]["id"]


def test_get_history_record_returns_snapshot_or_none():
    db.upsert_remark_db("doc1", "006", "a1", "minor", "hello")
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
    db.upsert_remark_db("doc1", "006", "a1", "minor", "x", status="published")
    db.upsert_remark_db("doc1", "006", "a2", "minor", "y", status="draft")
    db.upsert_remark_db("doc1", "006", "a3", "minor", "z", status="published")
    db.archive_remark("doc1", "006", "a3")
    db.upsert_remark_db("doc2", "001", "b1", "minor", "w", status="published")

    stats = db.get_stats()
    by_doc = {d["docId"]: d for d in stats["docs"]}
    assert by_doc["doc1"] == {"docId": "doc1", "published": 1, "draft": 1, "archived": 1}
    assert by_doc["doc2"] == {"docId": "doc2", "published": 1, "draft": 0, "archived": 0}


def test_get_stats_recent_activity_last_ten():
    for i in range(12):
        db.upsert_remark_db("doc1", "006", f"a{i}", "minor", f"text {i}")
    stats = db.get_stats()
    assert len(stats["recentActivity"]) == 10
    assert stats["recentActivity"][0]["remarkId"] == "a11"
