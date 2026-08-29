"""
Unit tests for scripts/api/db.py: опознание участников, приглашения, сессии.

Система не хранит ни email, ни имени из Google, ни аватара: участника
представляет HMAC его Google `sub` и выбранный им самим псевдоним, а доступ
выдаётся одноразовым приглашением. Обоснование — docs/anonymity-model.md.

Each test gets its own throwaway DB file so state never leaks between tests.
"""

import os

import pytest

pytest.importorskip("fastapi")  # keeps this consistent with the rest of the suite

import config  # noqa: E402
import db  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db_path = os.path.join(tmp_path, "redpen.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "IDENTITY_PEPPER", "unit-test-pepper")
    monkeypatch.setattr(config, "BOOTSTRAP_INVITE_CODE", "")
    db.init_db()
    yield
    db._conn.close()
    db._conn = None


# --- хеш субъекта -------------------------------------------------------


def test_subject_hash_is_stable_and_peppered(monkeypatch):
    first = db.hash_subject("google-sub-1")
    assert db.hash_subject("google-sub-1") == first
    assert db.hash_subject("google-sub-2") != first
    # Хеш зависит от перца: та же учётка на сервере с другим перцем — другой
    # человек. Именно поэтому бэкап без перца никого не выдаёт.
    monkeypatch.setattr(config, "IDENTITY_PEPPER", "another-pepper")
    assert db.hash_subject("google-sub-1") != first


def test_missing_pepper_is_an_error_not_a_fallback(monkeypatch):
    # Без перца хеш выродился бы в обычный sha256 от `sub`, то есть считался бы
    # кем угодно, у кого оказался чужой `sub`. Это отказ, а не режим по умолчанию.
    monkeypatch.setattr(config, "IDENTITY_PEPPER", "")
    with pytest.raises(db.IdentityError):
        db.hash_subject("google-sub-1")


# --- вход и приглашения -------------------------------------------------


def test_unknown_subject_without_invite_is_refused():
    assert db.login_with_google_sub("stranger") is None


def test_invite_admits_once_and_only_once():
    code = db.create_invite(role="editor")[0]
    user = db.login_with_google_sub("newcomer", invite_code=code)
    assert user is not None
    assert user["role"] == "editor"
    assert user["kind"] == "human"

    # Тот же код второму человеку уже не подходит.
    assert db.login_with_google_sub("second", invite_code=code) is None


def test_known_subject_logs_in_without_an_invite():
    code = db.create_invite(role="editor")[0]
    first = db.login_with_google_sub("regular", invite_code=code)
    again = db.login_with_google_sub("regular")
    assert again["id"] == first["id"]


def test_expired_invite_is_refused():
    code = db.create_invite(role="editor", expires_at="2000-01-01T00:00:00")[0]
    assert db.login_with_google_sub("latecomer", invite_code=code) is None


def test_invite_code_is_not_stored():
    code = db.create_invite(role="editor")[0]
    stored = db.list_invites()[0]
    assert "code" not in stored
    assert stored["codeHash"] != code
    assert stored["codeHash"] == db.hash_invite_code(code)


def test_revoke_only_works_on_unused_invites():
    code = db.create_invite(role="editor")[0]
    code_hash = db.hash_invite_code(code)
    assert db.revoke_invite(code_hash) is True
    assert db.list_invites() == []

    used = db.create_invite(role="editor")[0]
    db.login_with_google_sub("someone", invite_code=used)
    assert db.revoke_invite(db.hash_invite_code(used)) is False


def test_invite_records_who_used_it():
    code = db.create_invite(role="reviewer")[0]
    user = db.login_with_google_sub("reviewer-1", invite_code=code)
    entry = db.list_invites()[0]
    assert entry["usedBy"] == user["id"]
    assert entry["usedAt"] is not None


def test_unknown_role_is_refused():
    with pytest.raises(ValueError):
        db.create_invite(role="root")


# --- личность участника -------------------------------------------------


def test_user_record_carries_no_google_identity():
    code = db.create_invite()[0]
    user = db.login_with_google_sub("quiet-one", invite_code=code)
    assert set(user) == {"id", "kind", "displayName", "role", "createdAt", "lastLoginAt"}


def test_nothing_resembling_an_email_reaches_the_database():
    # Сквозная проверка инварианта: после входа в БД нет ни одной строки с '@'.
    code = db.create_invite()[0]
    db.login_with_google_sub("someone@somewhere", invite_code=code)
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM users").fetchone()
    assert not any("@" in str(value) for value in tuple(row) if value is not None)


def test_display_name_is_chosen_by_the_participant():
    code = db.create_invite()[0]
    user = db.login_with_google_sub("pseudonymous", invite_code=code)
    assert user["displayName"] is None
    updated = db.set_display_name(user["id"], "  Корректор  ")
    assert updated["displayName"] == "Корректор"


def test_retire_unlinks_the_account_but_keeps_history_coherent():
    code = db.create_invite(role="editor")[0]
    user = db.login_with_google_sub("leaving", invite_code=code)
    db.upsert_remark_db("doc1", "006", "a1", "major", "текст",
                            action="create", author_id=user["id"])
    session_id = db.create_session(user["id"])

    retired = db.retire_user(user["id"])
    assert retired["displayName"] == f"Участник №{user['id']}"
    assert retired["role"] == "viewer"
    assert db.get_session(session_id) is None
    # Аккаунт отвязан: вход по прежнему `sub` больше не узнаёт этого участника.
    assert db.login_with_google_sub("leaving") is None
    # Но ревизия осталась на месте и по-прежнему указывает на того же актора.
    assert db.list_history(doc_id="doc1", remark_id="a1")[0]["authorId"] == user["id"]


# --- акторы-агенты ------------------------------------------------------


def test_agent_actor_is_stable_and_marked_as_an_agent():
    agent = db.get_or_create_agent_actor("annotator-v3")
    assert agent["kind"] == "agent"
    assert agent["displayName"] == "annotator-v3"
    assert db.get_or_create_agent_actor("annotator-v3")["id"] == agent["id"]


# --- загрузочное приглашение -------------------------------------------


def test_bootstrap_invite_grants_the_first_admin(monkeypatch):
    monkeypatch.setattr(config, "BOOTSTRAP_INVITE_CODE", "first-admin-code")
    db.ensure_bootstrap_invite()
    user = db.login_with_google_sub("founder", invite_code="first-admin-code")
    assert user["role"] == "admin"


def test_bootstrap_invite_does_nothing_once_an_admin_exists(monkeypatch):
    monkeypatch.setattr(config, "BOOTSTRAP_INVITE_CODE", "first-admin-code")
    db.ensure_bootstrap_invite()
    db.login_with_google_sub("founder", invite_code="first-admin-code")

    monkeypatch.setattr(config, "BOOTSTRAP_INVITE_CODE", "second-code")
    db.ensure_bootstrap_invite()
    assert db.login_with_google_sub("impostor", invite_code="second-code") is None


# --- сессии -------------------------------------------------------------


def _some_user():
    return db.login_with_google_sub("session-user", invite_code=db.create_invite()[0])


def test_session_lifecycle():
    user = _some_user()
    session_id = db.create_session(user["id"])

    result = db.get_session(session_id)
    assert result is not None
    session, session_user = result
    assert session_user["id"] == user["id"]
    assert session["csrf"] is None

    db.set_session_csrf(session_id, "csrf-abc")
    session, _ = db.get_session(session_id)
    assert session["csrf"] == "csrf-abc"

    db.delete_session(session_id)
    assert db.get_session(session_id) is None


def test_expired_session_is_evicted():
    from datetime import datetime, timedelta

    user = _some_user()
    session_id = db.create_session(user["id"])
    conn = db.get_connection()
    past = (datetime.utcnow() - timedelta(seconds=1)).isoformat()
    conn.execute("UPDATE sessions SET expires_at = ? WHERE id = ?", (past, session_id))
    conn.commit()

    assert db.get_session(session_id) is None
    # Eviction should have deleted the row outright.
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    assert row is None
