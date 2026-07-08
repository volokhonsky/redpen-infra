"""
Unit tests for scripts/api/db.py (SQLite-backed users/sessions/allowlist).

Each test gets its own throwaway DB file so state never leaks between tests.
"""

import os
import tempfile

import pytest

pytest.importorskip("fastapi")  # keeps this consistent with the rest of the suite

import config  # noqa: E402
import db  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db_path = os.path.join(tmp_path, "redpen.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "ADMIN_EMAILS", [])
    db.init_db()
    yield
    db._conn.close()
    db._conn = None


def test_get_or_create_user_google_creates_then_reuses():
    user = db.get_or_create_user_google("sub-1", "alice@example.com", "Alice", "http://pic")
    assert user["email"] == "alice@example.com"
    assert user["role"] == "viewer"

    again = db.get_or_create_user_google("sub-1", "alice@example.com", "Alice", "http://pic")
    assert again["id"] == user["id"]


def test_resolve_role_admin_email(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_EMAILS", ["admin@example.com"])
    assert db.resolve_role("admin@example.com") == "admin"
    assert db.resolve_role("nobody@example.com") == "viewer"


def test_resolve_role_from_allowlist():
    db.upsert_allowlist("editor@example.com", "editor", "admin@example.com")
    assert db.resolve_role("editor@example.com") == "editor"
    assert db.resolve_role("stranger@example.com") == "viewer"


def test_google_login_recomputes_role_after_allowlist_change():
    user = db.get_or_create_user_google("sub-2", "bob@example.com", "Bob", None)
    assert user["role"] == "viewer"

    db.upsert_allowlist("bob@example.com", "editor", "admin@example.com")
    again = db.get_or_create_user_google("sub-2", "bob@example.com", "Bob", None)
    assert again["role"] == "editor"


def test_get_or_create_user_token_is_editor_and_stable():
    user = db.get_or_create_user_token("john_doe")
    assert user["role"] == "editor"
    assert user["name"] == "john_doe"

    again = db.get_or_create_user_token("john_doe")
    assert again["id"] == user["id"]


def test_session_lifecycle():
    user = db.get_or_create_user_token("carol")
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

    user = db.get_or_create_user_token("dave")
    session_id = db.create_session(user["id"])
    conn = db.get_connection()
    past = (datetime.utcnow() - timedelta(seconds=1)).isoformat()
    conn.execute("UPDATE sessions SET expires_at = ? WHERE id = ?", (past, session_id))
    conn.commit()

    assert db.get_session(session_id) is None
    # Eviction should have deleted the row outright.
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    assert row is None


def test_allowlist_crud():
    assert db.list_allowlist() == []

    db.upsert_allowlist("x@example.com", "editor", "admin@example.com")
    entries = db.list_allowlist()
    assert len(entries) == 1
    assert entries[0]["email"] == "x@example.com"
    assert entries[0]["role"] == "editor"

    db.upsert_allowlist("x@example.com", "admin", "admin@example.com")
    entries = db.list_allowlist()
    assert len(entries) == 1
    assert entries[0]["role"] == "admin"

    assert db.delete_allowlist("x@example.com") is True
    assert db.list_allowlist() == []
    assert db.delete_allowlist("x@example.com") is False
