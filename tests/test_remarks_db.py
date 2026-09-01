"""
Unit tests for the remarks/remark_history tables in scripts/api/db.py
(stage 2: SQLite is the canonical store for remarks).
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
    ann = db.upsert_remark_db(
        "doc1", "006", "ann-1", "minor", "hello", coord_x=10, coord_y=20, author_id=1, action="create"
    )
    assert ann["docId"] == "doc1"
    assert ann["pageNum"] == "006"
    assert ann["remarkId"] == "ann-1"
    assert ann["kind"] == "minor"
    assert ann["text"] == "hello"
    assert ann["coordX"] == 10 and ann["coordY"] == 20
    assert ann["status"] == "published"

    conn = db.get_connection()
    rows = conn.execute("SELECT * FROM remark_history WHERE remark_id = 'ann-1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "create"


def test_upsert_updates_existing_and_appends_history():
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "hello", action="create")
    updated = db.upsert_remark_db("doc1", "006", "ann-1", "major", "changed", action="update")

    assert updated["text"] == "changed"
    assert updated["kind"] == "major"

    all_ann = db.list_page_remarks("doc1", "006")
    assert len(all_ann) == 1
    assert all_ann[0]["text"] == "changed"

    conn = db.get_connection()
    rows = conn.execute("SELECT action FROM remark_history WHERE remark_id = 'ann-1' ORDER BY id").fetchall()
    assert [r["action"] for r in rows] == ["create", "update"]


def test_unique_constraint_is_per_doc_page_ann():
    a1 = db.upsert_remark_db("doc1", "006", "ann-1", "minor", "x")
    a2 = db.upsert_remark_db("doc1", "007", "ann-1", "minor", "y")
    a3 = db.upsert_remark_db("doc2", "006", "ann-1", "minor", "z")
    assert len({a1["rowidPk"], a2["rowidPk"], a3["rowidPk"]}) == 3


def test_list_page_remarks_stable_order():
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "one")
    db.upsert_remark_db("doc1", "006", "ann-2", "minor", "two")
    db.upsert_remark_db("doc1", "006", "ann-3", "minor", "three")
    ids = [a["remarkId"] for a in db.list_page_remarks("doc1", "006")]
    assert ids == ["ann-1", "ann-2", "ann-3"]


def test_list_page_remarks_excludes_archived_by_default():
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "one")
    db.upsert_remark_db("doc1", "006", "ann-2", "minor", "two")
    db.archive_remark("doc1", "006", "ann-1")

    published = db.list_page_remarks("doc1", "006")
    assert [a["remarkId"] for a in published] == ["ann-2"]

    everything = db.list_page_remarks("doc1", "006", include_archived=True)
    assert [a["remarkId"] for a in everything] == ["ann-1", "ann-2"]
    assert everything[0]["status"] == "archived"


def test_archive_missing_or_already_archived_returns_false():
    assert db.archive_remark("doc1", "006", "missing") is False

    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "one")
    assert db.archive_remark("doc1", "006", "ann-1") is True
    assert db.archive_remark("doc1", "006", "ann-1") is False


def test_archive_records_history():
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "one")
    db.archive_remark("doc1", "006", "ann-1", author_id=2)

    conn = db.get_connection()
    rows = conn.execute("SELECT action, author_id FROM remark_history WHERE remark_id = 'ann-1' ORDER BY id").fetchall()
    assert [r["action"] for r in rows] == ["update", "archive"]
    assert rows[-1]["author_id"] == 2


def test_get_remark_returns_regardless_of_status():
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "one")
    db.archive_remark("doc1", "006", "ann-1")
    ann = db.get_remark("doc1", "006", "ann-1")
    assert ann is not None
    assert ann["status"] == "archived"
    assert db.get_remark("doc1", "006", "missing") is None


def test_list_pages_and_doc_ids():
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "one")
    db.upsert_remark_db("doc1", "007", "ann-1", "minor", "one")
    db.upsert_remark_db("doc2", "006", "ann-1", "minor", "one")

    assert db.list_doc_ids() == ["doc1", "doc2"]
    assert db.list_pages() == [("doc1", "006"), ("doc1", "007"), ("doc2", "006")]
    assert db.list_pages("doc1") == [("doc1", "006"), ("doc1", "007")]


def test_page_num_stores_nonstandard_keys_as_is():
    db.upsert_remark_db("doc1", "-01", "ann-1", "minor", "cover")
    db.upsert_remark_db("doc1", "000", "ann-1", "minor", "title")

    assert db.list_pages("doc1") == [("doc1", "-01"), ("doc1", "000")]
    assert db.list_page_remarks("doc1", "-01")[0]["text"] == "cover"


def test_remark_without_coords_stores_nulls():
    ann = db.upsert_remark_db("doc1", "006", "ann-1", "minor", "note")
    assert ann["coordX"] is None
    assert ann["coordY"] is None


def test_add_history_standalone():
    db.add_history("doc1", "006", "ann-x", "import", {"remarkId": "ann-x", "text": "z"}, author_id=None)
    conn = db.get_connection()
    rows = conn.execute("SELECT * FROM remark_history WHERE remark_id = 'ann-x'").fetchall()
    assert len(rows) == 1
    assert rows[0]["action"] == "import"
    assert rows[0]["author_id"] is None


# ===== Tags =====


def test_upsert_stores_and_returns_tags():
    ann = db.upsert_remark_db("doc1", "006", "ann-1", "minor", "x", tags=["Framing", "omission"])
    assert ann["tags"] == ["framing", "omission"]
    assert db.list_page_remarks("doc1", "006")[0]["tags"] == ["framing", "omission"]


def test_upsert_without_tags_preserves_them():
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "x", tags=["omission"])
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "edited")
    assert db.get_remark("doc1", "006", "ann-1")["tags"] == ["omission"]


def test_upsert_with_empty_list_clears_tags():
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "x", tags=["omission"])
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "x", tags=[])
    assert db.get_remark("doc1", "006", "ann-1")["tags"] == []


def test_history_snapshot_carries_tags():
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "x", tags=["omission"])
    record = db.list_history(doc_id="doc1", page_num="006", remark_id="ann-1", limit=1)[0]
    assert record["snapshot"]["tags"] == ["omission"]


@pytest.mark.parametrize("bad", ["draft", "published", "DELETED", "archived", "has space", "кириллица", "", "-lead", "a" * 65])
def test_reserved_and_malformed_tags_rejected(bad):
    with pytest.raises(db.TagError):
        db.normalize_tag(bad)


def test_normalize_tags_drops_duplicates_and_keeps_order():
    assert db.normalize_tags(["Omission", "framing", "omission"]) == ["omission", "framing"]


def test_prefixed_tags_are_allowed():
    assert db.normalize_tag("confidence:high") == "confidence:high"
    assert db.normalize_tag("tc-usa-origin") == "tc-usa-origin"


def test_list_all_tags_counts_and_skips_archived():
    db.upsert_remark_db("doc1", "006", "a1", "minor", "x", tags=["omission", "framing"])
    db.upsert_remark_db("doc1", "007", "a2", "minor", "y", tags=["omission"])
    db.upsert_remark_db("doc1", "008", "a3", "minor", "z", tags=["omission"])
    db.archive_remark("doc1", "008", "a3")

    counts = {t["tag"]: t["count"] for t in db.list_all_tags("doc1")}
    assert counts == {"omission": 2, "framing": 1}


def test_list_remarks_filters_by_tag():
    db.upsert_remark_db("doc1", "006", "a1", "minor", "x", tags=["omission"])
    db.upsert_remark_db("doc1", "006", "a2", "minor", "y")

    assert [a["remarkId"] for a in db.list_remarks(tag="omission")] == ["a1"]
    assert db.count_remarks(tag="omission") == 1
