"""Разделение «псевдоним ↔ сессия» на старой базе (2026-09-01).

До этой правки строка `survey_respondents` была и псевдонимом, и заходом: под
одним именем заводились разные респонденты, и вернувшемуся человеку заново
раздавали то, что он уже оценил. Здесь проверяется, что база, заведённая до
разделения, переезжает без потерь: одинаковые псевдонимы схлопываются в одного
респондента, каждый старый заход становится сессией, ответы остаются на месте.
"""

import os
import sqlite3

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402

#: Таблицы опроса ровно в том виде, в каком они существовали до разделения.
LEGACY_SURVEY_SCHEMA = """
CREATE TABLE survey_respondents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pseudonym TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  last_seen_at TEXT);
CREATE TABLE survey_ratings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  respondent_id INTEGER NOT NULL REFERENCES survey_respondents(id),
  doc_id TEXT NOT NULL, page_num TEXT NOT NULL, remark_id TEXT NOT NULL,
  scale TEXT NOT NULL, value INTEGER NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(respondent_id, doc_id, page_num, remark_id, scale));
CREATE INDEX idx_survey_ratings_target
  ON survey_ratings(doc_id, page_num, remark_id);
"""


@pytest.fixture
def legacy_db(tmp_path, monkeypatch):
    """База со старой формой таблиц опроса и данными в ней."""
    path = os.path.join(tmp_path, "redpen.db")
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SURVEY_SCHEMA)
    conn.executemany(
        "INSERT INTO survey_respondents (id, pseudonym, token_hash, created_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [(1, "Пётр", "h1", "2026-08-31T10:00:00", "2026-08-31T10:30:00"),
         (2, "Иван", "h2", "2026-08-31T11:00:00", "2026-08-31T11:05:00"),
         (3, "Пётр", "h3", "2026-09-01T09:00:00", "2026-09-01T09:20:00")],
    )
    conn.executemany(
        "INSERT INTO survey_ratings (respondent_id, doc_id, page_num, remark_id, "
        "scale, value, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(1, "d", "042", "r-1", "interest", 3, "2026-08-31T10:10:00", "2026-08-31T10:10:00"),
         (2, "d", "042", "r-1", "interest", 5, "2026-08-31T11:01:00", "2026-08-31T11:01:00"),
         # Тот же человек, второй заход, то же замечание: до разделения это были
         # два разных респондента, и в старом ключе коллизии не было.
         (3, "d", "042", "r-1", "interest", 1, "2026-09-01T09:10:00", "2026-09-01T09:10:00")],
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setattr(config, "BOOTSTRAP_INVITE_CODE", "")
    monkeypatch.setattr(config, "DB_PATH", path)
    if db._conn is not None:
        db._conn.close()
    db._conn = None
    yield path
    if db._conn is not None:
        db._conn.close()
    db._conn = None


def test_pseudonyms_collapse_and_visits_become_sessions(legacy_db):
    db.init_db()
    conn = db.get_connection()

    names = [row[0] for row in conn.execute(
        "SELECT pseudonym FROM survey_respondents ORDER BY pseudonym")]
    assert names == ["Иван", "Пётр"]

    petr = conn.execute(
        "SELECT id, created_at, last_seen_at FROM survey_respondents "
        "WHERE pseudonym = 'Пётр'").fetchone()
    # Границы обоих заходов: начало самое раннее, активность самая поздняя.
    assert petr["created_at"] == "2026-08-31T10:00:00"
    assert petr["last_seen_at"] == "2026-09-01T09:20:00"

    sessions = conn.execute(
        "SELECT token_hash FROM survey_sessions WHERE respondent_id = ? ORDER BY id",
        (petr["id"],)).fetchall()
    assert [row["token_hash"] for row in sessions] == ["h1", "h3"]
    assert conn.execute("SELECT COUNT(*) FROM survey_sessions").fetchone()[0] == 3


def test_answers_survive_with_both_ids(legacy_db):
    db.init_db()
    conn = db.get_connection()

    rows = conn.execute(
        "SELECT r.value, s.token_hash, p.pseudonym FROM survey_answers r "
        "JOIN survey_sessions s ON s.id = r.session_id "
        "JOIN survey_respondents p ON p.id = r.respondent_id "
        "ORDER BY r.id").fetchall()
    assert [(row["value"], row["token_hash"], row["pseudonym"]) for row in rows] == [
        (3, "h1", "Пётр"), (5, "h2", "Иван"), (1, "h3", "Пётр")]

    # Голос сводится к псевдониму: два ответа Петра — один голос, последний.
    item = db.survey_results()["items"][0]
    assert item["raters"] == 2
    assert item["interest"] == {"count": 2, "average": 3.0}


def test_migration_is_idempotent(legacy_db):
    db.init_db()
    db._conn.close()
    db._conn = None
    db.init_db()   # второй запуск API на уже мигрированной базе
    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM survey_respondents").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM survey_sessions").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM survey_answers").fetchone()[0] == 3
    leftovers = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE '\\_survey%' ESCAPE '\\'")}
    assert leftovers == set()
