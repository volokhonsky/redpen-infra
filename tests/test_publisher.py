"""
Unit tests for scripts/api/publisher.py (stage 2: rendering the SQLite
annotations store into the static bare-array JSON the viewer reads).
"""

import json
import os
import stat

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402
import publisher  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db_path = os.path.join(tmp_path, "redpen.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "PUBLISH_DIR", os.path.join(tmp_path, "public"))
    db.init_db()
    yield
    db._conn.close()
    db._conn = None


def test_render_page_bare_array_with_coords():
    db.upsert_annotation_db("doc1", "006", "ann-1", "main", "hi", coord_x=10, coord_y=20)
    rendered = publisher.render_page("doc1", "006")
    assert rendered == [{"id": "ann-1", "text": "hi", "annType": "main", "coords": [10, 20]}]


def test_render_page_general_annotation_has_no_coords():
    db.upsert_annotation_db("doc1", "006", "ann-1", "general", "note")
    rendered = publisher.render_page("doc1", "006")
    assert rendered == [{"id": "ann-1", "text": "note", "annType": "general"}]
    assert "coords" not in rendered[0]


def test_render_page_excludes_deleted():
    db.upsert_annotation_db("doc1", "006", "ann-1", "comment", "one")
    db.upsert_annotation_db("doc1", "006", "ann-2", "comment", "two")
    db.soft_delete_annotation("doc1", "006", "ann-1")
    rendered = publisher.render_page("doc1", "006")
    assert [a["id"] for a in rendered] == ["ann-2"]


def test_compute_page_sha_is_deterministic():
    rendered = [{"id": "a", "text": "x", "annType": "comment"}]
    assert publisher.compute_page_sha(rendered) == publisher.compute_page_sha(list(rendered))


def test_compute_page_sha_changes_with_content():
    a = [{"id": "a", "text": "x", "annType": "comment"}]
    b = [{"id": "a", "text": "y", "annType": "comment"}]
    assert publisher.compute_page_sha(a) != publisher.compute_page_sha(b)


def test_publish_page_disabled_returns_false(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PUBLISH_DIR", "")
    db.upsert_annotation_db("doc1", "006", "ann-1", "comment", "one")
    assert publisher.publish_page("doc1", "006") is False


def test_publish_page_writes_valid_bare_array(tmp_path):
    db.upsert_annotation_db("doc1", "006", "ann-1", "comment", "one", coord_x=1, coord_y=2)
    ok = publisher.publish_page("doc1", "006")
    assert ok is True

    path = os.path.join(config.PUBLISH_DIR, "doc1", "annotations", "page_006.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert data == [{"id": "ann-1", "text": "one", "annType": "comment", "coords": [1, 2]}]


def test_publish_page_writes_world_readable_file(tmp_path):
    # PUBLISH_DIR is served directly by nginx (a different uid than the api
    # container). tempfile.mkstemp() defaults to mode 0600 (owner-only); the
    # published file must stay world-readable or nginx 403s on it.
    db.upsert_annotation_db("doc1", "006", "ann-1", "comment", "one")
    publisher.publish_page("doc1", "006")
    path = os.path.join(config.PUBLISH_DIR, "doc1", "annotations", "page_006.json")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode & stat.S_IROTH, f"expected world-readable, got {oct(mode)}"


def test_publish_page_general_has_no_coords_key_in_file(tmp_path):
    db.upsert_annotation_db("doc1", "006", "ann-1", "general", "note")
    publisher.publish_page("doc1", "006")
    path = os.path.join(config.PUBLISH_DIR, "doc1", "annotations", "page_006.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert "coords" not in data[0]


def test_publish_page_is_idempotent(tmp_path):
    db.upsert_annotation_db("doc1", "006", "ann-1", "comment", "one")
    publisher.publish_page("doc1", "006")
    path = os.path.join(config.PUBLISH_DIR, "doc1", "annotations", "page_006.json")
    with open(path, encoding="utf-8") as f:
        first = f.read()
    publisher.publish_page("doc1", "006")
    with open(path, encoding="utf-8") as f:
        second = f.read()
    assert first == second


def test_render_drafts_bare_array_with_draft_flag():
    db.upsert_annotation_db("doc1", "006", "d-1", "comment", "wip", coord_x=3, coord_y=4, status="draft")
    rendered = publisher.render_drafts("doc1", "006")
    assert rendered == [{"id": "d-1", "text": "wip", "annType": "comment", "draft": True, "coords": [3, 4]}]


def test_render_drafts_excludes_published():
    db.upsert_annotation_db("doc1", "006", "p-1", "comment", "live")
    db.upsert_annotation_db("doc1", "006", "d-1", "comment", "wip", status="draft")
    assert [a["id"] for a in publisher.render_drafts("doc1", "006")] == ["d-1"]


def test_render_page_excludes_drafts():
    db.upsert_annotation_db("doc1", "006", "p-1", "comment", "live")
    db.upsert_annotation_db("doc1", "006", "d-1", "comment", "wip", status="draft")
    assert [a["id"] for a in publisher.render_page("doc1", "006")] == ["p-1"]


def test_publish_page_writes_drafts_sidecar(tmp_path):
    db.upsert_annotation_db("doc1", "006", "p-1", "comment", "live")
    db.upsert_annotation_db("doc1", "006", "d-1", "comment", "wip", coord_x=1, coord_y=2, status="draft")
    assert publisher.publish_page("doc1", "006") is True

    drafts_path = os.path.join(config.PUBLISH_DIR, "doc1", "annotations", "page_006.drafts.json")
    with open(drafts_path, encoding="utf-8") as f:
        data = json.load(f)
    assert data == [{"id": "d-1", "text": "wip", "annType": "comment", "draft": True, "coords": [1, 2]}]
    # Published file stays drafts-free so serverPageSha is unaffected.
    main_path = os.path.join(config.PUBLISH_DIR, "doc1", "annotations", "page_006.json")
    with open(main_path, encoding="utf-8") as f:
        assert [a["id"] for a in json.load(f)] == ["p-1"]


def test_publish_page_no_drafts_writes_no_sidecar(tmp_path):
    db.upsert_annotation_db("doc1", "006", "p-1", "comment", "live")
    publisher.publish_page("doc1", "006")
    drafts_path = os.path.join(config.PUBLISH_DIR, "doc1", "annotations", "page_006.drafts.json")
    assert not os.path.exists(drafts_path)


def test_publish_page_removes_stale_drafts_sidecar(tmp_path):
    db.upsert_annotation_db("doc1", "006", "d-1", "comment", "wip", status="draft")
    publisher.publish_page("doc1", "006")
    drafts_path = os.path.join(config.PUBLISH_DIR, "doc1", "annotations", "page_006.drafts.json")
    assert os.path.exists(drafts_path)

    # Promote the draft to published: the sidecar must be dropped.
    db.upsert_annotation_db("doc1", "006", "d-1", "comment", "wip", status="published")
    publisher.publish_page("doc1", "006")
    assert not os.path.exists(drafts_path)


def test_publish_all_counts_pages(tmp_path):
    db.upsert_annotation_db("doc1", "006", "ann-1", "comment", "one")
    db.upsert_annotation_db("doc1", "007", "ann-1", "comment", "one")
    db.upsert_annotation_db("doc2", "-01", "ann-1", "comment", "cover")

    result = publisher.publish_all()
    assert result == {"pages": 3, "failed": 0}

    for doc_id, page_num in [("doc1", "006"), ("doc1", "007"), ("doc2", "-01")]:
        path = os.path.join(config.PUBLISH_DIR, doc_id, "annotations", f"page_{page_num}.json")
        assert os.path.exists(path)
