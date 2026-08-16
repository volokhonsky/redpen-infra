"""
Tests for scripts/api/migrate_general_annotations.py.

This script rewrites published content on prod, so the important cases are the
refusals: it must not run against a database that has drifted from the map.
"""

import json
import os

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402
import migrate_general_annotations as mig  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", os.path.join(tmp_path, "redpen.db"))
    monkeypatch.setattr(config, "PUBLISH_DIR", os.path.join(tmp_path, "public"))
    db._conn = None
    db.init_db()
    yield
    db._conn = None


def _plan(tmp_path, convert=(), delete=()):
    path = tmp_path / "map.json"
    path.write_text(json.dumps({
        "docId": "doc1",
        "convert": list(convert),
        "delete": list(delete),
    }, ensure_ascii=False), encoding="utf-8")
    return str(path)


CONVERT = {"pageKey": "006", "annId": "ann-1", "annType": "main", "coords": [430, 215], "anchor": "тест"}


def test_converts_general_to_anchored(tmp_path):
    db.upsert_annotation_db("doc1", "006", "ann-1", "general", "текст")
    mig.migrate(mig.load_map(_plan(tmp_path, convert=[CONVERT])), apply=True)

    ann = db.get_annotation("doc1", "006", "ann-1")
    assert ann["annType"] == "main"
    assert (ann["coordX"], ann["coordY"]) == (430, 215)
    assert ann["text"] == "текст"


def test_draft_stays_a_draft(tmp_path):
    """8 of the 14 real rows are drafts; publishing them by accident would put
    unreviewed text on the live site."""
    db.upsert_annotation_db("doc1", "006", "ann-1", "general", "текст", status="draft")
    mig.migrate(mig.load_map(_plan(tmp_path, convert=[CONVERT])), apply=True)
    assert db.get_annotation("doc1", "006", "ann-1")["status"] == "draft"


def test_tags_are_preserved(tmp_path):
    db.upsert_annotation_db("doc1", "006", "ann-1", "general", "текст", tags=["omission", "confidence:high"])
    mig.migrate(mig.load_map(_plan(tmp_path, convert=[CONVERT])), apply=True)
    assert sorted(db.get_annotation("doc1", "006", "ann-1")["tags"]) == ["confidence:high", "omission"]


def test_change_is_recorded_in_history(tmp_path):
    db.upsert_annotation_db("doc1", "006", "ann-1", "general", "текст")
    before = len(db.list_history(doc_id="doc1"))
    mig.migrate(mig.load_map(_plan(tmp_path, convert=[CONVERT])), apply=True)
    assert len(db.list_history(doc_id="doc1")) > before


def test_delete_is_soft(tmp_path):
    db.upsert_annotation_db("doc1", "017", "junk", "general", "Бубубу.")
    plan = _plan(tmp_path, delete=[{"pageKey": "017", "annId": "junk", "why": "мусор"}])
    mig.migrate(mig.load_map(plan), apply=True)
    assert db.get_annotation("doc1", "017", "junk")["status"] == "deleted"


def test_dry_run_changes_nothing(tmp_path):
    db.upsert_annotation_db("doc1", "006", "ann-1", "general", "текст")
    mig.migrate(mig.load_map(_plan(tmp_path, convert=[CONVERT])), apply=False)
    assert db.get_annotation("doc1", "006", "ann-1")["annType"] == "general"


def test_refuses_when_a_listed_row_is_not_general(tmp_path):
    """Someone edited it between planning and running."""
    db.upsert_annotation_db("doc1", "006", "ann-1", "comment", "текст", coord_x=1, coord_y=2)
    with pytest.raises(mig.MigrationError, match="not general"):
        mig.migrate(mig.load_map(_plan(tmp_path, convert=[CONVERT])), apply=True)


def test_refuses_when_a_general_row_is_not_in_the_map(tmp_path):
    """The map must be exhaustive, otherwise the type cannot be dropped from
    the API afterwards."""
    db.upsert_annotation_db("doc1", "006", "ann-1", "general", "текст")
    db.upsert_annotation_db("doc1", "007", "ann-2", "general", "забытая")
    with pytest.raises(mig.MigrationError, match="does not mention"):
        mig.migrate(mig.load_map(_plan(tmp_path, convert=[CONVERT])), apply=True)


def test_refusal_leaves_the_database_untouched(tmp_path):
    db.upsert_annotation_db("doc1", "006", "ann-1", "general", "текст")
    db.upsert_annotation_db("doc1", "007", "ann-2", "general", "забытая")
    with pytest.raises(mig.MigrationError):
        mig.migrate(mig.load_map(_plan(tmp_path, convert=[CONVERT])), apply=True)
    assert db.get_annotation("doc1", "006", "ann-1")["annType"] == "general"


@pytest.mark.parametrize("bad", [
    {"pageKey": "006", "annId": "x", "annType": "general", "coords": [1, 2]},
    {"pageKey": "006", "annId": "x", "annType": "main", "coords": [1]},
    {"pageKey": "006", "annId": "x", "annType": "main", "coords": "1,2"},
])
def test_invalid_map_entries_are_rejected(tmp_path, bad):
    with pytest.raises(mig.MigrationError):
        mig.load_map(_plan(tmp_path, convert=[bad]))


def test_real_map_is_valid_and_exhaustive():
    """The checked-in map must parse and cover exactly 16 rows."""
    plan = mig.load_map(mig.DEFAULT_MAP)
    assert plan["docId"] == "medinsky11klass"
    assert len(plan["convert"]) + len(plan["delete"]) == 16
    ids = [e["annId"] for e in plan["convert"]] + [e["annId"] for e in plan["delete"]]
    assert len(set(ids)) == 16
