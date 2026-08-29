"""
Unit tests for scripts/api/export_remarks.py (stage 2, A2.7).
"""

import json
import os
import stat

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402
import export_remarks  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db_path = os.path.join(tmp_path, "redpen.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.init_db()
    yield
    db._conn.close()
    db._conn = None


def test_export_writes_bare_array_files(tmp_path):
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "hi", coord_x=1, coord_y=2)
    db.upsert_remark_db("doc1", "007", "ann-1", "major", "note", coord_x=3, coord_y=4)
    db.upsert_remark_db("doc2", "-01", "ann-1", "minor", "cover")

    out = str(tmp_path / "export")
    count = export_remarks.run(out)
    assert count == 3

    with open(os.path.join(out, "doc1", "remarks", "page_006.json"), encoding="utf-8") as f:
        data = json.load(f)
    assert data == [{
        "id": "ann-1", "text": "hi", "kind": "minor", "coords": [1, 2],
        "category": "other",
    }]

    # tempfile.mkstemp() defaults to mode 0600 (owner-only); exported files
    # must stay world-readable (e.g. once committed/synced elsewhere).
    path = os.path.join(out, "doc1", "remarks", "page_006.json")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode & stat.S_IROTH, f"expected world-readable, got {oct(mode)}"

    assert os.path.exists(os.path.join(out, "doc2", "remarks", "page_-01.json"))


def test_export_excludes_deleted_remarks(tmp_path):
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "one")
    db.upsert_remark_db("doc1", "006", "ann-2", "minor", "two")
    db.soft_delete_remark("doc1", "006", "ann-1")

    out = str(tmp_path / "export")
    export_remarks.run(out)

    with open(os.path.join(out, "doc1", "remarks", "page_006.json"), encoding="utf-8") as f:
        data = json.load(f)
    assert [a["id"] for a in data] == ["ann-2"]


def test_export_filters_by_doc_id(tmp_path):
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "one")
    db.upsert_remark_db("doc2", "006", "ann-1", "minor", "one")

    out = str(tmp_path / "export")
    count = export_remarks.run(out, "doc1")
    assert count == 1
    assert os.path.exists(os.path.join(out, "doc1", "remarks", "page_006.json"))
    assert not os.path.exists(os.path.join(out, "doc2", "remarks", "page_006.json"))
