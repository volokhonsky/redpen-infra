"""
SQLite-backed storage for users, sessions, and the editor allowlist (stage 1).

No ORM: a single module-level connection (check_same_thread=False, guarded by
a lock) is enough since the API runs as a single uvicorn worker.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import config

# Категории живут одним модулем на весь проект (его же читают сборка и тесты).
# В контейнере на sys.path только scripts/api, а репозиторий скопирован целиком
# (см. Dockerfile: COPY . /app/), поэтому добавляем каталог scripts/ руками.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import annotation_categories  # noqa: E402

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
              last_login_at TEXT,
              -- Актор бывает двух видов: человек и агент. Правки агента ничем
              -- не хуже человеческих, но авторство у них разное по природе:
              -- у агента за правкой стоит прогон с версией промпта (agent_runs).
              kind TEXT NOT NULL DEFAULT 'human',
              display_name TEXT,
              -- HMAC(IDENTITY_PEPPER, google_sub). Ни email, ни имени, ни аватара:
              -- см. docs/anonymity-model.md. `sub` сам по себе непрозрачен, а с
              -- перцем, которого нет в бэкапе, хеш ни к кому не привязывается.
              sub_hash TEXT UNIQUE
            );
            -- Прогон агента: что именно и с каким промптом породило правку.
            -- Без этой таблицы «автор» машинной правки — просто токен, и вопрос
            -- «откуда взялась эта формулировка» остаётся без ответа.
            CREATE TABLE IF NOT EXISTS agent_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              actor_id INTEGER NOT NULL REFERENCES users(id),
              agent_name TEXT NOT NULL,
              agent_version TEXT NOT NULL,
              model TEXT,
              prompt_path TEXT,
              prompt_sha256 TEXT,
              params_json TEXT,
              doc_id TEXT,
              section_id TEXT,
              status TEXT NOT NULL DEFAULT 'running',
              notes TEXT,
              started_at TEXT NOT NULL,
              finished_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_agent_runs_actor
              ON agent_runs(actor_id, id);
            CREATE TABLE IF NOT EXISTS sessions (
              id TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL REFERENCES users(id),
              csrf TEXT,
              created_at TEXT NOT NULL,
              expires_at TEXT NOT NULL
            );
            -- Приглашение: одноразовый код, выданный вне системы. Заменил
            -- editor_allowlist по email — тот хранил личности открытым текстом
            -- в БД, которая ежедневно уезжает в бэкап.
            CREATE TABLE IF NOT EXISTS invites (
              code_hash TEXT PRIMARY KEY,
              role TEXT NOT NULL DEFAULT 'editor',
              note TEXT,
              created_by INTEGER REFERENCES users(id),
              created_at TEXT NOT NULL,
              expires_at TEXT,
              used_at TEXT,
              used_by INTEGER REFERENCES users(id)
            );
            -- Историческая таблица: заполнялась email-ами до перехода на инвайты.
            -- Оставлена, пока scripts/api/scrub_identities.py не отработает на проде.
            CREATE TABLE IF NOT EXISTS editor_allowlist (
              email TEXT PRIMARY KEY,
              role TEXT NOT NULL DEFAULT 'editor',
              added_by TEXT,
              added_at TEXT NOT NULL
            );
            -- Параграф учебника. Источник — manifest metadata.json
            -- (chapters[].sections[]), заливается scripts/api/import_sections.py:
            -- API не читает контент-файлы, а работа ведётся именно параграфами,
            -- поэтому диапазоны страниц лежат рядом с аннотациями.
            CREATE TABLE IF NOT EXISTS sections (
              doc_id TEXT NOT NULL,
              section_id TEXT NOT NULL,
              chapter_id TEXT,
              chapter_title TEXT,
              title TEXT NOT NULL,
              page_start INTEGER,
              page_end INTEGER,
              sort_order INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (doc_id, section_id)
            );
            CREATE INDEX IF NOT EXISTS idx_sections_range
              ON sections(doc_id, page_start, page_end);
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
              category TEXT NOT NULL DEFAULT 'other',
              category_source TEXT NOT NULL DEFAULT 'default',
              category_set_by INTEGER REFERENCES users(id),
              author_id INTEGER REFERENCES users(id),
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(doc_id, page_num, ann_id)
            );
            CREATE INDEX IF NOT EXISTS idx_annotations_page ON annotations(doc_id, page_num);
            CREATE TABLE IF NOT EXISTS annotation_tags (
              annotation_pk INTEGER NOT NULL REFERENCES annotations(rowid_pk) ON DELETE CASCADE,
              tag TEXT NOT NULL,
              UNIQUE(annotation_pk, tag)
            );
            CREATE INDEX IF NOT EXISTS idx_annotation_tags_tag ON annotation_tags(tag);
            -- Журнал ревизий. Строка в annotations — это материализованная
            -- «голова» последней ревизии; вся история правок живёт здесь.
            CREATE TABLE IF NOT EXISTS annotation_history (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              doc_id TEXT NOT NULL,
              page_num TEXT NOT NULL,
              ann_id TEXT NOT NULL,
              action TEXT NOT NULL,
              snapshot TEXT NOT NULL,
              author_id INTEGER,
              created_at TEXT NOT NULL,
              rev_no INTEGER,
              parent_rev_id INTEGER,
              agent_run_id INTEGER,
              summary TEXT
            );
            -- TODO(2026-08-21): таблица не используется ни одним кодом --
            -- ревью-подсистему удалили как неподключённую. DDL оставлен, пока
            -- не проверено, что на проде она пуста; после проверки удалить.
            CREATE TABLE IF NOT EXISTS annotation_reviews (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              doc_id TEXT NOT NULL,
              page_num TEXT NOT NULL,
              ann_id TEXT NOT NULL,
              reviewer_id INTEGER NOT NULL REFERENCES users(id),
              verdict TEXT NOT NULL,
              note TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(doc_id, page_num, ann_id, reviewer_id)
            );
            CREATE INDEX IF NOT EXISTS idx_reviews_ann
              ON annotation_reviews(doc_id, page_num, ann_id);
            """
        )
        _migrate_schema(_conn)
        _conn.commit()
    ensure_bootstrap_invite()


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Догоняющие ALTER-ы для баз, созданных до появления колонки.

    CREATE TABLE IF NOT EXISTS ничего не добавляет в уже существующую таблицу,
    поэтому новые колонки заводим здесь. Дефолт 'other' («Прочее») означает,
    что старые строки не ломаются: категория у них просто не проставлена.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(annotations)")}
    if "category" not in columns:
        conn.execute(
            "ALTER TABLE annotations ADD COLUMN category TEXT NOT NULL DEFAULT 'other'"
        )
    if "category_source" not in columns:
        conn.execute(
            "ALTER TABLE annotations ADD COLUMN category_source TEXT NOT NULL DEFAULT 'default'"
        )
    if "category_set_by" not in columns:
        conn.execute(
            "ALTER TABLE annotations ADD COLUMN category_set_by INTEGER REFERENCES users(id)"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_annotations_category ON annotations(category)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_annotations_category_source "
        "ON annotations(doc_id, category_source)"
    )

    users = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if "sub_hash" not in users:
        conn.execute("ALTER TABLE users ADD COLUMN sub_hash TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_sub_hash "
            "ON users(sub_hash) WHERE sub_hash IS NOT NULL"
        )
    if "kind" not in users:
        conn.execute("ALTER TABLE users ADD COLUMN kind TEXT NOT NULL DEFAULT 'human'")
    if "display_name" not in users:
        conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT")

    hist = {row["name"] for row in conn.execute("PRAGMA table_info(annotation_history)")}
    for column, ddl in (
        ("rev_no", "rev_no INTEGER"),
        ("parent_rev_id", "parent_rev_id INTEGER"),
        ("agent_run_id", "agent_run_id INTEGER"),
        ("summary", "summary TEXT"),
    ):
        if column not in hist:
            conn.execute(f"ALTER TABLE annotation_history ADD COLUMN {ddl}")
    # На annotation_history не было ни одного индекса, а на неё завязаны история
    # комментария, «мои правки» и лента изменений — три главных экрана редактора.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_history_ann "
        "ON annotation_history(doc_id, page_num, ann_id, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_history_actor "
        "ON annotation_history(author_id, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_history_run "
        "ON annotation_history(agent_run_id, id)"
    )
    _backfill_revision_numbers(conn)


def _backfill_revision_numbers(conn: sqlite3.Connection) -> None:
    """Проставить rev_no/parent_rev_id ревизиям, записанным до их появления.

    Нумерация выводится из порядка id внутри (doc_id, page_num, ann_id): id —
    автоинкремент, то есть порядок записи, и он надёжнее created_at (метки
    времени огрубляются, а у импорта они и вовсе одинаковые в пределах пакета).
    Идемпотентно: трогает только строки с NULL."""
    if conn.execute(
        "SELECT 1 FROM annotation_history WHERE rev_no IS NULL LIMIT 1"
    ).fetchone() is None:
        return
    rows = conn.execute(
        "SELECT id, doc_id, page_num, ann_id FROM annotation_history "
        "ORDER BY doc_id, page_num, ann_id, id"
    ).fetchall()
    updates = []
    key = None
    rev_no = 0
    parent = None
    for row in rows:
        row_key = (row["doc_id"], row["page_num"], row["ann_id"])
        if row_key != key:
            key, rev_no, parent = row_key, 0, None
        rev_no += 1
        updates.append((rev_no, parent, row["id"]))
        parent = row["id"]
    conn.executemany(
        "UPDATE annotation_history SET rev_no = ?, parent_rev_id = ? WHERE id = ?",
        updates,
    )


def _user_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Актор в том виде, в каком его знает система.

    Ни email, ни имени из Google, ни аватара: их здесь больше не хранят
    (docs/anonymity-model.md). Человека представляет выбранный им псевдоним,
    и он же виден в истории правок."""
    return {
        "id": row["id"],
        "kind": row["kind"],
        "displayName": row["display_name"],
        "role": row["role"],
        "createdAt": row["created_at"],
        "lastLoginAt": row["last_login_at"],
    }


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    with _lock:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _user_row_to_dict(row) if row else None


def list_users(limit: int = 200) -> List[Dict[str, Any]]:
    conn = get_connection()
    with _lock:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY id LIMIT ?", (limit,)
        ).fetchall()
    return [_user_row_to_dict(row) for row in rows]


# ===== Identity: хеш субъекта и приглашения =====


class IdentityError(RuntimeError):
    """Опознание невозможно настроенным образом."""


def hash_subject(sub: str) -> str:
    """HMAC-SHA256(перец, google_sub) — единственное, что мы знаем о человеке.

    Перец живёт только в окружении сервера и не попадает ни в БД, ни в её
    бэкапы. Пустой перец — это не «выключено», а ошибка: без него хеш
    вырождается в обычный sha256 от `sub`, то есть считается кем угодно, у кого
    оказался чужой `sub`."""
    if not config.IDENTITY_PEPPER:
        raise IdentityError("IDENTITY_PEPPER is not configured")
    return hmac.new(
        config.IDENTITY_PEPPER.encode("utf-8"),
        sub.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def hash_invite_code(code: str) -> str:
    """Код приглашения хранится хешем: БД не должна содержать живых ключей."""
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def generate_invite_code() -> str:
    """Код передаётся человеку вне системы, поэтому он должен читаться вслух."""
    return "-".join(secrets.token_hex(2) for _ in range(4))


def create_invite(role: str = "editor", note: Optional[str] = None,
                  created_by: Optional[int] = None,
                  expires_at: Optional[str] = None,
                  code: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """Завести приглашение. Возвращает (код, запись).

    Код возвращается ровно один раз — в БД лежит только его хеш. Потерянный код
    не восстанавливается, выписывается новый."""
    if role not in ("viewer", "editor", "reviewer", "admin"):
        raise ValueError(f"unknown role {role!r}")
    code = code or generate_invite_code()
    conn = get_connection()
    now = _now_iso()
    with _lock:
        conn.execute(
            """
            INSERT INTO invites (code_hash, role, note, created_by, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (hash_invite_code(code), role, note, created_by, now, expires_at),
        )
        conn.commit()
    return code, {"role": role, "note": note, "createdAt": now, "expiresAt": expires_at}


def _invite_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "codeHash": row["code_hash"],
        "role": row["role"],
        "note": row["note"],
        "createdBy": row["created_by"],
        "createdAt": row["created_at"],
        "expiresAt": row["expires_at"],
        "usedAt": row["used_at"],
        "usedBy": row["used_by"],
    }


def list_invites() -> List[Dict[str, Any]]:
    conn = get_connection()
    with _lock:
        rows = conn.execute(
            "SELECT * FROM invites ORDER BY created_at DESC, code_hash"
        ).fetchall()
    return [_invite_row_to_dict(row) for row in rows]


def revoke_invite(code_hash: str) -> bool:
    conn = get_connection()
    with _lock:
        cur = conn.execute(
            "DELETE FROM invites WHERE code_hash = ? AND used_at IS NULL", (code_hash,)
        )
        conn.commit()
    return cur.rowcount > 0


def _claim_invite(conn: sqlite3.Connection, code: str, now: str) -> Optional[str]:
    """Погасить приглашение и вернуть выданную им роль. Вызывается под _lock."""
    row = conn.execute(
        "SELECT * FROM invites WHERE code_hash = ?", (hash_invite_code(code),)
    ).fetchone()
    if row is None or row["used_at"] is not None:
        return None
    if row["expires_at"] and row["expires_at"] < now:
        return None
    return row["role"]


def login_with_google_sub(sub: str, invite_code: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Опознать участника по Google `sub`, при первом входе — по приглашению.

    Возвращает None, если такого участника нет и приглашение не подошло. Это не
    «неверный пароль», а «доступ не выдан»: круг участников закрыт, и вход в
    него происходит только по коду, переданному вне системы.

    Из токена Google берётся ровно `sub`. Email, имя и аватар не читаются и не
    сохраняются — так у изъятого сервера нечего забрать."""
    sub_hash = hash_subject(sub)
    conn = get_connection()
    now = _now_iso()
    with _lock:
        row = conn.execute("SELECT * FROM users WHERE sub_hash = ?", (sub_hash,)).fetchone()
        if row is not None:
            conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, row["id"]))
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
            return _user_row_to_dict(row)

        if not invite_code:
            return None
        role = _claim_invite(conn, invite_code, now)
        if role is None:
            return None
        cur = conn.execute(
            """
            INSERT INTO users (role, created_at, last_login_at, kind, sub_hash)
            VALUES (?, ?, ?, 'human', ?)
            """,
            (role, now, now, sub_hash),
        )
        user_id = cur.lastrowid
        conn.execute(
            "UPDATE invites SET used_at = ?, used_by = ? WHERE code_hash = ?",
            (now, user_id, hash_invite_code(invite_code)),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _user_row_to_dict(row)


def set_display_name(user_id: int, display_name: Optional[str]) -> Optional[Dict[str, Any]]:
    """Псевдоним выбирает сам участник; он же виден в истории правок."""
    name = (display_name or "").strip() or None
    conn = get_connection()
    with _lock:
        conn.execute("UPDATE users SET display_name = ? WHERE id = ?", (name, user_id))
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _user_row_to_dict(row) if row else None


def set_user_role(user_id: int, role: str) -> Optional[Dict[str, Any]]:
    if role not in ("viewer", "editor", "reviewer", "admin"):
        raise ValueError(f"unknown role {role!r}")
    conn = get_connection()
    with _lock:
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _user_row_to_dict(row) if row else None


def retire_user(user_id: int) -> Optional[Dict[str, Any]]:
    """«Уйти по-тихому»: отвязать аккаунт, сохранив связность истории.

    Хеш субъекта и псевдоним стираются, сессии убиваются, роль падает до
    viewer. Ревизии сохраняют author_id, поэтому история остаётся целой — но
    соотнести её с чьим-либо аккаунтом больше нельзя."""
    conn = get_connection()
    with _lock:
        conn.execute(
            "UPDATE users SET sub_hash = NULL, display_name = ?, role = 'viewer' WHERE id = ?",
            (f"Участник №{user_id}", user_id),
        )
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _user_row_to_dict(row) if row else None


def get_or_create_agent_actor(name: str) -> Dict[str, Any]:
    """Актор-агент по имени токена. Роль берётся из конфигурации токена.

    Раньше этот путь заводил пользователя с синтетическим email `token:<имя>` и
    жёстко прошитой ролью editor. Email больше нет, а вид актора теперь честно
    называется агентом — за его правками стоят прогоны (agent_runs)."""
    conn = get_connection()
    now = _now_iso()
    with _lock:
        row = conn.execute(
            "SELECT * FROM users WHERE kind = 'agent' AND display_name = ?", (name,)
        ).fetchone()
        if row is None:
            cur = conn.execute(
                """
                INSERT INTO users (role, created_at, last_login_at, kind, display_name)
                VALUES ('editor', ?, ?, 'agent', ?)
                """,
                (now, now, name),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
        else:
            conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, row["id"]))
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
    return _user_row_to_dict(row)


def ensure_bootstrap_invite() -> None:
    """Выписать admin-приглашение из BOOTSTRAP_INVITE_CODE, пока админов нет.

    Заменяет ADMIN_EMAILS: тот хранил бы личности открытым текстом в окружении
    прода, рядом с бэкапами БД. Код одноразовый и гасится при первом входе; пока
    в системе есть хоть один админ, переменная не делает ничего."""
    if not config.BOOTSTRAP_INVITE_CODE:
        return
    conn = get_connection()
    with _lock:
        if conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone():
            return
        code_hash = hash_invite_code(config.BOOTSTRAP_INVITE_CODE)
        if conn.execute("SELECT 1 FROM invites WHERE code_hash = ?", (code_hash,)).fetchone():
            return
        conn.execute(
            """
            INSERT INTO invites (code_hash, role, note, created_by, created_at)
            VALUES (?, 'admin', 'bootstrap', NULL, ?)
            """,
            (code_hash, _now_iso()),
        )
        conn.commit()


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


# ===== Annotation tags =====

# `status` stays the canonical draft/published/deleted flag; the matching tags
# are *derived* at render time (publisher.render_page_static), never stored, so
# there is no second source of truth to drift. Storing them is therefore an
# error, wherever the write comes from -- the API, import_annotations.py or the
# backfill script -- hence the check lives here rather than in main.py.
RESERVED_TAGS = frozenset({"draft", "published", "deleted"})

# Префикс зеркального тега категории. Тег `cat:<slug>` появляется в
# опубликованном JSON сам (publisher._render_item) и целиком выводится из
# колонки category; принимать его от клиента нельзя — иначе поле и тег
# разъедутся и снова встанет вопрос, какой из них главный.
CATEGORY_TAG_PREFIX = annotation_categories.CAT_PREFIX

#: Откуда взялась категория. Отвечает на вопрос, которого не может ответить сама
#: колонка `category`: «Прочее» там значит одновременно и честное «не приём, а
#: пояснение» (13 % корпуса), и «никто этим не занимался». Приёмка держится ровно
#: на этом различии, а в опубликованный JSON источник не попадает — он служебный.
#:
#:   default       — никто не назначал, стоит дефолт колонки;
#:   tags-backfill — грубая догадка category_for_tags(), требует проверки;
#:   agent         — решение агента-классификатора, ждёт приёмки;
#:   human         — назначено человеком, принято.
CATEGORY_SOURCES = ("default", "tags-backfill", "agent", "human")
DEFAULT_CATEGORY_SOURCE = "default"
#: Источник по умолчанию, когда категорию передали явно, а источник — нет.
#: Явная категория без указания источника приходит из редактора, то есть от человека.
EXPLICIT_CATEGORY_SOURCE = "human"


def normalize_category_source(raw: Any) -> str:
    if raw is None:
        return EXPLICIT_CATEGORY_SOURCE
    if raw not in CATEGORY_SOURCES:
        known = ", ".join(CATEGORY_SOURCES)
        raise ValueError(f"unknown category source {raw!r}; allowed: {known}")
    return raw

MAX_TAG_LENGTH = 64
MAX_TAGS_PER_ANNOTATION = 32

# Lowercase latin/digits, plus "-" inside a word and ":" as the prefix separator
# of the `prefix:value` convention (confidence:high, room for tc: later).
_TAG_RE = re.compile(r"^[a-z0-9]+([-:][a-z0-9]+)*$")


class TagError(ValueError):
    """Raised for a tag that is malformed or reserved."""


def normalize_tag(raw: Any) -> str:
    """Trim + lowercase a tag and validate it. Raises TagError."""
    if not isinstance(raw, str):
        raise TagError("tag must be a string")
    tag = raw.strip().lower()
    if not tag:
        raise TagError("tag must not be empty")
    if len(tag) > MAX_TAG_LENGTH:
        raise TagError(f"tag too long (max {MAX_TAG_LENGTH}): {tag[:MAX_TAG_LENGTH]}...")
    if tag in RESERVED_TAGS:
        raise TagError(f"tag '{tag}' is reserved (mirrors the status column, not stored)")
    if tag.startswith(CATEGORY_TAG_PREFIX):
        raise TagError(
            f"tag '{tag}' is reserved: the category is a field of its own, "
            f"set it via \"category\" instead"
        )
    if not _TAG_RE.match(tag):
        raise TagError(f"tag '{tag}' has invalid characters (allowed: a-z 0-9 - :)")
    return tag


def normalize_tags(raw: Any) -> List[str]:
    """Normalize an iterable of tags, dropping duplicates and keeping order."""
    if raw is None:
        raise TagError("tags must be a list")
    if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
        raise TagError("tags must be a list")
    if len(raw) > MAX_TAGS_PER_ANNOTATION:
        raise TagError(f"too many tags (max {MAX_TAGS_PER_ANNOTATION})")
    seen: List[str] = []
    for item in raw:
        tag = normalize_tag(item)
        if tag not in seen:
            seen.append(tag)
    return seen


def _set_tags(conn: sqlite3.Connection, annotation_pk: int, tags: List[str]) -> None:
    """Replace the tag set of one annotation. Caller holds _lock and commits."""
    conn.execute("DELETE FROM annotation_tags WHERE annotation_pk = ?", (annotation_pk,))
    if tags:
        conn.executemany(
            "INSERT INTO annotation_tags (annotation_pk, tag) VALUES (?, ?)",
            [(annotation_pk, tag) for tag in tags],
        )


def _read_tags(conn: sqlite3.Connection, annotation_pk: int) -> List[str]:
    rows = conn.execute(
        "SELECT tag FROM annotation_tags WHERE annotation_pk = ? ORDER BY tag", (annotation_pk,)
    ).fetchall()
    return [row["tag"] for row in rows]


def _read_tags_batch(conn: sqlite3.Connection, pks: List[int]) -> Dict[int, List[str]]:
    """Tags for many annotations in one query -- the page renderer would
    otherwise do one SELECT per annotation."""
    if not pks:
        return {}
    placeholders = ",".join("?" for _ in pks)
    rows = conn.execute(
        f"SELECT annotation_pk, tag FROM annotation_tags "
        f"WHERE annotation_pk IN ({placeholders}) ORDER BY annotation_pk, tag",
        pks,
    ).fetchall()
    out: Dict[int, List[str]] = {}
    for row in rows:
        out.setdefault(row["annotation_pk"], []).append(row["tag"])
    return out


def get_annotation_tags(annotation_pk: int) -> List[str]:
    conn = get_connection()
    with _lock:
        return _read_tags(conn, annotation_pk)


def list_all_tags(doc_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """[{tag, count}] over non-deleted annotations, most used first."""
    conn = get_connection()
    params: List[Any] = []
    where = "a.status != 'deleted'"
    if doc_id is not None:
        where += " AND a.doc_id = ?"
        params.append(doc_id)
    with _lock:
        rows = conn.execute(
            f"""
            SELECT t.tag AS tag, COUNT(*) AS n
            FROM annotation_tags t
            JOIN annotations a ON a.rowid_pk = t.annotation_pk
            WHERE {where}
            GROUP BY t.tag
            ORDER BY n DESC, t.tag
            """,
            params,
        ).fetchall()
    return [{"tag": row["tag"], "count": row["n"]} for row in rows]


# ===== Agent runs (прогоны агентов) =====


def _agent_run_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "actorId": row["actor_id"],
        "agentName": row["agent_name"],
        "agentVersion": row["agent_version"],
        "model": row["model"],
        "promptPath": row["prompt_path"],
        "promptSha256": row["prompt_sha256"],
        "docId": row["doc_id"],
        "sectionId": row["section_id"],
        "status": row["status"],
        "notes": row["notes"],
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
    }


def start_agent_run(
    actor_id: int,
    agent_name: str,
    agent_version: str,
    model: Optional[str] = None,
    prompt_path: Optional[str] = None,
    prompt_sha256: Optional[str] = None,
    params_json: Optional[str] = None,
    doc_id: Optional[str] = None,
    section_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Открыть прогон. Его id проставляется каждой ревизии этого прогона."""
    conn = get_connection()
    with _lock:
        cur = conn.execute(
            """
            INSERT INTO agent_runs
              (actor_id, agent_name, agent_version, model, prompt_path, prompt_sha256,
               params_json, doc_id, section_id, status, notes, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)
            """,
            (actor_id, agent_name, agent_version, model, prompt_path, prompt_sha256,
             params_json, doc_id, section_id, notes, _now_iso()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _agent_run_row_to_dict(row)


def finish_agent_run(run_id: int, status: str = "done",
                     notes: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    with _lock:
        conn.execute(
            "UPDATE agent_runs SET status = ?, finished_at = ?, "
            "notes = COALESCE(?, notes) WHERE id = ?",
            (status, _now_iso(), notes, run_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
    return _agent_run_row_to_dict(row) if row else None


def get_agent_run(run_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    with _lock:
        row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
    return _agent_run_row_to_dict(row) if row else None


def list_agent_runs(actor_id: Optional[int] = None, doc_id: Optional[str] = None,
                    limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if actor_id is not None:
        clauses.append("r.actor_id = ?")
        params.append(actor_id)
    if doc_id is not None:
        clauses.append("r.doc_id = ?")
        params.append(doc_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = get_connection()
    with _lock:
        rows = conn.execute(
            f"""
            SELECT r.*, (SELECT COUNT(*) FROM annotation_history h
                          WHERE h.agent_run_id = r.id) AS n_revisions
            FROM agent_runs r {where}
            ORDER BY r.id DESC LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()
    result = []
    for row in rows:
        item = _agent_run_row_to_dict(row)
        item["revisionCount"] = row["n_revisions"]
        result.append(item)
    return result


def list_run_revisions(run_id: int) -> List[Dict[str, Any]]:
    """Ревизии одного прогона, старые первыми — что именно он натворил."""
    conn = get_connection()
    with _lock:
        rows = conn.execute(
            "SELECT doc_id, page_num, ann_id, id, rev_no, action, summary "
            "FROM annotation_history WHERE agent_run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
    return [
        {"id": r["id"], "docId": r["doc_id"], "pageNum": r["page_num"],
         "annId": r["ann_id"], "revNo": r["rev_no"], "action": r["action"],
         "summary": r["summary"]}
        for r in rows
    ]


def plan_agent_run_revert(run_id: int) -> List[Dict[str, Any]]:
    """Что нужно сделать, чтобы отменить прогон целиком.

    Для каждой затронутой аннотации ищем ревизию, предшествующую первой ревизии
    этого прогона, и возвращаем её как целевое состояние. Если такой ревизии нет,
    аннотацию создал сам прогон — её нужно удалить (мягко: ничего не стирается).

    Возвращается план, а не результат: откат прогона — операция на сотни строк,
    и человек должен увидеть её объём до, а не после."""
    conn = get_connection()
    with _lock:
        touched = conn.execute(
            "SELECT doc_id, page_num, ann_id, MIN(id) AS first_id "
            "FROM annotation_history WHERE agent_run_id = ? "
            "GROUP BY doc_id, page_num, ann_id ORDER BY doc_id, page_num, ann_id",
            (run_id,),
        ).fetchall()
        plan: List[Dict[str, Any]] = []
        for row in touched:
            before = conn.execute(
                """
                SELECT id, rev_no, snapshot FROM annotation_history
                WHERE doc_id = ? AND page_num = ? AND ann_id = ? AND id < ?
                ORDER BY id DESC LIMIT 1
                """,
                (row["doc_id"], row["page_num"], row["ann_id"], row["first_id"]),
            ).fetchone()
            item = {
                "docId": row["doc_id"],
                "pageNum": row["page_num"],
                "annId": row["ann_id"],
            }
            if before is None:
                item["action"] = "delete"
                item["targetRevId"] = None
                item["targetRevNo"] = None
            else:
                item["action"] = "restore"
                item["targetRevId"] = before["id"]
                item["targetRevNo"] = before["rev_no"]
                try:
                    item["snapshot"] = json.loads(before["snapshot"])
                except (TypeError, ValueError):
                    item["snapshot"] = None
            plan.append(item)
    return plan


# ===== Sections (параграфы) =====


def replace_sections(doc_id: str, sections: List[Dict[str, Any]]) -> int:
    """Переписать список параграфов документа целиком.

    Целиком, а не по одному: manifest — источник правды, и параграф, исчезнувший
    из него, должен исчезнуть и здесь. Аннотации на параграфы не ссылаются
    (связь выводится по диапазону страниц), так что удалять безопасно."""
    conn = get_connection()
    with _lock:
        conn.execute("DELETE FROM sections WHERE doc_id = ?", (doc_id,))
        conn.executemany(
            """
            INSERT INTO sections
              (doc_id, section_id, chapter_id, chapter_title, title,
               page_start, page_end, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (doc_id, str(s["sectionId"]), s.get("chapterId"), s.get("chapterTitle"),
                 s["title"], s.get("pageStart"), s.get("pageEnd"), i)
                for i, s in enumerate(sections)
            ],
        )
        conn.commit()
    return len(sections)


def _section_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "docId": row["doc_id"],
        "sectionId": row["section_id"],
        "chapterId": row["chapter_id"],
        "chapterTitle": row["chapter_title"],
        "title": row["title"],
        "pageStart": row["page_start"],
        "pageEnd": row["page_end"],
    }


#: Страница принадлежит параграфу, если её числовой ключ попадает в диапазон.
#: CAST по TEXT-ключу: '006' → 6, '000' → 0, '-01' → -1, то есть передний блок
#: (обложка, титул) ни в какой параграф не попадает — так и задумано.
_PAGE_IN_SECTION = (
    "CAST(a.page_num AS INTEGER) BETWEEN s.page_start AND s.page_end"
)


def list_sections(doc_id: str) -> List[Dict[str, Any]]:
    """Параграфы документа со сводкой по аннотациям.

    Сводка — это доска работ: сколько всего, сколько опубликовано, сколько
    черновиков и сколько ещё не разобрано по категориям (category_source =
    'default'). Удалённые в счёт не идут."""
    conn = get_connection()
    with _lock:
        rows = conn.execute(
            f"""
            SELECT s.*,
              (SELECT COUNT(*) FROM annotations a
                WHERE a.doc_id = s.doc_id AND a.status != 'deleted'
                  AND {_PAGE_IN_SECTION}) AS n_total,
              (SELECT COUNT(*) FROM annotations a
                WHERE a.doc_id = s.doc_id AND a.status = 'published'
                  AND {_PAGE_IN_SECTION}) AS n_published,
              (SELECT COUNT(*) FROM annotations a
                WHERE a.doc_id = s.doc_id AND a.status = 'draft'
                  AND {_PAGE_IN_SECTION}) AS n_draft,
              (SELECT COUNT(*) FROM annotations a
                WHERE a.doc_id = s.doc_id AND a.status != 'deleted'
                  AND a.category_source = 'default'
                  AND {_PAGE_IN_SECTION}) AS n_unclassified,
              (SELECT MAX(a.updated_at) FROM annotations a
                WHERE a.doc_id = s.doc_id AND {_PAGE_IN_SECTION}) AS last_activity
            FROM sections s
            WHERE s.doc_id = ?
            ORDER BY s.sort_order
            """,
            (doc_id,),
        ).fetchall()
    result = []
    for row in rows:
        item = _section_row_to_dict(row)
        item["counts"] = {
            "total": row["n_total"],
            "published": row["n_published"],
            "draft": row["n_draft"],
            "unclassified": row["n_unclassified"],
        }
        item["lastActivity"] = row["last_activity"]
        result.append(item)
    return result


def find_section_for_page(doc_id: str, page_num: str) -> Optional[Dict[str, Any]]:
    """Параграф, которому принадлежит страница, или None.

    None — законный ответ, а не ошибка: передний блок и аппарат главы
    (например стр. 269-277) не входят ни в один параграф."""
    try:
        page = int(page_num)
    except (TypeError, ValueError):
        return None
    conn = get_connection()
    with _lock:
        row = conn.execute(
            """
            SELECT * FROM sections
            WHERE doc_id = ? AND ? BETWEEN page_start AND page_end
            ORDER BY sort_order LIMIT 1
            """,
            (doc_id, page),
        ).fetchone()
    return _section_row_to_dict(row) if row else None


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
        "category": row["category"],
        "categorySource": row["category_source"],
        "categorySetBy": row["category_set_by"],
        "authorId": row["author_id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _attach_tags(conn: sqlite3.Connection, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add a "tags" list to each annotation dict, in one query. Caller holds _lock."""
    by_pk = _read_tags_batch(conn, [item["rowidPk"] for item in items])
    for item in items:
        item["tags"] = by_pk.get(item["rowidPk"], [])
    return items


def _insert_history(
    conn: sqlite3.Connection,
    doc_id: str,
    page_num: str,
    ann_id: str,
    action: str,
    snapshot: Dict[str, Any],
    author_id: Optional[int],
    summary: Optional[str] = None,
    agent_run_id: Optional[int] = None,
) -> None:
    """Записать ревизию. Вызывается внутри той же транзакции, что и правка.

    `rev_no` и `parent_rev_id` считаются здесь, а не триггером: нумерация ведётся
    в пределах одной аннотации, а не всей таблицы, и должна быть непрерывной,
    чтобы «версия 3» в ссылке означала то же самое завтра."""
    prev = conn.execute(
        """
        SELECT id, rev_no FROM annotation_history
        WHERE doc_id = ? AND page_num = ? AND ann_id = ?
        ORDER BY id DESC LIMIT 1
        """,
        (doc_id, page_num, ann_id),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO annotation_history
          (doc_id, page_num, ann_id, action, snapshot, author_id, created_at,
           rev_no, parent_rev_id, agent_run_id, summary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (doc_id, page_num, ann_id, action, json.dumps(snapshot, ensure_ascii=False),
         author_id, _now_iso(),
         (prev["rev_no"] or 0) + 1 if prev else 1,
         prev["id"] if prev else None,
         agent_run_id, summary),
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
        return _attach_tags(conn, [_annotation_row_to_dict(row) for row in rows])


def list_page_drafts(doc_id: str, page_num: str) -> List[Dict[str, Any]]:
    """Draft (status='draft') annotations for a page, in insertion order. The
    publisher renders them into the same page_<NNN>.json as the published ones,
    each carrying the derived "draft" tag; the viewer filters them out unless
    the URL asks for them (?showDrafts=1 / ?tags=draft)."""
    conn = get_connection()
    with _lock:
        rows = conn.execute(
            "SELECT * FROM annotations WHERE doc_id = ? AND page_num = ? AND status = 'draft' ORDER BY rowid_pk",
            (doc_id, page_num),
        ).fetchall()
        return _attach_tags(conn, [_annotation_row_to_dict(row) for row in rows])


def get_annotation(doc_id: str, page_num: str, ann_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    with _lock:
        row = conn.execute(
            "SELECT * FROM annotations WHERE doc_id = ? AND page_num = ? AND ann_id = ?",
            (doc_id, page_num, ann_id),
        ).fetchone()
        if row is None:
            return None
        return _attach_tags(conn, [_annotation_row_to_dict(row)])[0]


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
    tags: Optional[List[str]] = None,
    category: Optional[str] = None,
    category_source: Optional[str] = None,
    summary: Optional[str] = None,
    agent_run_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Insert or update an annotation by (doc_id, page_num, ann_id) and record
    the resulting state in annotation_history, in the same transaction.

    `tags` is deliberately outside the column upsert: None means "leave the tag
    set alone", [] means "clear it". That way callers that predate tags
    (import_annotations.py, the editor's PUT, history revert) cannot wipe them
    just by not mentioning them.

    `category` follows the same rule for the same reason: None means "leave it
    alone", so a caller that predates categories cannot silently reset an
    annotation to 'other'. A brand-new row gets the column default ('other')
    when the caller says nothing.

    `category_source` moves only together with an explicit `category`: leaving the
    category alone must not silently promote a guess to 'human'. Passing a
    category without a source means a human set it (that is the editor's path);
    CLI backfills name 'tags-backfill' or 'agent' for themselves."""
    conn = get_connection()
    now = _now_iso()
    if tags is not None:
        tags = normalize_tags(tags)
    if category is not None:
        category = annotation_categories.normalize_category(category)
        category_source = normalize_category_source(category_source)
    elif category_source is not None:
        raise ValueError("category_source without category: the source of a category "
                         "that is not being set has no meaning")
    with _lock:
        conn.execute(
            """
            INSERT INTO annotations
              (ann_id, doc_id, page_num, ann_type, text, coord_x, coord_y, status, category,
               category_source, category_set_by, author_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(doc_id, page_num, ann_id) DO UPDATE SET
              ann_type = excluded.ann_type,
              text = excluded.text,
              coord_x = excluded.coord_x,
              coord_y = excluded.coord_y,
              status = excluded.status,
              -- NULL в параметре означает «не трогать»: COALESCE оставляет то,
              -- что уже стоит в строке.
              category = COALESCE(?, annotations.category),
              -- Источник и «кто назначил» едут только вместе с явной категорией:
              -- обычное сохранение текста не должно превращать догадку в решение
              -- человека.
              category_source = CASE WHEN ? IS NULL THEN annotations.category_source
                                     ELSE excluded.category_source END,
              category_set_by = CASE WHEN ? IS NULL THEN annotations.category_set_by
                                     ELSE excluded.category_set_by END,
              author_id = excluded.author_id,
              updated_at = excluded.updated_at
            """,
            (ann_id, doc_id, page_num, ann_type, text, coord_x, coord_y, status,
             category or annotation_categories.DEFAULT_CATEGORY,
             category_source or DEFAULT_CATEGORY_SOURCE,
             author_id if category is not None else None,
             author_id, now, now,
             category, category, category),
        )
        row = conn.execute(
            "SELECT * FROM annotations WHERE doc_id = ? AND page_num = ? AND ann_id = ?",
            (doc_id, page_num, ann_id),
        ).fetchone()
        ann = _annotation_row_to_dict(row)
        if tags is not None:
            _set_tags(conn, ann["rowidPk"], tags)
        ann["tags"] = _read_tags(conn, ann["rowidPk"])
        _insert_history(conn, doc_id, page_num, ann_id, action, ann, author_id,
                        summary=summary, agent_run_id=agent_run_id)
        conn.commit()
    return ann


def soft_delete_annotation(doc_id: str, page_num: str, ann_id: str,
                           author_id: Optional[int] = None,
                           summary: Optional[str] = None) -> bool:
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
        ann["tags"] = _read_tags(conn, ann["rowidPk"])
        _insert_history(conn, doc_id, page_num, ann_id, "delete", ann, author_id,
                        summary=summary)
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
    tag: Optional[str] = None,
    category: Optional[str] = None,
    category_source: Optional[str] = None,
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
    if category is not None:
        clauses.append("a.category = ?")
        params.append(annotation_categories.normalize_category(category))
    if category_source is not None:
        clauses.append("a.category_source = ?")
        params.append(normalize_category_source(category_source))
    if tag:
        # EXISTS rather than a JOIN, so count_annotations needs no DISTINCT.
        clauses.append(
            "EXISTS (SELECT 1 FROM annotation_tags t WHERE t.annotation_pk = a.rowid_pk AND t.tag = ?)"
        )
        params.append(tag.strip().lower())
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
    tag: Optional[str] = None,
    category: Optional[str] = None,
    category_source: Optional[str] = None,
) -> List[Dict[str, Any]]:
    where, params = _annotation_filters(doc_id, page_num, ann_type, status, author_id, q,
                                        tag, category, category_source)
    conn = get_connection()
    with _lock:
        rows = conn.execute(
            f"""
            SELECT a.*, u.display_name AS author_name
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
            # authorEmail не отдаётся: email в системе больше нет
            # (docs/anonymity-model.md).
            result.append(item)
        return _attach_tags(conn, result)


def count_annotations(
    doc_id: Optional[str] = None,
    page_num: Optional[str] = None,
    ann_type: Optional[str] = None,
    status: Optional[str] = None,
    author_id: Optional[int] = None,
    q: Optional[str] = None,
    tag: Optional[str] = None,
    category: Optional[str] = None,
    category_source: Optional[str] = None,
) -> int:
    where, params = _annotation_filters(doc_id, page_num, ann_type, status, author_id, q,
                                        tag, category, category_source)
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
            SELECT h.*, u.display_name AS author_name
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
                "revNo": row["rev_no"],
                "parentRevId": row["parent_rev_id"],
                "agentRunId": row["agent_run_id"],
                "summary": row["summary"],
            }
        )
    return result


def get_history_record(hist_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    with _lock:
        row = conn.execute(
            """
            SELECT h.*, u.display_name AS author_name
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


# Ревью-подсистема (кворум рецензентов) была отсюда удалена 2026-08-21:
# код существовал с этапа 3, но не был подключён ни к API, ни к кабинету,
# ни к тестам. Таблица annotation_reviews в init_db оставлена намеренно
# (см. TODO там). Восстановить можно из git: scripts/api/db.py до этой даты.

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
            SELECT h.doc_id, h.page_num, h.ann_id, h.action, h.created_at, u.display_name AS author_name
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
