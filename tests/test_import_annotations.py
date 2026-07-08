"""
Unit tests for scripts/api/import_annotations.py (stage 2, A2.3).
"""

import json
import os

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402
import import_annotations  # noqa: E402


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
        os.path.join(src, "doc1", "annotations", "page_006.json"),
        [{"id": "ann-1", "text": "hi", "annType": "main", "coords": [10, 20]}],
    )

    totals = import_annotations.run(src, None, overwrite=False, dry_run=False)
    assert totals == {"docs": 1, "pages": 1, "imported": 1, "skipped": 0, "errors": 0}

    ann = db.get_annotation("doc1", "006", "ann-1")
    assert ann["text"] == "hi"
    assert ann["annType"] == "main"
    assert ann["coordX"] == 10 and ann["coordY"] == 20


def test_import_legacy_page_object_format(tmp_path):
    src = str(tmp_path / "source")
    _write(
        os.path.join(src, "doc1", "annotations", "page_006.json"),
        {
            "pageId": "doc1_page_006",
            "serverPageSha": "abc",
            "annotations": [{"id": "ann-1", "text": "hi", "annType": "comment"}],
        },
    )

    totals = import_annotations.run(src, None, overwrite=False, dry_run=False)
    assert totals["imported"] == 1
    ann = db.get_annotation("doc1", "006", "ann-1")
    assert ann["text"] == "hi"
    assert ann["coordX"] is None and ann["coordY"] is None


def test_import_is_idempotent_by_default(tmp_path):
    src = str(tmp_path / "source")
    _write(
        os.path.join(src, "doc1", "annotations", "page_006.json"),
        [{"id": "ann-1", "text": "hi", "annType": "comment"}],
    )

    import_annotations.run(src, None, overwrite=False, dry_run=False)

    # Change the source file's text and re-run without --overwrite.
    _write(
        os.path.join(src, "doc1", "annotations", "page_006.json"),
        [{"id": "ann-1", "text": "changed", "annType": "comment"}],
    )
    totals = import_annotations.run(src, None, overwrite=False, dry_run=False)
    assert totals == {"docs": 1, "pages": 1, "imported": 0, "skipped": 1, "errors": 0}

    ann = db.get_annotation("doc1", "006", "ann-1")
    assert ann["text"] == "hi"  # unchanged


def test_import_overwrite_updates_existing(tmp_path):
    src = str(tmp_path / "source")
    _write(
        os.path.join(src, "doc1", "annotations", "page_006.json"),
        [{"id": "ann-1", "text": "hi", "annType": "comment"}],
    )
    import_annotations.run(src, None, overwrite=False, dry_run=False)

    _write(
        os.path.join(src, "doc1", "annotations", "page_006.json"),
        [{"id": "ann-1", "text": "changed", "annType": "main", "coords": [5, 5]}],
    )
    totals = import_annotations.run(src, None, overwrite=True, dry_run=False)
    assert totals["imported"] == 1
    assert totals["skipped"] == 0

    ann = db.get_annotation("doc1", "006", "ann-1")
    assert ann["text"] == "changed"
    assert ann["annType"] == "main"
    assert ann["coordX"] == 5


def test_import_dry_run_does_not_write(tmp_path):
    src = str(tmp_path / "source")
    _write(
        os.path.join(src, "doc1", "annotations", "page_006.json"),
        [{"id": "ann-1", "text": "hi", "annType": "comment"}],
    )
    totals = import_annotations.run(src, None, overwrite=False, dry_run=True)
    assert totals["imported"] == 1
    assert db.get_annotation("doc1", "006", "ann-1") is None


def test_import_preserves_nonstandard_page_keys(tmp_path):
    src = str(tmp_path / "source")
    _write(os.path.join(src, "doc1", "annotations", "page_000.json"), [{"id": "a", "text": "t", "annType": "comment"}])
    _write(os.path.join(src, "doc1", "annotations", "page_-01.json"), [{"id": "b", "text": "cover", "annType": "comment"}])

    totals = import_annotations.run(src, None, overwrite=False, dry_run=False)
    assert totals["pages"] == 2
    assert db.get_annotation("doc1", "000", "a") is not None
    assert db.get_annotation("doc1", "-01", "b") is not None


def test_import_generates_id_when_missing(tmp_path):
    src = str(tmp_path / "source")
    _write(
        os.path.join(src, "doc1", "annotations", "page_006.json"),
        [{"text": "no id here", "annType": "comment"}],
    )
    totals = import_annotations.run(src, None, overwrite=False, dry_run=False)
    assert totals["imported"] == 1

    anns = db.list_page_annotations("doc1", "006")
    assert len(anns) == 1
    assert anns[0]["annId"].startswith("srv-import-")


def test_import_records_history_with_no_author(tmp_path):
    src = str(tmp_path / "source")
    _write(
        os.path.join(src, "doc1", "annotations", "page_006.json"),
        [{"id": "ann-1", "text": "hi", "annType": "comment"}],
    )
    import_annotations.run(src, None, overwrite=False, dry_run=False)

    conn = db.get_connection()
    row = conn.execute("SELECT action, author_id FROM annotation_history WHERE ann_id = 'ann-1'").fetchone()
    assert row["action"] == "import"
    assert row["author_id"] is None


def test_import_filters_by_doc_id(tmp_path):
    src = str(tmp_path / "source")
    _write(os.path.join(src, "doc1", "annotations", "page_006.json"), [{"id": "a", "text": "t", "annType": "comment"}])
    _write(os.path.join(src, "doc2", "annotations", "page_006.json"), [{"id": "b", "text": "t", "annType": "comment"}])

    totals = import_annotations.run(src, "doc1", overwrite=False, dry_run=False)
    assert totals["docs"] == 1
    assert db.get_annotation("doc1", "006", "a") is not None
    assert db.get_annotation("doc2", "006", "b") is None


def test_import_bad_json_counts_as_error(tmp_path):
    src = str(tmp_path / "source")
    path = os.path.join(src, "doc1", "annotations", "page_006.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{ not valid json")

    totals = import_annotations.run(src, None, overwrite=False, dry_run=False)
    assert totals["errors"] == 1
    assert totals["imported"] == 0
