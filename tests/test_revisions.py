"""Ревизии аннотаций: непрерывная нумерация, цепочка версий, резюме правки.

`annotation_history` — журнал ревизий, а строка в `annotations` — всего лишь
материализованная «голова» последней из них. На этом инварианте держатся
карточка комментария, «мои правки» и лента изменений.
"""

import json
import os
import sqlite3

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
    if db._conn is not None:
        db._conn.close()
    db._conn = None


def _upsert(**kwargs):
    base = dict(doc_id="doc1", page_num="006", ann_id="ann-1",
                ann_type="comment", text="hello")
    base.update(kwargs)
    return db.upsert_annotation_db(**base)


def _revisions(ann_id="ann-1"):
    # list_history отдаёт свежие первыми; для чтения истории удобнее наоборот.
    return list(reversed(db.list_history(doc_id="doc1", ann_id=ann_id)))


def test_revision_numbers_start_at_one_and_are_continuous():
    _upsert(action="create")
    _upsert(text="v2")
    _upsert(text="v3")
    assert [r["revNo"] for r in _revisions()] == [1, 2, 3]


def test_revisions_form_a_chain():
    _upsert(action="create")
    _upsert(text="v2")
    _upsert(text="v3")
    revs = _revisions()
    assert revs[0]["parentRevId"] is None
    assert revs[1]["parentRevId"] == revs[0]["id"]
    assert revs[2]["parentRevId"] == revs[1]["id"]


def test_numbering_is_per_annotation_not_global():
    _upsert(action="create")
    _upsert(ann_id="ann-2", action="create")
    _upsert(ann_id="ann-2", text="v2")
    assert [r["revNo"] for r in _revisions("ann-1")] == [1]
    assert [r["revNo"] for r in _revisions("ann-2")] == [1, 2]


def test_delete_is_a_revision_too():
    _upsert(action="create")
    db.soft_delete_annotation("doc1", "006", "ann-1")
    revs = _revisions()
    assert [r["action"] for r in revs] == ["create", "delete"]
    assert [r["revNo"] for r in revs] == [1, 2]
    assert revs[-1]["snapshot"]["status"] == "deleted"


def test_head_equals_the_latest_revision_snapshot():
    # Главный инвариант: строка в annotations не должна расходиться с журналом.
    _upsert(action="create", category="today", author_id=3)
    head = _upsert(text="edited", coord_x=11, coord_y=22, author_id=3)
    latest = _revisions()[-1]["snapshot"]
    for field in ("annId", "docId", "pageNum", "annType", "text", "coordX",
                  "coordY", "status", "category", "categorySource", "tags"):
        assert latest[field] == head[field], field


def test_summary_is_recorded_and_optional():
    _upsert(action="create", summary="первая версия")
    _upsert(text="v2")
    revs = _revisions()
    assert revs[0]["summary"] == "первая версия"
    assert revs[1]["summary"] is None


def test_agent_run_is_recorded_on_the_revision():
    _upsert(action="create", agent_run_id=17, summary="категория по разбору текста")
    assert _revisions()[0]["agentRunId"] == 17


def test_legacy_rows_get_numbered_on_migration(tmp_path, monkeypatch):
    """База, записанная до появления rev_no, нумеруется при следующем init_db.

    Порядок берётся из id, а не из created_at: у пакетного импорта метки
    времени совпадают, а id — это порядок записи.
    """
    db._conn.close()
    db._conn = None
    legacy = os.path.join(tmp_path, "legacy.db")
    conn = sqlite3.connect(legacy)
    conn.executescript(
        """
        CREATE TABLE annotation_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          doc_id TEXT NOT NULL, page_num TEXT NOT NULL, ann_id TEXT NOT NULL,
          action TEXT NOT NULL, snapshot TEXT NOT NULL,
          author_id INTEGER, created_at TEXT NOT NULL
        );
        """
    )
    same_time = "2026-08-01T00:00:00"
    for ann_id, action in (("a", "create"), ("a", "update"), ("b", "create"), ("a", "update")):
        conn.execute(
            "INSERT INTO annotation_history (doc_id, page_num, ann_id, action, snapshot,"
            " author_id, created_at) VALUES ('doc1','006',?,?,?,NULL,?)",
            (ann_id, action, json.dumps({}), same_time),
        )
    conn.commit()
    conn.close()

    monkeypatch.setattr(config, "DB_PATH", legacy)
    db.init_db()
    assert [r["revNo"] for r in reversed(db.list_history(doc_id="doc1", ann_id="a"))] == [1, 2, 3]
    assert [r["revNo"] for r in db.list_history(doc_id="doc1", ann_id="b")] == [1]

    # Идемпотентность: повторный init_db ничего не пересчитывает и не ломает.
    db.init_db()
    assert [r["revNo"] for r in reversed(db.list_history(doc_id="doc1", ann_id="a"))] == [1, 2, 3]


def test_new_revisions_continue_after_a_backfill(tmp_path, monkeypatch):
    _upsert(action="create")
    _upsert(text="v2")
    # Симулируем «старые строки без номера» и повторную миграцию.
    conn = db.get_connection()
    with db._lock:
        conn.execute("UPDATE annotation_history SET rev_no = NULL, parent_rev_id = NULL")
        conn.commit()
        db._backfill_revision_numbers(conn)
        conn.commit()
    _upsert(text="v3")
    assert [r["revNo"] for r in _revisions()] == [1, 2, 3]
