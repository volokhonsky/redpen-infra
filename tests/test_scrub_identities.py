"""Миграция личностей: хеши вместо email, пустая allowlist, целая история.

До миграции `users` хранила email, имя и аватар открытым текстом, а ежедневный
бэкап БД был готовым именным списком авторов. Тесты проверяют, что после
прогона от личностей не остаётся ничего, а работа проекта не рвётся.
"""

import os
import sqlite3

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402
import scrub_identities  # noqa: E402


LEGACY_USERS = [
    (1, "google-sub-alice", "alice@example.com", "Alice", "http://pic/a", "admin"),
    (2, "google-sub-bob", "bob@example.com", "Bob", None, "editor"),
    (3, None, "token:annotator-v3", "annotator-v3", None, "editor"),
]


@pytest.fixture
def legacy_db(tmp_path, monkeypatch):
    """База в том виде, в каком она лежит на проде до миграции."""
    if db._conn is not None:
        db._conn.close()
        db._conn = None
    path = os.path.join(tmp_path, "legacy.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          google_sub TEXT UNIQUE, email TEXT UNIQUE, name TEXT, picture_url TEXT,
          role TEXT NOT NULL DEFAULT 'viewer',
          created_at TEXT NOT NULL, last_login_at TEXT
        );
        CREATE TABLE editor_allowlist (
          email TEXT PRIMARY KEY, role TEXT NOT NULL DEFAULT 'editor',
          added_by TEXT, added_at TEXT NOT NULL
        );
        """
    )
    conn.executemany(
        "INSERT INTO users (id, google_sub, email, name, picture_url, role, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, '2026-08-01T00:00:00')", LEGACY_USERS)
    conn.execute("INSERT INTO editor_allowlist (email, role, added_at)"
                 " VALUES ('bob@example.com', 'editor', '2026-08-01T00:00:00')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "IDENTITY_PEPPER", "migration-test-pepper")
    monkeypatch.setattr(config, "BOOTSTRAP_INVITE_CODE", "")
    yield path
    if db._conn is not None:
        db._conn.close()
        db._conn = None


def _run(apply=False):
    argv = ["scrub_identities.py"] + (["--apply"] if apply else [])
    import sys
    old = sys.argv
    sys.argv = argv
    try:
        return scrub_identities.main()
    finally:
        sys.argv = old


def test_dry_run_changes_nothing(legacy_db):
    assert _run() == 0
    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) AS n FROM users WHERE email IS NOT NULL"
                        ).fetchone()["n"] == 3


def test_apply_leaves_no_personal_data(legacy_db):
    assert _run(apply=True) == 0
    conn = db.get_connection()
    rows = conn.execute("SELECT * FROM users").fetchall()
    for row in rows:
        for column in ("google_sub", "email", "name", "picture_url"):
            assert row[column] is None, column
    # И ни в одной строке не остаётся ничего похожего на email.
    assert not any("@" in str(v) for row in rows for v in tuple(row) if v is not None)


def test_apply_preserves_roles_and_lets_people_back_in(legacy_db):
    _run(apply=True)
    # Роль сохранилась: она давно живёт в users.role, а не в allowlist.
    assert db.get_user_by_id(1)["role"] == "admin"
    # И прежний Google-аккаунт узнаётся без нового приглашения.
    assert db.login_with_google_sub("google-sub-alice")["id"] == 1
    assert db.login_with_google_sub("google-sub-bob")["id"] == 2


def test_apply_gives_everyone_a_placeholder_pseudonym(legacy_db):
    _run(apply=True)
    assert db.get_user_by_id(1)["displayName"] == "Участник №1"


def test_allowlist_is_emptied(legacy_db):
    _run(apply=True)
    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) AS n FROM editor_allowlist").fetchone()["n"] == 0


def test_apply_is_idempotent(legacy_db):
    _run(apply=True)
    hash_before = db.get_connection().execute(
        "SELECT sub_hash FROM users WHERE id = 1").fetchone()["sub_hash"]
    assert _run(apply=True) == 0
    hash_after = db.get_connection().execute(
        "SELECT sub_hash FROM users WHERE id = 1").fetchone()["sub_hash"]
    assert hash_after == hash_before


def test_refuses_to_run_without_a_pepper(legacy_db, monkeypatch):
    monkeypatch.setattr(config, "IDENTITY_PEPPER", "")
    assert _run(apply=True) == 2
