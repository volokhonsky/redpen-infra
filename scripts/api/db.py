"""
SQLite-backed storage for users, sessions, and the editor allowlist (stage 1).

No ORM: a single module-level connection (check_same_thread=False, guarded by
a lock) is enough since the API runs as a single uvicorn worker.
"""

import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import config

SESSION_TTL_SECONDS = 30 * 86400  # 30 days, matches the auth cookie max_age

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def get_connection() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        raise RuntimeError("db not initialized; call init_db() first")
    return _conn


def init_db() -> None:
    """Create the DB file/directory and schema if missing. Idempotent."""
    global _conn
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    with _lock:
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              google_sub TEXT UNIQUE,
              email TEXT UNIQUE,
              name TEXT,
              picture_url TEXT,
              role TEXT NOT NULL DEFAULT 'viewer',
              created_at TEXT NOT NULL,
              last_login_at TEXT
            );
            CREATE TABLE IF NOT EXISTS sessions (
              id TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL REFERENCES users(id),
              csrf TEXT,
              created_at TEXT NOT NULL,
              expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS editor_allowlist (
              email TEXT PRIMARY KEY,
              role TEXT NOT NULL DEFAULT 'editor',
              added_by TEXT,
              added_at TEXT NOT NULL
            );
            """
        )
        _conn.commit()


def _user_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "googleSub": row["google_sub"],
        "email": row["email"],
        "name": row["name"],
        "pictureUrl": row["picture_url"],
        "role": row["role"],
        "createdAt": row["created_at"],
        "lastLoginAt": row["last_login_at"],
    }


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    with _lock:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _user_row_to_dict(row) if row else None


def resolve_role(email: Optional[str]) -> str:
    """admin (ADMIN_EMAILS) > editor_allowlist role > viewer."""
    if not email:
        return "viewer"
    if email in config.ADMIN_EMAILS:
        return "admin"
    conn = get_connection()
    with _lock:
        row = conn.execute(
            "SELECT role FROM editor_allowlist WHERE email = ?", (email,)
        ).fetchone()
    return row["role"] if row else "viewer"


def get_or_create_user_google(sub: str, email: str, name: str, picture: Optional[str]) -> Dict[str, Any]:
    conn = get_connection()
    role = resolve_role(email)
    now = _now_iso()
    with _lock:
        row = conn.execute("SELECT * FROM users WHERE google_sub = ?", (sub,)).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO users (google_sub, email, name, picture_url, role, created_at, last_login_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (sub, email, name, picture, role, now, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE google_sub = ?", (sub,)).fetchone()
        else:
            conn.execute(
                """
                UPDATE users SET email = ?, name = ?, picture_url = ?, role = ?, last_login_at = ?
                WHERE id = ?
                """,
                (email, name, picture, role, now, row["id"]),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
    return _user_row_to_dict(row)


def get_or_create_user_token(username: str) -> Dict[str, Any]:
    """Dev-fallback token login: fixed 'editor' role, keyed by a synthetic email."""
    conn = get_connection()
    synthetic_email = f"token:{username}"
    now = _now_iso()
    with _lock:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (synthetic_email,)).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO users (google_sub, email, name, picture_url, role, created_at, last_login_at)
                VALUES (NULL, ?, ?, NULL, 'editor', ?, ?)
                """,
                (synthetic_email, username, now, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE email = ?", (synthetic_email,)).fetchone()
        else:
            conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, row["id"]))
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
    return _user_row_to_dict(row)


def create_session(user_id: int) -> str:
    conn = get_connection()
    session_id = secrets.token_hex(32)
    now = datetime.utcnow()
    expires_at = now + timedelta(seconds=SESSION_TTL_SECONDS)
    with _lock:
        conn.execute(
            "INSERT INTO sessions (id, user_id, csrf, created_at, expires_at) VALUES (?, ?, NULL, ?, ?)",
            (session_id, user_id, now.isoformat(), expires_at.isoformat()),
        )
        conn.commit()
    return session_id


def get_session(session_id: str) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Return (session, user) if the session exists and hasn't expired, else None.

    Expired sessions are deleted as a side effect of the lookup.
    """
    if not session_id:
        return None
    conn = get_connection()
    with _lock:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        try:
            expired = datetime.fromisoformat(row["expires_at"]) < datetime.utcnow()
        except ValueError:
            expired = True
        if expired:
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()
            return None
        user_row = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    if user_row is None:
        delete_session(session_id)
        return None
    session = {
        "id": row["id"],
        "userId": row["user_id"],
        "csrf": row["csrf"],
        "createdAt": row["created_at"],
        "expiresAt": row["expires_at"],
    }
    return session, _user_row_to_dict(user_row)


def delete_session(session_id: str) -> None:
    conn = get_connection()
    with _lock:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()


def set_session_csrf(session_id: str, token: str) -> None:
    conn = get_connection()
    with _lock:
        conn.execute("UPDATE sessions SET csrf = ? WHERE id = ?", (token, session_id))
        conn.commit()


def list_allowlist() -> List[Dict[str, Any]]:
    conn = get_connection()
    with _lock:
        rows = conn.execute(
            "SELECT * FROM editor_allowlist ORDER BY added_at DESC"
        ).fetchall()
    return [
        {
            "email": row["email"],
            "role": row["role"],
            "addedBy": row["added_by"],
            "addedAt": row["added_at"],
        }
        for row in rows
    ]


def upsert_allowlist(email: str, role: str, added_by: Optional[str]) -> None:
    conn = get_connection()
    now = _now_iso()
    with _lock:
        conn.execute(
            """
            INSERT INTO editor_allowlist (email, role, added_by, added_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET role = excluded.role, added_by = excluded.added_by
            """,
            (email, role, added_by, now),
        )
        conn.commit()


def delete_allowlist(email: str) -> bool:
    conn = get_connection()
    with _lock:
        cur = conn.execute("DELETE FROM editor_allowlist WHERE email = ?", (email,))
        conn.commit()
        return cur.rowcount > 0
