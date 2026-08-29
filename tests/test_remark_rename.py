"""
Переименование сущности «аннотация» → «замечание» (remark), 2026-08-29.

Здесь проверяется не новая функциональность, а обратная совместимость: база,
заведённая до переименования, должна переехать без потерь, а данные и клиенты,
написанные до него, — по-прежнему читаться. Всё, что помечено «легаси», имеет
срок жизни: см. фазу 6 в плане переименования.
"""

import json
import os
import sqlite3

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402
import page_html  # noqa: E402
import publisher  # noqa: E402
import remark_converter  # noqa: E402

#: Схема ровно в том виде, в каком она существовала до переименования.
LEGACY_SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL DEFAULT 'human');
CREATE TABLE annotations (
  rowid_pk INTEGER PRIMARY KEY AUTOINCREMENT, ann_id TEXT NOT NULL,
  doc_id TEXT NOT NULL, page_num TEXT NOT NULL, ann_type TEXT NOT NULL,
  text TEXT NOT NULL, coord_x INTEGER, coord_y INTEGER,
  status TEXT NOT NULL DEFAULT 'published',
  category TEXT NOT NULL DEFAULT 'other',
  category_source TEXT NOT NULL DEFAULT 'default',
  category_set_by INTEGER REFERENCES users(id),
  author_id INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(doc_id, page_num, ann_id));
CREATE INDEX idx_annotations_page ON annotations(doc_id, page_num);
CREATE INDEX idx_annotations_category ON annotations(category);
CREATE INDEX idx_annotations_category_source ON annotations(doc_id, category_source);
CREATE TABLE annotation_tags (
  annotation_pk INTEGER NOT NULL REFERENCES annotations(rowid_pk) ON DELETE CASCADE,
  tag TEXT NOT NULL, UNIQUE(annotation_pk, tag));
CREATE INDEX idx_annotation_tags_tag ON annotation_tags(tag);
CREATE TABLE annotation_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT NOT NULL,
  page_num TEXT NOT NULL, ann_id TEXT NOT NULL, action TEXT NOT NULL,
  snapshot TEXT NOT NULL, author_id INTEGER, created_at TEXT NOT NULL,
  rev_no INTEGER, parent_rev_id INTEGER, agent_run_id INTEGER, summary TEXT);
CREATE INDEX idx_history_ann ON annotation_history(doc_id, page_num, ann_id, id);
CREATE TABLE annotation_reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT NOT NULL,
  page_num TEXT NOT NULL, ann_id TEXT NOT NULL,
  reviewer_id INTEGER NOT NULL REFERENCES users(id), verdict TEXT NOT NULL,
  note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(doc_id, page_num, ann_id, reviewer_id));
CREATE INDEX idx_reviews_ann ON annotation_reviews(doc_id, page_num, ann_id);
"""

LEGACY_ROWS = [("ann-1", "main"), ("ann-2", "comment"),
               ("ann-3", "comment"), ("ann-4", "general")]


def _make_legacy_db(path, with_review=False):
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    conn.execute("INSERT INTO users (kind) VALUES ('human')")
    for i, (ann_id, ann_type) in enumerate(LEGACY_ROWS, start=1):
        conn.execute(
            "INSERT INTO annotations (ann_id, doc_id, page_num, ann_type, text,"
            " created_at, updated_at) VALUES (?,?,?,?,?,'t','t')",
            (ann_id, "doc", "007", ann_type, "текст"))
        conn.execute("INSERT INTO annotation_tags (annotation_pk, tag) VALUES (?,?)",
                     (i, "tag%d" % i))
    conn.execute(
        "INSERT INTO annotation_history (doc_id, page_num, ann_id, action, snapshot,"
        " created_at) VALUES ('doc','007','ann-1','update',?,'t')",
        (json.dumps({"annId": "ann-1", "annType": "main", "text": "x"}),))
    if with_review:
        conn.execute(
            "INSERT INTO annotation_reviews (doc_id, page_num, ann_id, reviewer_id,"
            " verdict, created_at, updated_at) VALUES ('doc','007','ann-1',1,'ok','t','t')")
    conn.commit()
    conn.close()


@pytest.fixture()
def migrated(tmp_path, monkeypatch):
    """База со старой схемой, поднятая текущим init_db()."""
    path = os.path.join(tmp_path, "redpen.db")
    _make_legacy_db(path)
    monkeypatch.setattr(config, "DB_PATH", path)
    db.init_db()
    yield db.get_connection()
    db._conn.close()
    db._conn = None


# --------------------------------------------------------------------------
# Миграция схемы
# --------------------------------------------------------------------------

def test_legacy_tables_and_columns_are_renamed(migrated):
    leftovers = [r[0] for r in migrated.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE '%annotation%'")]
    assert leftovers == []
    columns = {r[1] for r in migrated.execute("PRAGMA table_info(remarks)")}
    assert "remark_id" in columns and "kind" in columns
    assert "ann_id" not in columns and "ann_type" not in columns


def test_kind_values_are_migrated_and_general_is_left_alone(migrated):
    counts = dict(migrated.execute(
        "SELECT kind, COUNT(*) FROM remarks GROUP BY kind").fetchall())
    assert counts == {"major": 1, "minor": 2, "general": 1}


def test_nothing_is_lost(migrated):
    assert migrated.execute("SELECT COUNT(*) FROM remarks").fetchone()[0] == len(LEGACY_ROWS)
    assert migrated.execute("SELECT COUNT(*) FROM remark_tags").fetchone()[0] == len(LEGACY_ROWS)
    assert migrated.execute("SELECT COUNT(*) FROM remark_history").fetchone()[0] == 1


def test_constraints_survive_the_rename(migrated):
    """FK и UNIQUE должны указывать на новые имена, а не на исчезнувшие старые."""
    assert migrated.execute("PRAGMA foreign_key_check").fetchall() == []
    assert migrated.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    fk = migrated.execute("PRAGMA foreign_key_list(remark_tags)").fetchall()
    assert [(r["table"], r["from"], r["to"]) for r in fk] == [("remarks", "remark_pk", "rowid_pk")]
    unique = [r[2] for r in migrated.execute("PRAGMA index_info('sqlite_autoindex_remarks_1')")]
    assert unique == ["doc_id", "page_num", "remark_id"]


def test_stale_indexes_are_dropped(migrated):
    names = {r[0] for r in migrated.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert not {n for n in names if "annotation" in n or n.startswith("idx_history_")}
    assert {"idx_remarks_page", "idx_remark_tags_tag",
            "idx_remark_history_target"} <= names


def test_migration_is_idempotent(migrated, tmp_path):
    """Второй запуск на уже мигрированной базе не должен ни падать, ни менять её."""
    before = migrated.execute(
        "SELECT kind, COUNT(*) FROM remarks GROUP BY kind ORDER BY kind").fetchall()
    db._conn.close()
    db._conn = None
    db.init_db()
    after = db.get_connection().execute(
        "SELECT kind, COUNT(*) FROM remarks GROUP BY kind ORDER BY kind").fetchall()
    assert [tuple(r) for r in after] == [tuple(r) for r in before]


def test_empty_review_table_is_dropped_but_a_filled_one_is_kept(tmp_path, monkeypatch):
    """Ревью-подсистему удалили как неподключённую. Пустую таблицу сносим,
    непустую — переименовываем: данные, которых мы не ждали, не выбрасывают."""
    path = os.path.join(tmp_path, "filled.db")
    _make_legacy_db(path, with_review=True)
    monkeypatch.setattr(config, "DB_PATH", path)
    db.init_db()
    tables = {r[0] for r in db.get_connection().execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "remark_reviews" in tables and "annotation_reviews" not in tables
    db._conn.close()
    db._conn = None


# --------------------------------------------------------------------------
# Данные, записанные до переименования
# --------------------------------------------------------------------------

def test_history_snapshots_are_normalized_on_read(migrated):
    """Журнал ревизий — аудит, его не переписывают. Старые снапшоты приводятся
    к текущим именам на чтении."""
    record = db.list_history(doc_id="doc", page_num="007")[0]
    assert record["snapshot"]["remarkId"] == "ann-1"
    assert record["snapshot"]["kind"] == "major"


def test_upsert_accepts_legacy_kind_values(migrated):
    """Импорт старых выгрузок идёт в базу напрямую — старое значение не должно
    завестись в таблице заново."""
    row = db.upsert_remark_db("doc", "007", "ann-9", "comment", "текст")
    assert row["kind"] == "minor"
    assert db.upsert_remark_db("doc", "007", "ann-10", "main", "т")["kind"] == "major"


def test_converter_reads_the_legacy_meta_key():
    """Черновики агентов, написанные до переименования, несут `type: main`."""
    parsed = remark_converter.parse_markdown_remark(
        "~~~meta\ntype: main\nid: a-1\ntarget: [1, 2]\n~~~\n\nтекст\n")
    assert parsed[0]["kind"] == "major"
    assert remark_converter.parse_markdown_remark(
        "~~~meta\nkind: minor\nid: a-2\ntarget: [1, 2]\n~~~\n\nт\n")[0]["kind"] == "minor"


def test_converter_writes_only_the_current_names(tmp_path):
    path = os.path.join(tmp_path, "page_007.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump([{"id": "a-1", "text": "т", "kind": "major", "coords": [1, 2]}], f)
    md = remark_converter.convert_json_to_md(path)
    assert "kind: major" in md and "type:" not in md


def test_page_blob_carries_both_key_names():
    """Страницы перерисовываются по одной: в момент выкладки часть их читает
    ещё не обновлённый JS."""
    blob = page_html._page_data_blob(
        [{"id": "a-1", "text": "т", "kind": "major", "coords": [1, 2]}])
    payload = json.loads(blob.split(">", 1)[1].rsplit("<", 1)[0])
    assert payload[0]["kind"] == "major"
    assert payload[0]["annType"] == "main"


def test_page_blob_reads_the_legacy_key():
    blob = page_html._page_data_blob([{"id": "a-1", "text": "т", "annType": "comment"}])
    payload = json.loads(blob.split(">", 1)[1].rsplit("<", 1)[0])
    assert payload[0]["kind"] == "minor"


def test_legacy_kind_round_trips_through_the_publisher():
    assert publisher.legacy_kind("major") == "main"
    assert publisher.legacy_kind("minor") == "comment"
    # Неизвестное значение проходит как есть: выдумывать за данные нечего.
    assert publisher.legacy_kind("general") == "general"
