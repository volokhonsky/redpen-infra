"""Источник категории: `category_source` / `category_set_by`.

Колонка `category` не может ответить на вопрос «разобрана ли аннотация»:
дефолт `other` означает и честное «не приём, а пояснение» (13 % корпуса), и
«никто этим не занимался». Приёмка держится ровно на этом различии, поэтому
источник — отдельное служебное поле, в статику не попадающее.
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


def _upsert(**kwargs):
    base = dict(doc_id="doc1", page_num="006", remark_id="ann-1",
                kind="minor", text="hello")
    base.update(kwargs)
    return db.upsert_remark_db(**base)


def test_new_remark_without_category_is_unclassified():
    ann = _upsert(action="create")
    assert ann["category"] == "other"
    assert ann["categorySource"] == "default"
    assert ann["categorySetBy"] is None


def test_explicit_category_defaults_to_human():
    ann = _upsert(action="create", category="omission", author_id=7)
    assert ann["category"] == "omission"
    assert ann["categorySource"] == "human"
    assert ann["categorySetBy"] == 7


def test_explicit_other_is_a_decision_not_a_default():
    # Главный смысл поля: человек, сознательно выбравший «Прочее», отличается
    # от аннотации, которой никто не занимался, хотя категория у них одна.
    ann = _upsert(action="create", category="other", author_id=7)
    assert ann["category"] == "other"
    assert ann["categorySource"] == "human"


def test_agent_and_backfill_name_themselves():
    ann = _upsert(action="create", category="today",
                  category_source="agent", author_id=42)
    assert ann["categorySource"] == "agent"
    assert ann["categorySetBy"] == 42

    ann = _upsert(category="sides", category_source="tags-backfill")
    assert ann["categorySource"] == "tags-backfill"


def test_saving_without_category_does_not_promote_a_guess():
    _upsert(action="create", category="today", category_source="tags-backfill")
    # Обычное сохранение текста из редактора: ключа category нет вовсе.
    ann = _upsert(text="edited", author_id=9)
    assert ann["text"] == "edited"
    assert ann["category"] == "today"
    assert ann["categorySource"] == "tags-backfill"


def test_reclassifying_moves_source_and_setter():
    _upsert(action="create", category="today", category_source="agent", author_id=42)
    ann = _upsert(category="sides", author_id=9)
    assert ann["category"] == "sides"
    assert ann["categorySource"] == "human"
    assert ann["categorySetBy"] == 9


def test_source_without_category_is_refused():
    with pytest.raises(ValueError):
        _upsert(action="create", category_source="human")


def test_unknown_source_is_refused():
    with pytest.raises(ValueError):
        _upsert(action="create", category="today", category_source="oracle")


def test_history_snapshot_carries_the_source():
    _upsert(action="create", category="today", category_source="agent", author_id=42)
    items = db.list_history(doc_id="doc1", remark_id="ann-1")
    assert items[0]["snapshot"]["categorySource"] == "agent"


def test_source_never_reaches_the_published_json():
    import publisher
    _upsert(action="create", category="today", category_source="agent", author_id=42)
    rendered = publisher.render_page_static("doc1", "006")
    assert rendered[0]["category"] == "today"
    assert "categorySource" not in rendered[0]
    assert "categorySetBy" not in rendered[0]
