"""
Unit tests for the annotations/annotation_history tables in scripts/api/db.py
(stage 2: SQLite is the canonical store for annotations).
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
    db.init_db()
    yield
    db._conn.close()
    db._conn = None


def test_upsert_creates_and_records_history():
    ann = db.upsert_annotation_db(
        "doc1", "006", "ann-1", "comment", "hello", coord_x=10, coord_y=20, author_id=1, action="create"
    )
    assert ann["docId"] == "doc1"
    assert ann["pageNum"] == "006"
    assert ann["annId"] == "ann-1"
    assert ann["annType"] == "comment"
    assert ann["text"] == "hello"
    assert ann["coordX"] == 10 and ann["coordY"] == 20
    assert ann["status"] == "published"

    conn = db.get_connection()
    rows = conn.execute("SELECT * FROM annotation_history WHERE ann_id = 'ann-1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "create"


def test_upsert_updates_existing_and_appends_history():
    db.upsert_annotation_db("doc1", "006", "ann-1", "comment", "hello", action="create")
    updated = db.upsert_annotation_db("doc1", "006", "ann-1", "main", "changed", action="update")

    assert updated["text"] == "changed"
    assert updated["annType"] == "main"

    all_ann = db.list_page_annotations("doc1", "006")
    assert len(all_ann) == 1
    assert all_ann[0]["text"] == "changed"

    conn = db.get_connection()
    rows = conn.execute("SELECT action FROM annotation_history WHERE ann_id = 'ann-1' ORDER BY id").fetchall()
    assert [r["action"] for r in rows] == ["create", "update"]


def test_unique_constraint_is_per_doc_page_ann():
    a1 = db.upsert_annotation_db("doc1", "006", "ann-1", "comment", "x")
    a2 = db.upsert_annotation_db("doc1", "007", "ann-1", "comment", "y")
    a3 = db.upsert_annotation_db("doc2", "006", "ann-1", "comment", "z")
    assert len({a1["rowidPk"], a2["rowidPk"], a3["rowidPk"]}) == 3


def test_list_page_annotations_stable_order():
    db.upsert_annotation_db("doc1", "006", "ann-1", "comment", "one")
    db.upsert_annotation_db("doc1", "006", "ann-2", "comment", "two")
    db.upsert_annotation_db("doc1", "006", "ann-3", "comment", "three")
    ids = [a["annId"] for a in db.list_page_annotations("doc1", "006")]
    assert ids == ["ann-1", "ann-2", "ann-3"]


def test_list_page_annotations_excludes_deleted_by_default():
    db.upsert_annotation_db("doc1", "006", "ann-1", "comment", "one")
    db.upsert_annotation_db("doc1", "006", "ann-2", "comment", "two")
    db.soft_delete_annotation("doc1", "006", "ann-1")

    published = db.list_page_annotations("doc1", "006")
    assert [a["annId"] for a in published] == ["ann-2"]

    everything = db.list_page_annotations("doc1", "006", include_deleted=True)
    assert [a["annId"] for a in everything] == ["ann-1", "ann-2"]
    assert everything[0]["status"] == "deleted"


def test_soft_delete_missing_or_already_deleted_returns_false():
    assert db.soft_delete_annotation("doc1", "006", "missing") is False

    db.upsert_annotation_db("doc1", "006", "ann-1", "comment", "one")
    assert db.soft_delete_annotation("doc1", "006", "ann-1") is True
    assert db.soft_delete_annotation("doc1", "006", "ann-1") is False


def test_soft_delete_records_history():
    db.upsert_annotation_db("doc1", "006", "ann-1", "comment", "one")
    db.soft_delete_annotation("doc1", "006", "ann-1", author_id=2)

    conn = db.get_connection()
    rows = conn.execute("SELECT action, author_id FROM annotation_history WHERE ann_id = 'ann-1' ORDER BY id").fetchall()
    assert [r["action"] for r in rows] == ["update", "delete"]
    assert rows[-1]["author_id"] == 2


def test_get_annotation_returns_regardless_of_status():
    db.upsert_annotation_db("doc1", "006", "ann-1", "comment", "one")
    db.soft_delete_annotation("doc1", "006", "ann-1")
    ann = db.get_annotation("doc1", "006", "ann-1")
    assert ann is not None
    assert ann["status"] == "deleted"
    assert db.get_annotation("doc1", "006", "missing") is None


def test_list_pages_and_doc_ids():
    db.upsert_annotation_db("doc1", "006", "ann-1", "comment", "one")
    db.upsert_annotation_db("doc1", "007", "ann-1", "comment", "one")
    db.upsert_annotation_db("doc2", "006", "ann-1", "comment", "one")

    assert db.list_doc_ids() == ["doc1", "doc2"]
    assert db.list_pages() == [("doc1", "006"), ("doc1", "007"), ("doc2", "006")]
    assert db.list_pages("doc1") == [("doc1", "006"), ("doc1", "007")]


def test_page_num_stores_nonstandard_keys_as_is():
    db.upsert_annotation_db("doc1", "-01", "ann-1", "comment", "cover")
    db.upsert_annotation_db("doc1", "000", "ann-1", "comment", "title")

    assert db.list_pages("doc1") == [("doc1", "-01"), ("doc1", "000")]
    assert db.list_page_annotations("doc1", "-01")[0]["text"] == "cover"


def test_general_annotation_has_no_coords():
    ann = db.upsert_annotation_db("doc1", "006", "ann-1", "general", "note")
    assert ann["coordX"] is None
    assert ann["coordY"] is None


def test_add_history_standalone():
    db.add_history("doc1", "006", "ann-x", "import", {"annId": "ann-x", "text": "z"}, author_id=None)
    conn = db.get_connection()
    rows = conn.execute("SELECT * FROM annotation_history WHERE ann_id = 'ann-x'").fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "import"
    assert rows[0]["author_id"] is None
