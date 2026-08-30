"""Бэкфилл состава изменения по ревизиям, записанным до появления колонки.

Скрипт восстанавливает `changes` диффом соседних снимков в цепочке одного
замечания. Проверяется главное: цепочка читается в порядке записи, чужие
замечания не смешиваются, повторный прогон ничего не меняет, а нечитаемый снимок
оставляет ревизию пустой, а не заполняет её догадкой.
"""

import json
import os

import pytest

pytest.importorskip("fastapi")

import backfill_history_changes as bhc  # noqa: E402
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


def _upsert(remark_id="ann-1", **kwargs):
    base = dict(doc_id="doc1", page_num="006", remark_id=remark_id,
                kind="minor", text="hello")
    base.update(kwargs)
    return db.upsert_remark_db(**base)


def _forget_changes():
    """Привести журнал к состоянию «до появления колонки»."""
    conn = db.get_connection()
    with db._lock:
        conn.execute("UPDATE remark_history SET changes = NULL")
        conn.commit()


def _changes(remark_id="ann-1"):
    return [h["changes"] for h in
            reversed(db.list_history(doc_id="doc1", remark_id=remark_id))]


def test_backfill_restores_the_composition_of_an_old_chain():
    _upsert(action="create", status="draft")
    _upsert(text="v2", status="draft")
    _upsert(text="v2", status="published")
    _upsert(text="v2", status="published", category="omission")
    expected = _changes()
    _forget_changes()
    assert _changes() == [None, None, None, None]

    assert bhc.main(["--apply"]) == 0
    assert _changes() == expected
    assert expected == [["create"], ["text"], ["publish"], ["category"]]


def test_dry_run_writes_nothing():
    _upsert(action="create")
    _upsert(text="v2")
    _forget_changes()
    assert bhc.main([]) == 0
    assert _changes() == [None, None]


def test_second_run_is_idempotent():
    _upsert(action="create")
    _upsert(text="v2")
    _forget_changes()
    bhc.main(["--apply"])
    first = _changes()
    bhc.main(["--apply"])
    assert _changes() == first


def test_already_filled_rows_are_left_alone():
    _upsert(action="create")
    _upsert(text="v2")
    # Ничего не забываем: скрипту нечего делать.
    assert bhc.compute() == []


def test_chains_of_different_remarks_do_not_bleed_into_each_other():
    _upsert(remark_id="ann-1", action="create", text="a")
    _upsert(remark_id="ann-2", action="create", text="b")
    _upsert(remark_id="ann-1", text="a2")
    _forget_changes()
    bhc.main(["--apply"])
    # У второго замечания одна ревизия, и она — создание, а не «правка текста»
    # относительно чужого снимка.
    assert _changes("ann-2") == [["create"]]
    assert _changes("ann-1") == [["create"], ["text"]]


def test_unreadable_snapshot_leaves_the_row_empty():
    _upsert(action="create")
    _upsert(text="v2")
    conn = db.get_connection()
    with db._lock:
        conn.execute("UPDATE remark_history SET changes = NULL")
        conn.execute("UPDATE remark_history SET snapshot = 'not json' "
                     "WHERE rev_no = 1")
        conn.commit()
    bhc.main(["--apply"])
    # Первая нечитаема, вторую не с чем сравнивать — обе остаются пустыми.
    assert _changes() == [None, None]
