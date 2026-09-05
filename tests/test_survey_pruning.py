"""Уборка заходов опроса, не оставивших ни одного ответа.

`POST /api/survey/session` — единственный маршрут, где строки в канонической
базе заводит посторонний человек, и до 2026-09-05 ни срока, ни уборки у этих
строк не было. Убирается только пустое: сказавший хоть слово остаётся.
"""
import os
from datetime import datetime, timedelta

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", os.path.join(tmp_path, "redpen.db"))
    if db._conn is not None:
        db._conn.close()
    db._conn = None
    db.init_db()
    yield
    if db._conn is not None:
        db._conn.close()
    db._conn = None


def _age_session(session_id: int, days: int) -> None:
    stamp = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with db._lock:
        db._conn.execute("UPDATE survey_sessions SET created_at = ? WHERE id = ?",
                         (stamp, session_id))
        db._conn.commit()


def _pool_and_answer(session):
    db.upsert_remark_db("prunedoc", "007", "r-1", "minor", "текст")
    db.pool_add("prunedoc", "007", "r-1")
    db.set_survey_answer(session["respondentId"], session["sessionId"],
                         "prunedoc", "007", "r-1", "importance", value=3)


def test_empty_old_sessions_are_removed():
    session = db.start_survey_session("Молчун")
    _age_session(session["sessionId"], days=90)

    report = db.prune_empty_survey_sessions(30, apply=True)
    assert report == {"sessions": 1, "respondents": 1}
    assert db.list_respondents()["total"] == 0


def test_recent_sessions_are_left_alone():
    db.start_survey_session("Только что пришёл")
    report = db.prune_empty_survey_sessions(30, apply=True)
    assert report == {"sessions": 0, "respondents": 0}
    assert db.list_respondents()["total"] == 1


def test_a_pseudonym_that_answered_is_never_removed():
    session = db.start_survey_session("Ответивший")
    _pool_and_answer(session)
    _age_session(session["sessionId"], days=900)

    report = db.prune_empty_survey_sessions(30, apply=True)
    assert report == {"sessions": 0, "respondents": 0}
    assert db.list_respondents()["total"] == 1


def test_an_empty_visit_of_an_answering_pseudonym_goes_but_the_name_stays():
    """У человека может быть и пустой заход, и содержательный."""
    answered = db.start_survey_session("Двоякий")
    _pool_and_answer(answered)
    empty = db.start_survey_session("Двоякий")
    _age_session(empty["sessionId"], days=90)

    report = db.prune_empty_survey_sessions(30, apply=True)
    assert report == {"sessions": 1, "respondents": 0}
    # Псевдоним на месте, потому что его ответы на месте.
    assert db.list_respondents()["total"] == 1


def test_without_apply_nothing_is_written():
    session = db.start_survey_session("Молчун")
    _age_session(session["sessionId"], days=90)

    report = db.prune_empty_survey_sessions(30)
    assert report == {"sessions": 1, "respondents": 1}
    assert db.list_respondents()["total"] == 1


def _stored_last_seen(session_id):
    with db._lock:
        return db._conn.execute(
            "SELECT last_seen_at FROM survey_sessions WHERE id = ?",
            (session_id,)).fetchone()["last_seen_at"]


def test_reading_a_session_does_not_rewrite_the_stamp_every_time():
    """Опознание захода не должно стоить записи на диск на каждом запросе.

    Через get_session_by_token проходит каждый запрос опроса, включая раздачу
    очередной порции. До 2026-09-05 каждый такой запрос делал два UPDATE и
    commit — то есть чтение стоило записи."""
    session = db.start_survey_session("Частый")
    token = session["token"]

    db.get_session_by_token(token)
    first = _stored_last_seen(session["sessionId"])
    for _ in range(20):
        db.get_session_by_token(token)
    assert _stored_last_seen(session["sessionId"]) == first


def test_the_stamp_is_still_refreshed_once_the_window_passes(monkeypatch):
    """Отметка не должна застыть: админ по ней отличает живой заход от
    брошенного."""
    session = db.start_survey_session("Вернувшийся")
    db.get_session_by_token(session["token"])
    first = _stored_last_seen(session["sessionId"])

    monkeypatch.setattr(db, "LAST_SEEN_GRANULARITY_SEC", 0)
    db.get_session_by_token(session["token"])
    assert _stored_last_seen(session["sessionId"]) != first
