"""
Unit tests for scripts/api/export_annotations.py (stage 2, A2.7).
"""

import json
import os
import stat

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402
import export_annotations  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db_path = os.path.join(tmp_path, "redpen.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.init_db()
    yield
    db._conn.close()
    db._conn = None


def test_export_writes_bare_array_files(tmp_path):
    db.upsert_annotation_db("doc1", "006", "ann-1", "comment", "hi", coord_x=1, coord_y=2)
    db.upsert_annotation_db("doc1", "007", "ann-1", "general", "note")
    db.upsert_annotation_db("doc2", "-01", "ann-1", "comment", "cover")

    out = str(tmp_path / "export")
    count = export_annotations.run(out)
    assert count == 3

    with open(os.path.join(out, "doc1", "annotations", "page_006.json"), encoding="utf-8") as f:
        data = json.load(f)
    assert data == [{"id": "ann-1", "text": "hi", "annType": "comment", "coords": [1, 2]}]

    # tempfile.mkstemp() defaults to mode 0600 (owner-only); exported files
    # must stay world-readable (e.g. once committed/synced elsewhere).
    path = os.path.join(out, "doc1", "annotations", "page_006.json")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode & stat.S_IROTH, f"expected world-readable, got {oct(mode)}"

    assert os.path.exists(os.path.join(out, "doc2", "annotations", "page_-01.json"))


def test_export_excludes_deleted_annotations(tmp_path):
    db.upsert_annotation_db("doc1", "006", "ann-1", "comment", "one")
    db.upsert_annotation_db("doc1", "006", "ann-2", "comment", "two")
    db.soft_delete_annotation("doc1", "006", "ann-1")

    out = str(tmp_path / "export")
    export_annotations.run(out)

    with open(os.path.join(out, "doc1", "annotations", "page_006.json"), encoding="utf-8") as f:
        data = json.load(f)
    assert [a["id"] for a in data] == ["ann-2"]


def test_export_filters_by_doc_id(tmp_path):
    db.upsert_annotation_db("doc1", "006", "ann-1", "comment", "one")
    db.upsert_annotation_db("doc2", "006", "ann-1", "comment", "one")

    out = str(tmp_path / "export")
    count = export_annotations.run(out, "doc1")
    assert count == 1
    assert os.path.exists(os.path.join(out, "doc1", "annotations", "page_006.json"))
    assert not os.path.exists(os.path.join(out, "doc2", "annotations", "page_006.json"))
