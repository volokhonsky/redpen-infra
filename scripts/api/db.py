"""
SQLite-backed storage for users, sessions, and the editor allowlist (stage 1).

No ORM: a single module-level connection (check_same_thread=False, guarded by
a lock) is enough since the API runs as a single uvicorn worker.
"""

import json
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
            CREATE TABLE IF NOT EXISTS annotations (
              rowid_pk INTEGER PRIMARY KEY AUTOINCREMENT,
              ann_id TEXT NOT NULL,
              doc_id TEXT NOT NULL,
              page_num TEXT NOT NULL,
              ann_type TEXT NOT NULL,
              text TEXT NOT NULL,
              coord_x INTEGER,
              coord_y INTEGER,
              status TEXT NOT NULL DEFAULT 'published',
              author_id INTEGER REFERENCES users(id),
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(doc_id, page_num, ann_id)
            );
            CREATE INDEX IF NOT EXISTS idx_annotations_page ON annotations(doc_id, page_num);
            CREATE TABLE IF NOT EXISTS annotation_history (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              doc_id TEXT NOT NULL,
              page_num TEXT NOT NULL,
              ann_id TEXT NOT NULL,
              action TEXT NOT NULL,
              snapshot TEXT NOT NULL,
              author_id INTEGER,
              created_at TEXT NOT NULL
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


# ===== Annotations (stage 2: SQLite is the canonical store) =====


def _annotation_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "rowidPk": row["rowid_pk"],
        "annId": row["ann_id"],
        "docId": row["doc_id"],
        "pageNum": row["page_num"],
        "annType": row["ann_type"],
        "text": row["text"],
        "coordX": row["coord_x"],
        "coordY": row["coord_y"],
        "status": row["status"],
        "authorId": row["author_id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _insert_history(
    conn: sqlite3.Connection,
    doc_id: str,
    page_num: str,
    ann_id: str,
    action: str,
    snapshot: Dict[str, Any],
    author_id: Optional[int],
) -> None:
    conn.execute(
        """
        INSERT INTO annotation_history (doc_id, page_num, ann_id, action, snapshot, author_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (doc_id, page_num, ann_id, action, json.dumps(snapshot, ensure_ascii=False), author_id, _now_iso()),
    )


def add_history(
    doc_id: str,
    page_num: str,
    ann_id: str,
    action: str,
    snapshot: Dict[str, Any],
    author_id: Optional[int] = None,
) -> None:
    conn = get_connection()
    with _lock:
        _insert_history(conn, doc_id, page_num, ann_id, action, snapshot, author_id)
        conn.commit()


def list_page_annotations(doc_id: str, page_num: str, include_deleted: bool = False) -> List[Dict[str, Any]]:
    conn = get_connection()
    with _lock:
        if include_deleted:
            rows = conn.execute(
                "SELECT * FROM annotations WHERE doc_id = ? AND page_num = ? ORDER BY rowid_pk",
                (doc_id, page_num),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM annotations WHERE doc_id = ? AND page_num = ? AND status = 'published' ORDER BY rowid_pk",
                (doc_id, page_num),
            ).fetchall()
    return [_annotation_row_to_dict(row) for row in rows]


def list_page_drafts(doc_id: str, page_num: str) -> List[Dict[str, Any]]:
    """Draft (status='draft') annotations for a page, in insertion order. These
    never reach the published bare-array page_<NNN>.json; the publisher renders
    them into a separate page_<NNN>.drafts.json the viewer loads only in the
    ?showDrafts=1 preview mode."""
    conn = get_connection()
    with _lock:
        rows = conn.execute(
            "SELECT * FROM annotations WHERE doc_id = ? AND page_num = ? AND status = 'draft' ORDER BY rowid_pk",
            (doc_id, page_num),
        ).fetchall()
    return [_annotation_row_to_dict(row) for row in rows]


def get_annotation(doc_id: str, page_num: str, ann_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    with _lock:
        row = conn.execute(
            "SELECT * FROM annotations WHERE doc_id = ? AND page_num = ? AND ann_id = ?",
            (doc_id, page_num, ann_id),
        ).fetchone()
    return _annotation_row_to_dict(row) if row else None


def upsert_annotation_db(
    doc_id: str,
    page_num: str,
    ann_id: str,
    ann_type: str,
    text: str,
    coord_x: Optional[int] = None,
    coord_y: Optional[int] = None,
    status: str = "published",
    author_id: Optional[int] = None,
    action: str = "update",
) -> Dict[str, Any]:
    """Insert or update an annotation by (doc_id, page_num, ann_id) and record
    the resulting state in annotation_history, in the same transaction."""
    conn = get_connection()
    now = _now_iso()
    with _lock:
        conn.execute(
            """
            INSERT INTO annotations
              (ann_id, doc_id, page_num, ann_type, text, coord_x, coord_y, status, author_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(doc_id, page_num, ann_id) DO UPDATE SET
              ann_type = excluded.ann_type,
              text = excluded.text,
              coord_x = excluded.coord_x,
              coord_y = excluded.coord_y,
              status = excluded.status,
              author_id = excluded.author_id,
              updated_at = excluded.updated_at
            """,
            (ann_id, doc_id, page_num, ann_type, text, coord_x, coord_y, status, author_id, now, now),
        )
        row = conn.execute(
            "SELECT * FROM annotations WHERE doc_id = ? AND page_num = ? AND ann_id = ?",
            (doc_id, page_num, ann_id),
        ).fetchone()
        ann = _annotation_row_to_dict(row)
        _insert_history(conn, doc_id, page_num, ann_id, action, ann, author_id)
        conn.commit()
    return ann


def soft_delete_annotation(doc_id: str, page_num: str, ann_id: str, author_id: Optional[int] = None) -> bool:
    """Mark a published annotation as deleted and record history. Returns False
    if the annotation doesn't exist or is already deleted."""
    conn = get_connection()
    now = _now_iso()
    with _lock:
        row = conn.execute(
            "SELECT * FROM annotations WHERE doc_id = ? AND page_num = ? AND ann_id = ?",
            (doc_id, page_num, ann_id),
        ).fetchone()
        if row is None or row["status"] == "deleted":
            return False
        conn.execute(
            "UPDATE annotations SET status = 'deleted', updated_at = ? WHERE doc_id = ? AND page_num = ? AND ann_id = ?",
            (now, doc_id, page_num, ann_id),
        )
        row = conn.execute(
            "SELECT * FROM annotations WHERE doc_id = ? AND page_num = ? AND ann_id = ?",
            (doc_id, page_num, ann_id),
        ).fetchone()
        ann = _annotation_row_to_dict(row)
        _insert_history(conn, doc_id, page_num, ann_id, "delete", ann, author_id)
        conn.commit()
    return True


def list_pages(doc_id: Optional[str] = None) -> List[Tuple[str, str]]:
    """Distinct (doc_id, page_num) pairs that have at least one annotation row
    (any status)."""
    conn = get_connection()
    with _lock:
        if doc_id:
            rows = conn.execute(
                "SELECT DISTINCT doc_id, page_num FROM annotations WHERE doc_id = ? ORDER BY doc_id, page_num",
                (doc_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT doc_id, page_num FROM annotations ORDER BY doc_id, page_num"
            ).fetchall()
    return [(row["doc_id"], row["page_num"]) for row in rows]


def list_doc_ids() -> List[str]:
    conn = get_connection()
    with _lock:
        rows = conn.execute("SELECT DISTINCT doc_id FROM annotations ORDER BY doc_id").fetchall()
    return [row["doc_id"] for row in rows]


# ===== Cabinet (stage 3): filtered lists, history, stats =====


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _annotation_filters(
    doc_id: Optional[str],
    page_num: Optional[str],
    ann_type: Optional[str],
    status: Optional[str],
    author_id: Optional[int],
    q: Optional[str],
) -> Tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if doc_id is not None:
        clauses.append("a.doc_id = ?")
        params.append(doc_id)
    if page_num is not None:
        clauses.append("a.page_num = ?")
        params.append(page_num)
    if ann_type is not None:
        clauses.append("a.ann_type = ?")
        params.append(ann_type)
    if status is not None:
        clauses.append("a.status = ?")
        params.append(status)
    if author_id is not None:
        clauses.append("a.author_id = ?")
        params.append(author_id)
    if q:
        clauses.append("a.text LIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_like(q)}%")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def list_annotations(
    doc_id: Optional[str] = None,
    page_num: Optional[str] = None,
    ann_type: Optional[str] = None,
    status: Optional[str] = None,
    author_id: Optional[int] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    where, params = _annotation_filters(doc_id, page_num, ann_type, status, author_id, q)
    conn = get_connection()
    with _lock:
        rows = conn.execute(
            f"""
            SELECT a.*, u.name AS author_name, u.email AS author_email
            FROM annotations a
            LEFT JOIN users u ON u.id = a.author_id
            {where}
            ORDER BY a.updated_at DESC, a.rowid_pk DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()
    result = []
    for row in rows:
        item = _annotation_row_to_dict(row)
        item["authorName"] = row["author_name"]
        item["authorEmail"] = row["author_email"]
        result.append(item)
    return result


def count_annotations(
    doc_id: Optional[str] = None,
    page_num: Optional[str] = None,
    ann_type: Optional[str] = None,
    status: Optional[str] = None,
    author_id: Optional[int] = None,
    q: Optional[str] = None,
) -> int:
    where, params = _annotation_filters(doc_id, page_num, ann_type, status, author_id, q)
    conn = get_connection()
    with _lock:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM annotations a {where}", params
        ).fetchone()
    return row["n"]


def list_history(
    doc_id: Optional[str] = None,
    page_num: Optional[str] = None,
    ann_id: Optional[str] = None,
    author_id: Optional[int] = None,
    action: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if doc_id is not None:
        clauses.append("h.doc_id = ?")
        params.append(doc_id)
    if page_num is not None:
        clauses.append("h.page_num = ?")
        params.append(page_num)
    if ann_id is not None:
        clauses.append("h.ann_id = ?")
        params.append(ann_id)
    if author_id is not None:
        clauses.append("h.author_id = ?")
        params.append(author_id)
    if action is not None:
        clauses.append("h.action = ?")
        params.append(action)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    conn = get_connection()
    with _lock:
        rows = conn.execute(
            f"""
            SELECT h.*, u.name AS author_name
            FROM annotation_history h
            LEFT JOIN users u ON u.id = h.author_id
            {where}
            ORDER BY h.id DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()
    result = []
    for row in rows:
        try:
            snapshot = json.loads(row["snapshot"])
        except (TypeError, ValueError):
            snapshot = None
        result.append(
            {
                "id": row["id"],
                "docId": row["doc_id"],
                "pageNum": row["page_num"],
                "annId": row["ann_id"],
                "action": row["action"],
                "snapshot": snapshot,
                "authorId": row["author_id"],
                "authorName": row["author_name"],
                "createdAt": row["created_at"],
            }
        )
    return result


def get_history_record(hist_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    with _lock:
        row = conn.execute(
            """
            SELECT h.*, u.name AS author_name
            FROM annotation_history h
            LEFT JOIN users u ON u.id = h.author_id
            WHERE h.id = ?
            """,
            (hist_id,),
        ).fetchone()
    if row is None:
        return None
    try:
        snapshot = json.loads(row["snapshot"])
    except (TypeError, ValueError):
        snapshot = None
    return {
        "id": row["id"],
        "docId": row["doc_id"],
        "pageNum": row["page_num"],
        "annId": row["ann_id"],
        "action": row["action"],
        "snapshot": snapshot,
        "authorId": row["author_id"],
        "authorName": row["author_name"],
        "createdAt": row["created_at"],
    }


def list_users() -> List[Dict[str, Any]]:
    conn = get_connection()
    with _lock:
        rows = conn.execute(
            "SELECT id, email, name, picture_url, role, created_at, last_login_at FROM users ORDER BY created_at DESC"
        ).fetchall()
    return [
        {
            "id": row["id"],
            "email": row["email"],
            "name": row["name"],
            "pictureUrl": row["picture_url"],
            "role": row["role"],
            "createdAt": row["created_at"],
            "lastLoginAt": row["last_login_at"],
        }
        for row in rows
    ]


def get_stats() -> Dict[str, Any]:
    conn = get_connection()
    with _lock:
        rows = conn.execute(
            """
            SELECT doc_id, status, COUNT(*) AS n
            FROM annotations
            GROUP BY doc_id, status
            """
        ).fetchall()
        recent_rows = conn.execute(
            """
            SELECT h.doc_id, h.page_num, h.ann_id, h.action, h.created_at, u.name AS author_name
            FROM annotation_history h
            LEFT JOIN users u ON u.id = h.author_id
            ORDER BY h.id DESC
            LIMIT 10
            """
        ).fetchall()

    docs: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        doc = docs.setdefault(
            row["doc_id"], {"docId": row["doc_id"], "published": 0, "draft": 0, "deleted": 0}
        )
        if row["status"] in doc:
            doc[row["status"]] = row["n"]

    recent_activity = [
        {
            "docId": row["doc_id"],
            "pageNum": row["page_num"],
            "annId": row["ann_id"],
            "action": row["action"],
            "authorName": row["author_name"],
            "createdAt": row["created_at"],
        }
        for row in recent_rows
    ]

    return {
        "docs": sorted(docs.values(), key=lambda d: d["docId"]),
        "recentActivity": recent_activity,
    }
