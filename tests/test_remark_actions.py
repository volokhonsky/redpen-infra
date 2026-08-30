"""Действия над замечанием: словарь типов и состав изменения в журнале.

Колонка `action` отвечает на вопрос «как была сделана запись», `changes` — на
вопрос «что изменилось». Второй вопрос и есть предмет этих тестов: одно
сохранение может менять несколько вещей сразу, и приёмка параграфа опирается на
то, что правку текста видно отдельно от служебных переходов.
"""

import json
import os

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402
import remark_actions  # noqa: E402


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
    base = dict(doc_id="doc1", page_num="006", remark_id="ann-1",
                kind="minor", text="hello")
    base.update(kwargs)
    return db.upsert_remark_db(**base)


def _last_changes(remark_id="ann-1"):
    items = db.list_history(doc_id="doc1", remark_id=remark_id, limit=1)
    return items[0]["changes"]


def _all_changes(remark_id="ann-1"):
    return [h["changes"] for h in
            reversed(db.list_history(doc_id="doc1", remark_id=remark_id))]


# --- чистый словарь, без БД ------------------------------------------------

def test_first_revision_is_create():
    assert remark_actions.diff_snapshots(None, {"text": "a"}) == ["create"]


def test_no_change_yields_no_actions():
    snap = {"text": "a", "status": "draft", "category": "other", "tags": ["x"]}
    assert remark_actions.diff_snapshots(snap, dict(snap)) == []


@pytest.mark.parametrize("field,before,after,token", [
    ("text", "a", "b", "text"),
    ("kind", "minor", "major", "kind"),
    ("category", "other", "omission", "category"),
])
def test_single_field_yields_single_token(field, before, after, token):
    prev = {field: before}
    cur = {field: after}
    assert remark_actions.diff_snapshots(prev, cur) == [token]


def test_coords_compare_as_a_pair():
    prev = {"coordX": 10, "coordY": 20}
    assert remark_actions.diff_snapshots(prev, {"coordX": 10, "coordY": 20}) == []
    assert remark_actions.diff_snapshots(prev, {"coordX": 11, "coordY": 20}) == ["coords"]


def test_tags_compare_as_sets_not_lists():
    prev = {"tags": ["a", "b"]}
    # Тот же набор в другом порядке — не изменение.
    assert remark_actions.diff_snapshots(prev, {"tags": ["b", "a"]}) == []
    assert remark_actions.diff_snapshots(prev, {"tags": ["a"]}) == ["tags"]


@pytest.mark.parametrize("before,after,token", [
    ("draft", "published", "publish"),
    ("published", "draft", "unpublish"),
    ("published", "deleted", "delete"),
    ("draft", "deleted", "delete"),
    ("deleted", "published", "restore"),
    ("deleted", "draft", "restore"),
])
def test_status_transitions_are_directed(before, after, token):
    assert remark_actions.diff_snapshots({"status": before}, {"status": after}) == [token]


def test_combined_save_yields_several_tokens_in_dictionary_order():
    prev = {"text": "a", "status": "draft", "category": "other"}
    cur = {"text": "b", "status": "published", "category": "omission"}
    assert remark_actions.diff_snapshots(prev, cur) == ["text", "publish", "category"]


def test_revert_provenance_is_added_to_the_actual_changes():
    assert remark_actions.with_provenance("revert", ["text"]) == ["text", "revert"]
    # import и backfill описывают инструмент, а не изменение: токенами не становятся.
    assert remark_actions.with_provenance("import", ["text"]) == ["text"]


def test_label_of_unknown_composition_does_not_guess():
    assert remark_actions.label(None) == remark_actions.UNKNOWN_LABEL
    assert remark_actions.label([]) == "без изменений"
    assert remark_actions.label(["text", "publish"]) == "правка текста, публикация"


def test_content_edit_flag():
    assert remark_actions.is_content_edit(["text"]) is True
    assert remark_actions.is_content_edit(["coords"]) is True
    assert remark_actions.is_content_edit(["publish", "category"]) is False
    # Старая ревизия — состав неизвестен, а не «правка текста».
    assert remark_actions.is_content_edit(None) is False


# --- запись в журнал -------------------------------------------------------

def test_history_records_composition_on_write():
    _upsert(action="create", status="draft")
    assert _last_changes() == ["create"]

    _upsert(text="v2", status="draft")
    assert _last_changes() == ["text"]

    _upsert(text="v2", status="published")
    assert _last_changes() == ["publish"]

    _upsert(text="v3", status="published", category="omission")
    assert _last_changes() == ["text", "category"]


def test_tag_only_save_is_not_a_text_edit():
    _upsert(action="create", tags=["alpha"])
    _upsert(tags=["alpha", "beta"])
    assert _last_changes() == ["tags"]
    assert remark_actions.is_content_edit(_last_changes()) is False


def test_soft_delete_records_delete_token():
    _upsert(action="create", status="published")
    db.soft_delete_remark("doc1", "006", "ann-1")
    assert _last_changes() == ["delete"]


def test_restore_after_delete():
    _upsert(action="create", status="published")
    db.soft_delete_remark("doc1", "006", "ann-1")
    _upsert(status="published")
    assert _last_changes() == ["restore"]


def test_repeated_identical_save_records_empty_composition():
    _upsert(action="create", status="draft")
    _upsert(status="draft")
    # Ревизия записана (журнал не теряет попыток), но действий в ней нет.
    assert _last_changes() == []


def test_set_status_db_writes_a_revision_and_keeps_authorship():
    _upsert(action="create", status="draft", author_id=None)
    ann = db.set_status_db("doc1", "006", "ann-1", "published", author_id=None)
    assert ann["status"] == "published"
    assert _last_changes() == ["publish"]


def test_set_category_db_records_source_and_change():
    _upsert(action="create")
    db.set_category_db("doc1", "006", "ann-1", "omission")
    assert _last_changes() == ["category"]
    ann = db.get_remark("doc1", "006", "ann-1")
    assert ann["category"] == "omission"
    assert ann["categorySource"] == "human"


def test_set_tags_db_records_change():
    _upsert(action="create", tags=["a"])
    db.set_tags_db("doc1", "006", "ann-1", ["a", "b"])
    assert _last_changes() == ["tags"]
    assert db.get_remark("doc1", "006", "ann-1")["tags"] == ["a", "b"]


def test_history_label_comes_from_the_server():
    _upsert(action="create", status="draft")
    _upsert(status="published")
    assert db.list_history(doc_id="doc1", limit=1)[0]["actionLabel"] == "публикация"


def test_changed_filter_selects_by_composition():
    _upsert(action="create", status="draft")            # create
    _upsert(text="v2", status="draft")                  # text
    _upsert(text="v2", status="published")              # publish

    only_text = db.list_history(doc_id="doc1", changed="text")
    assert [h["revNo"] for h in only_text] == [2]

    only_publish = db.list_history(doc_id="doc1", changed="publish")
    assert [h["revNo"] for h in only_publish] == [3]

    assert db.list_history(doc_id="doc1", changed="tags") == []


def test_old_revisions_without_composition_survive_reads():
    _upsert(action="create")
    conn = db.get_connection()
    with db._lock:
        conn.execute("UPDATE remark_history SET changes = NULL")
        conn.commit()
    item = db.list_history(doc_id="doc1", limit=1)[0]
    assert item["changes"] is None
    assert item["actionLabel"] == remark_actions.UNKNOWN_LABEL
    # Фильтр по составу такие строки не показывает: догадка хуже пропуска.
    assert db.list_history(doc_id="doc1", changed="create") == []


def test_get_history_record_matches_list_history():
    _upsert(action="create")
    _upsert(text="v2", summary="поправил формулировку")
    listed = db.list_history(doc_id="doc1", limit=1)[0]
    fetched = db.get_history_record(listed["id"])
    assert fetched == listed


def test_delete_can_be_attributed_to_an_agent_run():
    _upsert(action="create", status="published")
    db.soft_delete_remark("doc1", "006", "ann-1", agent_run_id=42)
    assert db.list_history(doc_id="doc1", limit=1)[0]["agentRunId"] == 42
