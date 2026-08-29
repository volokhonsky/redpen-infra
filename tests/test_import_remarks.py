"""
Unit tests for scripts/api/import_remarks.py (stage 2, A2.3).
"""

import json
import os

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402
import import_remarks  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db_path = os.path.join(tmp_path, "redpen.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.init_db()
    yield
    db._conn.close()
    db._conn = None


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def test_import_bare_array_format(tmp_path):
    src = str(tmp_path / "source")
    _write(
        os.path.join(src, "doc1", "remarks", "page_006.json"),
        [{"id": "ann-1", "text": "hi", "kind": "major", "coords": [10, 20]}],
    )

    totals = import_remarks.run(src, None, overwrite=False, dry_run=False)
    assert totals == {"docs": 1, "pages": 1, "imported": 1, "skipped": 0, "errors": 0}

    ann = db.get_remark("doc1", "006", "ann-1")
    assert ann["text"] == "hi"
    assert ann["kind"] == "major"
    assert ann["coordX"] == 10 and ann["coordY"] == 20


def test_import_legacy_page_object_format(tmp_path):
    src = str(tmp_path / "source")
    _write(
        os.path.join(src, "doc1", "remarks", "page_006.json"),
        {
            "pageId": "doc1_page_006",
            "serverPageSha": "abc",
            "remarks": [{"id": "ann-1", "text": "hi", "kind": "minor"}],
        },
    )

    totals = import_remarks.run(src, None, overwrite=False, dry_run=False)
    assert totals["imported"] == 1
    ann = db.get_remark("doc1", "006", "ann-1")
    assert ann["text"] == "hi"
    assert ann["coordX"] is None and ann["coordY"] is None


def test_import_is_idempotent_by_default(tmp_path):
    src = str(tmp_path / "source")
    _write(
        os.path.join(src, "doc1", "remarks", "page_006.json"),
        [{"id": "ann-1", "text": "hi", "kind": "minor"}],
    )

    import_remarks.run(src, None, overwrite=False, dry_run=False)

    # Change the source file's text and re-run without --overwrite.
    _write(
        os.path.join(src, "doc1", "remarks", "page_006.json"),
        [{"id": "ann-1", "text": "changed", "kind": "minor"}],
    )
    totals = import_remarks.run(src, None, overwrite=False, dry_run=False)
    assert totals == {"docs": 1, "pages": 1, "imported": 0, "skipped": 1, "errors": 0}

    ann = db.get_remark("doc1", "006", "ann-1")
    assert ann["text"] == "hi"  # unchanged


def test_import_overwrite_updates_existing(tmp_path):
    src = str(tmp_path / "source")
    _write(
        os.path.join(src, "doc1", "remarks", "page_006.json"),
        [{"id": "ann-1", "text": "hi", "kind": "minor"}],
    )
    import_remarks.run(src, None, overwrite=False, dry_run=False)

    _write(
        os.path.join(src, "doc1", "remarks", "page_006.json"),
        [{"id": "ann-1", "text": "changed", "kind": "major", "coords": [5, 5]}],
    )
    totals = import_remarks.run(src, None, overwrite=True, dry_run=False)
    assert totals["imported"] == 1
    assert totals["skipped"] == 0

    ann = db.get_remark("doc1", "006", "ann-1")
    assert ann["text"] == "changed"
    assert ann["kind"] == "major"
    assert ann["coordX"] == 5


def test_import_dry_run_does_not_write(tmp_path):
    src = str(tmp_path / "source")
    _write(
        os.path.join(src, "doc1", "remarks", "page_006.json"),
        [{"id": "ann-1", "text": "hi", "kind": "minor"}],
    )
    totals = import_remarks.run(src, None, overwrite=False, dry_run=True)
    assert totals["imported"] == 1
    assert db.get_remark("doc1", "006", "ann-1") is None


def test_import_preserves_nonstandard_page_keys(tmp_path):
    src = str(tmp_path / "source")
    _write(os.path.join(src, "doc1", "remarks", "page_000.json"), [{"id": "a", "text": "t", "kind": "minor"}])
    _write(os.path.join(src, "doc1", "remarks", "page_-01.json"), [{"id": "b", "text": "cover", "kind": "minor"}])

    totals = import_remarks.run(src, None, overwrite=False, dry_run=False)
    assert totals["pages"] == 2
    assert db.get_remark("doc1", "000", "a") is not None
    assert db.get_remark("doc1", "-01", "b") is not None


def test_import_generates_id_when_missing(tmp_path):
    src = str(tmp_path / "source")
    _write(
        os.path.join(src, "doc1", "remarks", "page_006.json"),
        [{"text": "no id here", "kind": "minor"}],
    )
    totals = import_remarks.run(src, None, overwrite=False, dry_run=False)
    assert totals["imported"] == 1

    anns = db.list_page_remarks("doc1", "006")
    assert len(anns) == 1
    assert anns[0]["remarkId"].startswith("srv-import-")


def test_import_records_history_with_no_author(tmp_path):
    src = str(tmp_path / "source")
    _write(
        os.path.join(src, "doc1", "remarks", "page_006.json"),
        [{"id": "ann-1", "text": "hi", "kind": "minor"}],
    )
    import_remarks.run(src, None, overwrite=False, dry_run=False)

    conn = db.get_connection()
    row = conn.execute("SELECT action, author_id FROM remark_history WHERE remark_id = 'ann-1'").fetchone()
    assert row["action"] == "import"
    assert row["author_id"] is None


def test_import_filters_by_doc_id(tmp_path):
    src = str(tmp_path / "source")
    _write(os.path.join(src, "doc1", "remarks", "page_006.json"), [{"id": "a", "text": "t", "kind": "minor"}])
    _write(os.path.join(src, "doc2", "remarks", "page_006.json"), [{"id": "b", "text": "t", "kind": "minor"}])

    totals = import_remarks.run(src, "doc1", overwrite=False, dry_run=False)
    assert totals["docs"] == 1
    assert db.get_remark("doc1", "006", "a") is not None
    assert db.get_remark("doc2", "006", "b") is None


def test_import_bad_json_counts_as_error(tmp_path):
    src = str(tmp_path / "source")
    path = os.path.join(src, "doc1", "remarks", "page_006.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{ not valid json")

    totals = import_remarks.run(src, None, overwrite=False, dry_run=False)
    assert totals["errors"] == 1
    assert totals["imported"] == 0
