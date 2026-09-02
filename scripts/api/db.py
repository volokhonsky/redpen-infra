"""
SQLite-backed storage for users, sessions, and the editor allowlist (stage 1).

No ORM: a single module-level connection (check_same_thread=False, guarded by
a lock) is enough since the API runs as a single uvicorn worker.
"""

import hashlib
import hmac
import json
import logging
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
import remark_kinds  # noqa: E402

import rating_scales
import remark_actions

SESSION_TTL_SECONDS = 30 * 86400  # 30 days, matches the auth cookie max_age

logger = logging.getLogger("redpen.api")

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
        # Строго до executescript: DDL ниже содержит CREATE TABLE IF NOT EXISTS
        # remarks, и на старой базе он создал бы пустышку, после чего
        # ALTER TABLE annotations RENAME TO remarks упал бы, а API не поднялся.
        _rename_legacy_to_remarks(_conn)
        # Тоже строго до executescript, и по той же причине: DDL ниже заводит
        # survey_respondents новой формы, а на старой базе таблица с этим
        # именем уже есть -- со своим token_hash и без UNIQUE на псевдониме.
        _split_survey_sessions_pre(_conn)
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
            -- поэтому диапазоны страниц лежат рядом с замечаниями.
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
            CREATE TABLE IF NOT EXISTS remarks (
              rowid_pk INTEGER PRIMARY KEY AUTOINCREMENT,
              remark_id TEXT NOT NULL,
              doc_id TEXT NOT NULL,
              page_num TEXT NOT NULL,
              kind TEXT NOT NULL,
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
              UNIQUE(doc_id, page_num, remark_id)
            );
            CREATE INDEX IF NOT EXISTS idx_remarks_page ON remarks(doc_id, page_num);
            CREATE TABLE IF NOT EXISTS remark_tags (
              remark_pk INTEGER NOT NULL REFERENCES remarks(rowid_pk) ON DELETE CASCADE,
              tag TEXT NOT NULL,
              UNIQUE(remark_pk, tag)
            );
            CREATE INDEX IF NOT EXISTS idx_remark_tags_tag ON remark_tags(tag);
            -- Журнал ревизий. Строка в remarks — это материализованная
            -- «голова» последней ревизии; вся история правок живёт здесь.
            CREATE TABLE IF NOT EXISTS remark_history (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              doc_id TEXT NOT NULL,
              page_num TEXT NOT NULL,
              remark_id TEXT NOT NULL,
              action TEXT NOT NULL,
              snapshot TEXT NOT NULL,
              author_id INTEGER,
              created_at TEXT NOT NULL,
              rev_no INTEGER,
              parent_rev_id INTEGER,
              agent_run_id INTEGER,
              summary TEXT,
              -- Состав изменения: JSON-массив токенов remark_actions.ACTIONS.
              -- NULL означает «не вычислено» (ревизии до появления колонки).
              changes TEXT
            );
            -- Оценки и комментарии — рабочие данные редактора. Они не меняют
            -- строку в remarks и потому не являются ревизиями: rev_no не должен
            -- сдвигаться от того, что кто-то поставил оценку, иначе «версия 3»
            -- перестанет быть ссылкой. В статику они не попадают никогда
            -- (publisher._render_item перечисляет поля явно).
            CREATE TABLE IF NOT EXISTS remark_ratings (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              doc_id TEXT NOT NULL,
              page_num TEXT NOT NULL,
              remark_id TEXT NOT NULL,
              scale TEXT NOT NULL,
              value INTEGER NOT NULL,
              rater_id INTEGER NOT NULL REFERENCES users(id),
              note TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(doc_id, page_num, remark_id, scale, rater_id)
            );
            CREATE INDEX IF NOT EXISTS idx_remark_ratings_target
              ON remark_ratings(doc_id, page_num, remark_id);
            CREATE INDEX IF NOT EXISTS idx_remark_ratings_rater
              ON remark_ratings(rater_id, id);
            CREATE TABLE IF NOT EXISTS remark_notes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              doc_id TEXT NOT NULL,
              page_num TEXT NOT NULL,
              remark_id TEXT NOT NULL,
              author_id INTEGER NOT NULL REFERENCES users(id),
              body TEXT NOT NULL,
              -- Ответ в треде. NULL — корень треда; ответ на ответ запрещён
              -- (см. add_note): один уровень покрывает рабочее обсуждение и не
              -- требует рекурсивного рендера.
              parent_id INTEGER REFERENCES remark_notes(id),
              resolved_at TEXT,
              resolved_by INTEGER REFERENCES users(id),
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              deleted_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_remark_notes_target
              ON remark_notes(doc_id, page_num, remark_id, id);
            CREATE INDEX IF NOT EXISTS idx_remark_notes_author
              ON remark_notes(author_id, id);

            -- ===== ОПРОС: ПУЛ, РЕСПОНДЕНТЫ, ОТВЕТЫ =====
            --
            -- Опрос (`/survey/`) спрашивает у людей вне закрытого круга то, на
            -- что круг ответить не может: интересен ли факт и можно ли
            -- предъявлять замечание в такой формулировке. К статике всё это
            -- отношения не имеет — как и оценки участников, наружу не выходит.

            -- Что вынесено на оценку. Отдельная таблица, а не тег: теги
            -- рендерятся в redpen-page-data и в читательские фильтры, и
            -- служебная метка уехала бы к читателю.
            CREATE TABLE IF NOT EXISTS rating_pool (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              doc_id TEXT NOT NULL,
              page_num TEXT NOT NULL,
              remark_id TEXT NOT NULL,
              added_by INTEGER REFERENCES users(id),
              added_at TEXT NOT NULL,
              UNIQUE(doc_id, page_num, remark_id)
            );

            -- Респондент опроса -- это псевдоним, а один заход под ним --
            -- сессия (survey_sessions ниже). Разведены они с 2026-09-01: пока
            -- каждый заход заводил своего респондента, вернувшемуся человеку
            -- заново раздавали то, что он уже оценил. Цена решения названа в
            -- docs/anonymity-model.md: имя без секрета, и назваться чужим
            -- псевдонимом может кто угодно.
            --
            -- В `users` респондент намеренно не заводится: users -- это круг
            -- участников, вход в него по приглашению, и пополнять его с улицы
            -- значило бы стереть границу, ради которой приглашения заведены.
            -- Отсюда и отдельная таблица ответов ниже.
            --
            -- Псевдоним хранится как введён: префикс `anonymous:` приписывается
            -- на чтении (survey_author) и потому не подделывается вводом.
            -- Подпись сравнивается посимвольно, как введена (пробелы по краям
            -- и внутри схлопывает `normalize_pseudonym`). «Пётр» и «пётр» —
            -- разные люди: правила, по которому одно написание значит то же,
            -- что другое, нет, а сводить их значило бы отдавать одному
            -- человеку ответы другого.
            CREATE TABLE IF NOT EXISTS survey_respondents (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              pseudonym TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL,
              last_seen_at TEXT
            );

            -- Один заход. От токена в базе остаётся только хеш -- как от кода
            -- приглашения.
            CREATE TABLE IF NOT EXISTS survey_sessions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              respondent_id INTEGER NOT NULL REFERENCES survey_respondents(id),
              token_hash TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL,
              last_seen_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_survey_sessions_respondent
              ON survey_sessions(respondent_id);

            -- Ответ респондента: одна строка на «заход + вопрос» по одному
            -- замечанию. Вопрос (`question`) описан в rating_scales.py; ответ --
            -- либо число (шкала меры или решение «публиковать ли»), либо
            -- свободный текст (что бы вы сказали автору замечания). CHECK
            -- запрещает строку, не несущую ни того, ни другого.
            --
            -- Ключ уникальности посессионный: два захода одного псевдонима,
            -- добравшиеся до одного замечания, оставляют две строки -- лента
            -- показывает события, а не сводку. Повтор при этом маловероятен:
            -- pool_pick не раздаёт уже отвеченное под этой подписью. Сводка
            -- (survey_results) сводит числовой голос к псевдониму, беря
            -- последний ответ; открытые ответы она показывает все -- текст не
            -- усредняется, и терять формулировку ради аккуратности таблицы
            -- незачем.
            --
            -- respondent_id продублирован намеренно: и раздача (pool_pick), и
            -- сводка спрашивают «что уже сказал этот псевдоним», и join через
            -- сессии в каждом таком запросе был бы лишним.
            CREATE TABLE IF NOT EXISTS survey_answers (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              respondent_id INTEGER NOT NULL REFERENCES survey_respondents(id),
              session_id INTEGER NOT NULL REFERENCES survey_sessions(id),
              doc_id TEXT NOT NULL,
              page_num TEXT NOT NULL,
              remark_id TEXT NOT NULL,
              question TEXT NOT NULL,
              value INTEGER,
              text TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(session_id, doc_id, page_num, remark_id, question),
              CHECK (value IS NOT NULL OR text IS NOT NULL)
            );
            CREATE INDEX IF NOT EXISTS idx_survey_answers_target
              ON survey_answers(doc_id, page_num, remark_id);
            CREATE INDEX IF NOT EXISTS idx_survey_answers_respondent
              ON survey_answers(respondent_id);
            """
        )
        _migrate_schema(_conn)
        _conn.commit()
    ensure_bootstrap_invite()



#: Значения вида замечания до переименования сущности (2026-08-29).
LEGACY_KINDS = remark_kinds.LEGACY_KINDS


def _rename_legacy_to_remarks(conn: sqlite3.Connection) -> None:
    """Переименовать annotations/* в remarks/* на базе, созданной до 2026-08-29.

    Одна транзакция: падение посреди откатывает всё, и API просто не поднимется
    на полупереименованной базе. Идемпотентна — на уже мигрированной (или
    пустой) базе выходит сразу.

    Индексы с новыми именами создаст штатный DDL и _migrate_schema(); здесь
    важно снять старые, иначе они переживут переименование таблицы и останутся
    висеть под прежними именами.
    """
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "annotations" not in tables:
        return

    # Ревью-подсистему удалили как неподключённую (2026-08-21), в DDL её нет.
    # Таблицу сносим только если она действительно пуста; иначе переименовываем
    # и оставляем разбираться человеку.
    reviews_rows = 0
    if "annotation_reviews" in tables:
        reviews_rows = conn.execute(
            "SELECT COUNT(*) FROM annotation_reviews").fetchone()[0]

    conn.execute("PRAGMA foreign_keys=off")
    # Чтобы RENAME TO переписал ссылку FK в remark_tags, а не оставил её
    # указывать на исчезнувшее имя.
    conn.execute("PRAGMA legacy_alter_table=off")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ALTER TABLE annotations RENAME TO remarks")
        conn.execute("ALTER TABLE remarks RENAME COLUMN ann_id TO remark_id")
        conn.execute("ALTER TABLE remarks RENAME COLUMN ann_type TO kind")
        conn.execute("ALTER TABLE annotation_tags RENAME TO remark_tags")
        conn.execute("ALTER TABLE remark_tags RENAME COLUMN annotation_pk TO remark_pk")
        conn.execute("ALTER TABLE annotation_history RENAME TO remark_history")
        conn.execute("ALTER TABLE remark_history RENAME COLUMN ann_id TO remark_id")
        for legacy, current in LEGACY_KINDS.items():
            conn.execute("UPDATE remarks SET kind = ? WHERE kind = ?", (current, legacy))
        for index in (
            "idx_annotations_page",
            "idx_annotation_tags_tag",
            "idx_annotations_category",
            "idx_annotations_category_source",
            "idx_history_ann",
            "idx_history_actor",
            "idx_history_run",
            "idx_reviews_ann",
        ):
            conn.execute(f"DROP INDEX IF EXISTS {index}")
        if "annotation_reviews" in tables:
            if reviews_rows:
                conn.execute("ALTER TABLE annotation_reviews RENAME TO remark_reviews")
                conn.execute("ALTER TABLE remark_reviews RENAME COLUMN ann_id TO remark_id")
            else:
                conn.execute("DROP TABLE annotation_reviews")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=on")
    logger.info(
        "schema: annotations -> remarks (reviews rows=%s)", reviews_rows
    )


def normalize_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Привести снапшот ревизии к текущим именам полей.

    Журнал ревизий — аудит, и переписывать его миграцией нельзя: это превратило
    бы запись о прошлом в реконструкцию. Поэтому старые снапшоты (ключи annId /
    annType, значения main / comment) нормализуются на чтении. Бессрочно:
    строки, записанные до переименования, останутся в базе навсегда.
    """
    if not isinstance(snapshot, dict):
        return snapshot
    out = dict(snapshot)
    if "remarkId" not in out and "annId" in out:
        out["remarkId"] = out["annId"]
    kind = out.get("kind") or out.get("annType")
    if kind is not None:
        # Упразднённый general показываем как обычное замечание: тип без якоря
        # на скане больше не существует, см. docs/general-migration-map.json.
        out["kind"] = LEGACY_KINDS.get(kind, "minor" if kind == "general" else kind)
    return out


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Догоняющие ALTER-ы для баз, созданных до появления колонки.

    CREATE TABLE IF NOT EXISTS ничего не добавляет в уже существующую таблицу,
    поэтому новые колонки заводим здесь. Дефолт 'other' («Прочее») означает,
    что старые строки не ломаются: категория у них просто не проставлена.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(remarks)")}
    if "category" not in columns:
        conn.execute(
            "ALTER TABLE remarks ADD COLUMN category TEXT NOT NULL DEFAULT 'other'"
        )
    if "category_source" not in columns:
        conn.execute(
            "ALTER TABLE remarks ADD COLUMN category_source TEXT NOT NULL DEFAULT 'default'"
        )
    if "category_set_by" not in columns:
        conn.execute(
            "ALTER TABLE remarks ADD COLUMN category_set_by INTEGER REFERENCES users(id)"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_remarks_category ON remarks(category)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_remarks_category_source "
        "ON remarks(doc_id, category_source)"
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

    hist = {row["name"] for row in conn.execute("PRAGMA table_info(remark_history)")}
    for column, ddl in (
        ("rev_no", "rev_no INTEGER"),
        ("parent_rev_id", "parent_rev_id INTEGER"),
        ("agent_run_id", "agent_run_id INTEGER"),
        ("summary", "summary TEXT"),
        # Состав изменения. Существующие строки остаются с NULL: заполняет их
        # scripts/api/backfill_history_changes.py, а не старт API — на проде
        # тысячи ревизий, и разовая операция не должна тормозить запуск.
        ("changes", "changes TEXT"),
    ):
        if column not in hist:
            conn.execute(f"ALTER TABLE remark_history ADD COLUMN {ddl}")
    # На remark_history не было ни одного индекса, а на неё завязаны история
    # замечания, «мои правки» и лента изменений — три главных экрана редактора.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_remark_history_target "
        "ON remark_history(doc_id, page_num, remark_id, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_remark_history_actor "
        "ON remark_history(author_id, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_remark_history_run "
        "ON remark_history(agent_run_id, id)"
    )
    _backfill_revision_numbers(conn)
    _retire_reviewer_role(conn)
    _migrate_statuses_to_archived(conn)
    _split_survey_sessions_post(conn)


def _retire_reviewer_role(conn: sqlite3.Connection) -> None:
    """Упразднить роль `reviewer` (2026-08-31).

    Роль обещала «принимает чужие черновики и предложения категорий», но ни
    одна ветка кода никогда не давала ей ничего сверх редакторских прав, а
    кабинет её и вовсе не пускал: `viewer < editor < reviewer < admin` была
    лестницей на бумаге. Оставлять значение, которое ничего не значит, — способ
    однажды выдать его человеку и удивиться.

    Идёт после `executescript`: колонка `role` к этому моменту уже есть. Не
    путать с `_rename_legacy_to_remarks`, который обязан идти строго до.
    """
    # Колонки проверяем: сюда попадают и базы старше самой роли, где `role`
    # ещё нет (её добавляет DDL выше только для новых таблиц).
    def has_role(table):
        return any(row["name"] == "role"
                   for row in conn.execute("PRAGMA table_info(%s)" % table))

    if has_role("users"):
        conn.execute("UPDATE users SET role = 'editor' WHERE role = 'reviewer'")
    # Невыбранные приглашения тоже: код уже у человека на руках, и войти по
    # нему он должен редактором, а не получить 400.
    if has_role("invites"):
        conn.execute(
            "UPDATE invites SET role = 'editor' WHERE role = 'reviewer' AND used_at IS NULL"
        )


def _migrate_statuses_to_archived(conn: sqlite3.Connection) -> None:
    """Перевести status='deleted' → 'archived' (2026-09).

    Мягкое удаление всегда и было архивом — обратимым, скрытым отовсюду, но не
    стёртым. Отдельным значением его делает то, что теперь рядом появилось
    настоящее удаление (`purge_remark`), и слово «удалён» больше не должно
    значить «убран, но цел».

    Идёт после `executescript`, тем же приёмом, что и `_retire_reviewer_role`.
    Не путать с `_rename_legacy_to_remarks`, который обязан идти строго до DDL.

    Журнал ревизий не трогаем: старые снапшоты со `status='deleted'`
    нормализуются на чтении там же, где и легаси-имена полей (см.
    `normalize_snapshot`, `remark_actions.diff_snapshots`).
    """
    if any(row["name"] == "status"
           for row in conn.execute("PRAGMA table_info(remarks)")):
        conn.execute(
            "UPDATE remarks SET status = 'archived' WHERE status = 'deleted'"
        )


def _split_survey_sessions_pre(conn: sqlite3.Connection) -> None:
    """Первая половина разделения «псевдоним ↔ сессия» (2026-09-01).

    До этой правки строка `survey_respondents` была и тем, и другим: заход
    заводил нового респондента, и вернувшемуся человеку заново раздавали уже
    оценённое. Теперь респондент — псевдоним, а заход — строка
    `survey_sessions`.

    Здесь только уводим старые таблицы в сторону; переливает данные
    `_split_survey_sessions_post`. Разделение на две половины избавляет от
    дубля DDL: новые таблицы создаст штатный `executescript` между ними.
    Поэтому эта половина обязана идти строго до него — иначе
    `CREATE TABLE IF NOT EXISTS survey_respondents` увидит старую таблицу и
    промолчит, оставив базу в прежней форме.

    Идемпотентна: на новой (и на пустой) базе выходит сразу.
    """
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "survey_respondents" not in tables or "_survey_respondents_old" in tables:
        return
    columns = {row["name"] for row in conn.execute(
        "PRAGMA table_info(survey_respondents)")}
    if "token_hash" not in columns:
        return  # уже новая форма
    conn.execute("ALTER TABLE survey_respondents RENAME TO _survey_respondents_old")
    if "survey_ratings" in tables:
        conn.execute("ALTER TABLE survey_ratings RENAME TO _survey_ratings_old")
    # Индексы переезжают вместе с таблицей и остаются висеть под прежними
    # именами — снимаем, штатный DDL заведёт их заново на новых таблицах.
    conn.execute("DROP INDEX IF EXISTS idx_survey_ratings_target")
    conn.commit()


def _split_survey_sessions_post(conn: sqlite3.Connection) -> None:
    """Вторая половина разделения «псевдоним ↔ сессия»: переливка.

    Идёт после `executescript` — новые таблицы к этому моменту созданы. Каждая
    старая строка респондента становится сессией (токен у неё свой), а
    одинаковые псевдонимы схлопываются в одного респондента: в этом и была
    правка. Заодно ответы переезжают в `survey_answers` (`scale` -> `question`,
    к числу добавлен текст) -- отдельной миграции для этого не нужно: обе
    правки застают базу в одной и той же прежней форме. Коллизий уникальности при переливке ответов не бывает — ключ
    посессионный, а сессия ровно одна на старую строку.

    Не путать с `_split_survey_sessions_pre`, который обязан идти строго до DDL.
    """
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "_survey_respondents_old" not in tables:
        return

    old_rows = conn.execute(
        "SELECT id, pseudonym, token_hash, created_at, last_seen_at "
        "FROM _survey_respondents_old ORDER BY id"
    ).fetchall()

    respondent_by_name: Dict[str, int] = {}
    session_by_old_id: Dict[int, int] = {}
    for row in old_rows:
        name = row["pseudonym"]
        if name not in respondent_by_name:
            cur = conn.execute(
                "INSERT INTO survey_respondents (pseudonym, created_at, last_seen_at) "
                "VALUES (?, ?, ?)",
                (name, row["created_at"], row["last_seen_at"]),
            )
            respondent_by_name[name] = cur.lastrowid
        else:
            # Границы обоих заходов: начало — самое раннее, активность — самая
            # поздняя. Строки идут по возрастанию id, то есть по времени.
            conn.execute(
                "UPDATE survey_respondents SET last_seen_at = ? WHERE id = ? "
                "AND (last_seen_at IS NULL OR last_seen_at < ?)",
                (row["last_seen_at"], respondent_by_name[name], row["last_seen_at"]),
            )
        cur = conn.execute(
            "INSERT INTO survey_sessions (respondent_id, token_hash, created_at, last_seen_at) "
            "VALUES (?, ?, ?, ?)",
            (respondent_by_name[name], row["token_hash"], row["created_at"],
             row["last_seen_at"]),
        )
        session_by_old_id[row["id"]] = cur.lastrowid

    if "_survey_ratings_old" in tables:
        for row in conn.execute(
            "SELECT respondent_id, doc_id, page_num, remark_id, scale, value, "
            "created_at, updated_at FROM _survey_ratings_old ORDER BY id"
        ).fetchall():
            session_id = session_by_old_id.get(row["respondent_id"])
            if session_id is None:
                # Ответ без респондента: внешнего ключа на рабочем соединении
                # нет, так что теоретически возможен. Переносить его некуда и
                # незачем — он и в сводке ничей.
                continue
            # scale -> question: до 2026-09-01 ответом могло быть только
            # число, и колонка называлась по единственному своему содержимому.
            # Открытых ответов в переносимых данных нет по построению.
            conn.execute(
                "INSERT INTO survey_answers (respondent_id, session_id, doc_id, "
                "page_num, remark_id, question, value, created_at, updated_at) "
                "VALUES ((SELECT respondent_id FROM survey_sessions WHERE id = ?), "
                "?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, session_id, row["doc_id"], row["page_num"],
                 row["remark_id"], row["scale"], row["value"],
                 row["created_at"], row["updated_at"]),
            )
        conn.execute("DROP TABLE _survey_ratings_old")
    conn.execute("DROP TABLE _survey_respondents_old")
    conn.commit()


def _backfill_revision_numbers(conn: sqlite3.Connection) -> None:
    """Проставить rev_no/parent_rev_id ревизиям, записанным до их появления.

    Нумерация выводится из порядка id внутри (doc_id, page_num, remark_id): id —
    автоинкремент, то есть порядок записи, и он надёжнее created_at (метки
    времени огрубляются, а у импорта они и вовсе одинаковые в пределах пакета).
    Идемпотентно: трогает только строки с NULL."""
    if conn.execute(
        "SELECT 1 FROM remark_history WHERE rev_no IS NULL LIMIT 1"
    ).fetchone() is None:
        return
    rows = conn.execute(
        "SELECT id, doc_id, page_num, remark_id FROM remark_history "
        "ORDER BY doc_id, page_num, remark_id, id"
    ).fetchall()
    updates = []
    key = None
    rev_no = 0
    parent = None
    for row in rows:
        row_key = (row["doc_id"], row["page_num"], row["remark_id"])
        if row_key != key:
            key, rev_no, parent = row_key, 0, None
        rev_no += 1
        updates.append((rev_no, parent, row["id"]))
        parent = row["id"]
    conn.executemany(
        "UPDATE remark_history SET rev_no = ?, parent_rev_id = ? WHERE id = ?",
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
    if role not in ("viewer", "editor", "admin"):
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
    if role not in ("viewer", "editor", "admin"):
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

# `status` stays the canonical draft/published/archived flag; the matching tags
# are *derived* at render time (publisher.render_page_static), never stored, so
# there is no second source of truth to drift. Storing them is therefore an
# error, wherever the write comes from -- the API, import_remarks.py or the
# backfill script -- hence the check lives here rather than in main.py.
#
# `deleted` stays reserved though the status is gone (2026-09, migrated to
# `archived`): un-reserving an old name only invites a tag that means nothing.
RESERVED_TAGS = frozenset({"draft", "published", "deleted", "archived"})

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


def _set_tags(conn: sqlite3.Connection, remark_pk: int, tags: List[str]) -> None:
    """Replace the tag set of one remark. Caller holds _lock and commits."""
    conn.execute("DELETE FROM remark_tags WHERE remark_pk = ?", (remark_pk,))
    if tags:
        conn.executemany(
            "INSERT INTO remark_tags (remark_pk, tag) VALUES (?, ?)",
            [(remark_pk, tag) for tag in tags],
        )


def _read_tags(conn: sqlite3.Connection, remark_pk: int) -> List[str]:
    rows = conn.execute(
        "SELECT tag FROM remark_tags WHERE remark_pk = ? ORDER BY tag", (remark_pk,)
    ).fetchall()
    return [row["tag"] for row in rows]


def _read_tags_batch(conn: sqlite3.Connection, pks: List[int]) -> Dict[int, List[str]]:
    """Tags for many remarks in one query -- the page renderer would
    otherwise do one SELECT per remark."""
    if not pks:
        return {}
    placeholders = ",".join("?" for _ in pks)
    rows = conn.execute(
        f"SELECT remark_pk, tag FROM remark_tags "
        f"WHERE remark_pk IN ({placeholders}) ORDER BY remark_pk, tag",
        pks,
    ).fetchall()
    out: Dict[int, List[str]] = {}
    for row in rows:
        out.setdefault(row["remark_pk"], []).append(row["tag"])
    return out


def list_all_tags(doc_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """[{tag, count}] over non-archived remarks, most used first."""
    conn = get_connection()
    params: List[Any] = []
    where = "a.status != 'archived'"
    if doc_id is not None:
        where += " AND a.doc_id = ?"
        params.append(doc_id)
    with _lock:
        rows = conn.execute(
            f"""
            SELECT t.tag AS tag, COUNT(*) AS n
            FROM remark_tags t
            JOIN remarks a ON a.rowid_pk = t.remark_pk
            WHERE {where}
            GROUP BY t.tag
            ORDER BY n DESC, t.tag
            """,
            params,
        ).fetchall()
    return [{"tag": row["tag"], "count": row["n"]} for row in rows]


# ===== Agent runs (прогоны агентов) =====
#
# ВНИМАНИЕ: подсистема НЕ подключена к API. Ни один эндпоинт в main.py не
# вызывает start_agent_run/finish_agent_run, поэтому agent_runs на проде пуста,
# а agent_run_id во всех мутациях остаётся None. Живыми её держат только тесты
# (tests/test_agent_runs.py). Это заготовка под §3 «Агенты как участники с
# авторством» плана docs/editor-app-plan-2026-08.md; удалять до его исполнения
# не нужно, но и считать работающей — тоже.


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
            SELECT r.*, (SELECT COUNT(*) FROM remark_history h
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
            "SELECT doc_id, page_num, remark_id, id, rev_no, action, summary "
            "FROM remark_history WHERE agent_run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
    return [
        {"id": r["id"], "docId": r["doc_id"], "pageNum": r["page_num"],
         "remarkId": r["remark_id"], "revNo": r["rev_no"], "action": r["action"],
         "summary": r["summary"]}
        for r in rows
    ]


def plan_agent_run_revert(run_id: int) -> List[Dict[str, Any]]:
    """Что нужно сделать, чтобы отменить прогон целиком.

    Для каждого затронутого замечания ищем ревизию, предшествующую первой ревизии
    этого прогона, и возвращаем её как целевое состояние. Если такой ревизии нет,
    замечание создал сам прогон — его нужно удалить (мягко: ничего не стирается).

    Возвращается план, а не результат: откат прогона — операция на сотни строк,
    и человек должен увидеть её объём до, а не после."""
    conn = get_connection()
    with _lock:
        touched = conn.execute(
            "SELECT doc_id, page_num, remark_id, MIN(id) AS first_id "
            "FROM remark_history WHERE agent_run_id = ? "
            "GROUP BY doc_id, page_num, remark_id ORDER BY doc_id, page_num, remark_id",
            (run_id,),
        ).fetchall()
        plan: List[Dict[str, Any]] = []
        for row in touched:
            before = conn.execute(
                """
                SELECT id, rev_no, snapshot FROM remark_history
                WHERE doc_id = ? AND page_num = ? AND remark_id = ? AND id < ?
                ORDER BY id DESC LIMIT 1
                """,
                (row["doc_id"], row["page_num"], row["remark_id"], row["first_id"]),
            ).fetchone()
            item = {
                "docId": row["doc_id"],
                "pageNum": row["page_num"],
                "remarkId": row["remark_id"],
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
                    item["snapshot"] = normalize_snapshot(json.loads(before["snapshot"]))
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
    """Параграфы документа со сводкой по замечаниям.

    Сводка — это доска работ: сколько всего, сколько опубликовано, сколько
    черновиков и сколько ещё не разобрано по категориям (category_source =
    'default'). Архивные в счёт не идут."""
    conn = get_connection()
    with _lock:
        rows = conn.execute(
            f"""
            SELECT s.*,
              (SELECT COUNT(*) FROM remarks a
                WHERE a.doc_id = s.doc_id AND a.status != 'archived'
                  AND {_PAGE_IN_SECTION}) AS n_total,
              (SELECT COUNT(*) FROM remarks a
                WHERE a.doc_id = s.doc_id AND a.status = 'published'
                  AND {_PAGE_IN_SECTION}) AS n_published,
              (SELECT COUNT(*) FROM remarks a
                WHERE a.doc_id = s.doc_id AND a.status = 'draft'
                  AND {_PAGE_IN_SECTION}) AS n_draft,
              (SELECT COUNT(*) FROM remarks a
                WHERE a.doc_id = s.doc_id AND a.status != 'archived'
                  AND a.category_source = 'default'
                  AND {_PAGE_IN_SECTION}) AS n_unclassified,
              (SELECT MAX(a.updated_at) FROM remarks a
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


def _remark_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "rowidPk": row["rowid_pk"],
        "remarkId": row["remark_id"],
        "docId": row["doc_id"],
        "pageNum": row["page_num"],
        "kind": row["kind"],
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
    """Add a "tags" list to each remark dict, in one query. Caller holds _lock."""
    by_pk = _read_tags_batch(conn, [item["rowidPk"] for item in items])
    for item in items:
        item["tags"] = by_pk.get(item["rowidPk"], [])
    return items


def _pool_state_batch(conn: sqlite3.Connection,
                      keys: List[Tuple[str, str, str]]) -> Dict[Tuple[str, str, str], int]:
    """Членство в пуле опроса и число полученных ответов — одним запросом на
    всю выдачу, а не по запросу на замечание.

    Ключ составной, поэтому склеиваем его через `\n`: перевод строки не может
    попасть ни в docId, ни в ключ страницы, ни в id замечания (все три
    проверяются регекспами на входе API). Индекс при этом не работает, но пул —
    сотни строк по замыслу: это список вопросов для человека, а не таблица
    данных.
    """
    if not keys:
        return {}
    joined = ["\n".join(k) for k in keys]
    placeholders = ",".join("?" for _ in joined)
    rows = conn.execute(
        f"""
        SELECT p.doc_id, p.page_num, p.remark_id,
               (SELECT COUNT(DISTINCT s.respondent_id) FROM survey_answers s
                 WHERE s.doc_id = p.doc_id AND s.page_num = p.page_num
                   AND s.remark_id = p.remark_id) AS answers
        FROM rating_pool p
        WHERE p.doc_id || char(10) || p.page_num || char(10) || p.remark_id
              IN ({placeholders})
        """,
        joined,
    ).fetchall()
    return {(r["doc_id"], r["page_num"], r["remark_id"]): r["answers"] for r in rows}


def _attach_pool(conn: sqlite3.Connection, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Проставить `inPool`/`poolAnswers` каждому замечанию. Caller holds _lock.

    Вызывается только из редакторских чтений и никогда — из тех, что питают
    публикацию: пул не является свойством замечания для читателя и в статику
    попадать не должен (`rating_pool` для того и заведена отдельной таблицей,
    а не тегом).
    """
    state = _pool_state_batch(
        conn, [(i["docId"], i["pageNum"], i["remarkId"]) for i in items]
    )
    for item in items:
        key = (item["docId"], item["pageNum"], item["remarkId"])
        item["inPool"] = key in state
        item["poolAnswers"] = state.get(key, 0)
    return items


def _insert_history(
    conn: sqlite3.Connection,
    doc_id: str,
    page_num: str,
    remark_id: str,
    action: str,
    snapshot: Dict[str, Any],
    author_id: Optional[int],
    summary: Optional[str] = None,
    agent_run_id: Optional[int] = None,
) -> None:
    """Записать ревизию. Вызывается внутри той же транзакции, что и правка.

    `rev_no` и `parent_rev_id` считаются здесь, а не триггером: нумерация ведётся
    в пределах одного замечания, а не всей таблицы, и должна быть непрерывной,
    чтобы «версия 3» в ссылке означала то же самое завтра.

    Состав изменения (`changes`) вычисляется здесь же диффом со снимком
    предыдущей ревизии: одно сохранение может менять несколько вещей сразу, а
    направление перехода статуса (публикация против возврата в черновики) видно
    только тому, кто знает оба состояния. Запрос за родителем всё равно нужен
    ради нумерации — снимок берётся из него же, лишнего обращения не возникает.
    """
    prev = conn.execute(
        """
        SELECT id, rev_no, snapshot FROM remark_history
        WHERE doc_id = ? AND page_num = ? AND remark_id = ?
        ORDER BY id DESC LIMIT 1
        """,
        (doc_id, page_num, remark_id),
    ).fetchone()
    prev_snapshot = None
    unreadable_parent = False
    if prev is not None:
        try:
            prev_snapshot = normalize_snapshot(json.loads(prev["snapshot"]))
        except (TypeError, ValueError):
            # Битый снимок в журнале не повод срывать правку, но и сравнивать с
            # пустотой нельзя: вышло бы «изменилось всё». Состав остаётся
            # невычисленным, как у ревизий до появления колонки.
            unreadable_parent = True
    if unreadable_parent:
        changes = None
    else:
        changes = remark_actions.with_provenance(
            action, remark_actions.diff_snapshots(prev_snapshot, snapshot)
        )
    conn.execute(
        """
        INSERT INTO remark_history
          (doc_id, page_num, remark_id, action, snapshot, author_id, created_at,
           rev_no, parent_rev_id, agent_run_id, summary, changes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (doc_id, page_num, remark_id, action, json.dumps(snapshot, ensure_ascii=False),
         author_id, _now_iso(),
         (prev["rev_no"] or 0) + 1 if prev else 1,
         prev["id"] if prev else None,
         agent_run_id, summary,
         json.dumps(changes, ensure_ascii=False) if changes is not None else None),
    )


def add_history(
    doc_id: str,
    page_num: str,
    remark_id: str,
    action: str,
    snapshot: Dict[str, Any],
    author_id: Optional[int] = None,
) -> None:
    conn = get_connection()
    with _lock:
        _insert_history(conn, doc_id, page_num, remark_id, action, snapshot, author_id)
        conn.commit()


def list_page_remarks(doc_id: str, page_num: str, include_archived: bool = False) -> List[Dict[str, Any]]:
    conn = get_connection()
    with _lock:
        if include_archived:
            rows = conn.execute(
                "SELECT * FROM remarks WHERE doc_id = ? AND page_num = ? ORDER BY rowid_pk",
                (doc_id, page_num),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM remarks WHERE doc_id = ? AND page_num = ? AND status = 'published' ORDER BY rowid_pk",
                (doc_id, page_num),
            ).fetchall()
        return _attach_tags(conn, [_remark_row_to_dict(row) for row in rows])


def list_page_drafts(doc_id: str, page_num: str) -> List[Dict[str, Any]]:
    """Draft (status='draft') remarks for a page, in insertion order. The
    publisher renders them into the same page_<NNN>.json as the published ones,
    each carrying the derived "draft" tag; the viewer filters them out unless
    the URL asks for them (?showDrafts=1 / ?tags=draft)."""
    conn = get_connection()
    with _lock:
        rows = conn.execute(
            "SELECT * FROM remarks WHERE doc_id = ? AND page_num = ? AND status = 'draft' ORDER BY rowid_pk",
            (doc_id, page_num),
        ).fetchall()
        return _attach_tags(conn, [_remark_row_to_dict(row) for row in rows])


def get_remark(doc_id: str, page_num: str, remark_id: str,
               with_pool: bool = False) -> Optional[Dict[str, Any]]:
    """Одно замечание. `with_pool` — по требованию, а не всегда: эту функцию
    зовут и импорт, и бэкфилл, и проверки существования, которым лишний запрос
    в пул ни к чему, а сведения о нём — тем более."""
    conn = get_connection()
    with _lock:
        row = conn.execute(
            "SELECT * FROM remarks WHERE doc_id = ? AND page_num = ? AND remark_id = ?",
            (doc_id, page_num, remark_id),
        ).fetchone()
        if row is None:
            return None
        items = _attach_tags(conn, [_remark_row_to_dict(row)])
        if with_pool:
            _attach_pool(conn, items)
        return items[0]


def upsert_remark_db(
    doc_id: str,
    page_num: str,
    remark_id: str,
    kind: str,
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
    """Insert or update a remark by (doc_id, page_num, remark_id) and record
    the resulting state in remark_history, in the same transaction.

    `kind` принимается и в прежних именах (main/comment): импорт старых
    выгрузок и бэкапов идёт сюда напрямую, и нормализация здесь — гарантия, что
    после переименования в таблице не заведётся ни одной старой строки, чей бы
    вызов её ни принёс.

    `tags` is deliberately outside the column upsert: None means "leave the tag
    set alone", [] means "clear it". That way callers that predate tags
    (import_remarks.py, the editor's PUT, history revert) cannot wipe them
    just by not mentioning them.

    `category` follows the same rule for the same reason: None means "leave it
    alone", so a caller that predates categories cannot silently reset an
    remark to 'other'. A brand-new row gets the column default ('other')
    when the caller says nothing.

    `category_source` moves only together with an explicit `category`: leaving the
    category alone must not silently promote a guess to 'human'. Passing a
    category without a source means a human set it (that is the editor's path);
    CLI backfills name 'tags-backfill' or 'agent' for themselves."""
    kind = LEGACY_KINDS.get(kind, kind)
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
            INSERT INTO remarks
              (remark_id, doc_id, page_num, kind, text, coord_x, coord_y, status, category,
               category_source, category_set_by, author_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(doc_id, page_num, remark_id) DO UPDATE SET
              kind = excluded.kind,
              text = excluded.text,
              coord_x = excluded.coord_x,
              coord_y = excluded.coord_y,
              status = excluded.status,
              -- NULL в параметре означает «не трогать»: COALESCE оставляет то,
              -- что уже стоит в строке.
              category = COALESCE(?, remarks.category),
              -- Источник и «кто назначил» едут только вместе с явной категорией:
              -- обычное сохранение текста не должно превращать догадку в решение
              -- человека.
              category_source = CASE WHEN ? IS NULL THEN remarks.category_source
                                     ELSE excluded.category_source END,
              category_set_by = CASE WHEN ? IS NULL THEN remarks.category_set_by
                                     ELSE excluded.category_set_by END,
              author_id = excluded.author_id,
              updated_at = excluded.updated_at
            """,
            (remark_id, doc_id, page_num, kind, text, coord_x, coord_y, status,
             category or annotation_categories.DEFAULT_CATEGORY,
             category_source or DEFAULT_CATEGORY_SOURCE,
             author_id if category is not None else None,
             author_id, now, now,
             category, category, category),
        )
        row = conn.execute(
            "SELECT * FROM remarks WHERE doc_id = ? AND page_num = ? AND remark_id = ?",
            (doc_id, page_num, remark_id),
        ).fetchone()
        ann = _remark_row_to_dict(row)
        if tags is not None:
            _set_tags(conn, ann["rowidPk"], tags)
        ann["tags"] = _read_tags(conn, ann["rowidPk"])
        _insert_history(conn, doc_id, page_num, remark_id, action, ann, author_id,
                        summary=summary, agent_run_id=agent_run_id)
        conn.commit()
    return ann


def archive_remark(doc_id: str, page_num: str, remark_id: str,
                   author_id: Optional[int] = None,
                   summary: Optional[str] = None,
                   agent_run_id: Optional[int] = None) -> bool:
    """Убрать замечание в архив (status='archived') и записать ревизию.

    Обратимо: вернуть замечание из архива можно узким `set_status_db`
    (`PATCH .../status`), diff подпишет переход `restore`. Возвращает False,
    если замечания нет или оно уже в архиве.
    """
    conn = get_connection()
    now = _now_iso()
    with _lock:
        row = conn.execute(
            "SELECT * FROM remarks WHERE doc_id = ? AND page_num = ? AND remark_id = ?",
            (doc_id, page_num, remark_id),
        ).fetchone()
        if row is None or row["status"] == "archived":
            return False
        conn.execute(
            "UPDATE remarks SET status = 'archived', updated_at = ? WHERE doc_id = ? AND page_num = ? AND remark_id = ?",
            (now, doc_id, page_num, remark_id),
        )
        row = conn.execute(
            "SELECT * FROM remarks WHERE doc_id = ? AND page_num = ? AND remark_id = ?",
            (doc_id, page_num, remark_id),
        ).fetchone()
        ann = _remark_row_to_dict(row)
        ann["tags"] = _read_tags(conn, ann["rowidPk"])
        # agent_run_id — чтобы архивация, сделанная агентом, привязывалась к
        # прогону так же, как правка: иначе «откатить прогон целиком» её не
        # увидит.
        _insert_history(conn, doc_id, page_num, remark_id, "archive", ann, author_id,
                        summary=summary, agent_run_id=agent_run_id)
        conn.commit()
    return True


def purge_remark(doc_id: str, page_num: str, remark_id: str,
                 author_id: Optional[int],
                 summary: Optional[str] = None) -> bool:
    """Стереть замечание навсегда: строку `remarks`, все связанные данные и всю
    историю правок. Остаётся ровно одна запись в журнале — `action='purge'`:
    кто и когда стёр, плюс снимок головы на момент удаления.

    Необратимо. Применимо при любом статусе — и к архивному, и к живому
    замечанию (в последнем случае вызывающий обязан перепубликовать страницу).
    Возвращает False, если замечания нет.

    Всё одной транзакцией под `_lock`. Связанные таблицы вычищаются явно:
    `PRAGMA foreign_keys` на рабочем соединении не включён (см. get_connection),
    на `ON DELETE CASCADE` у `remark_tags` полагаться нельзя, а у оценок,
    комментариев, пула и ответов опроса внешнего ключа к `remarks` нет вовсе.
    """
    conn = get_connection()
    with _lock:
        head = _head_snapshot(conn, doc_id, page_num, remark_id)
        if head is None:
            return False
        remark_pk = head["rowidPk"]
        max_rev = conn.execute(
            "SELECT MAX(rev_no) AS m FROM remark_history "
            "WHERE doc_id = ? AND page_num = ? AND remark_id = ?",
            (doc_id, page_num, remark_id),
        ).fetchone()["m"] or 0

        conn.execute("DELETE FROM remark_tags WHERE remark_pk = ?", (remark_pk,))
        for table in ("remark_ratings", "remark_notes", "rating_pool",
                      "survey_answers"):
            conn.execute(
                f"DELETE FROM {table} WHERE doc_id = ? AND page_num = ? AND remark_id = ?",
                (doc_id, page_num, remark_id),
            )
        conn.execute(
            "DELETE FROM remarks WHERE doc_id = ? AND page_num = ? AND remark_id = ?",
            (doc_id, page_num, remark_id),
        )
        conn.execute(
            "DELETE FROM remark_history WHERE doc_id = ? AND page_num = ? AND remark_id = ?",
            (doc_id, page_num, remark_id),
        )
        # Прямой INSERT, а не _insert_history: тот считает rev_no и дифф от
        # предыдущей ревизии, которой уже нет.
        conn.execute(
            """
            INSERT INTO remark_history
              (doc_id, page_num, remark_id, action, snapshot, author_id, created_at,
               rev_no, parent_rev_id, agent_run_id, summary, changes)
            VALUES (?, ?, ?, 'purge', ?, ?, ?, ?, NULL, NULL, ?, ?)
            """,
            (doc_id, page_num, remark_id,
             json.dumps(head, ensure_ascii=False), author_id, _now_iso(),
             max_rev + 1, summary,
             json.dumps(["purge"], ensure_ascii=False)),
        )
        conn.commit()
    return True


def _head_snapshot(conn: sqlite3.Connection, doc_id: str, page_num: str,
                   remark_id: str) -> Optional[Dict[str, Any]]:
    """Снимок текущего состояния замечания. Вызывающий держит _lock."""
    row = conn.execute(
        "SELECT * FROM remarks WHERE doc_id = ? AND page_num = ? AND remark_id = ?",
        (doc_id, page_num, remark_id),
    ).fetchone()
    if row is None:
        return None
    ann = _remark_row_to_dict(row)
    ann["tags"] = _read_tags(conn, ann["rowidPk"])
    return ann


# Узкие операции ниже существуют отдельно от upsert_remark_db по двум причинам.
# Во-первых, upsert перечисляет все изменяемые колонки, и вызывающий, которому
# нужна одна, вынужден присылать всё замечание целиком. Во-вторых, upsert
# переписывает author_id на того, кто сохраняет: для правки текста это верно, а
# для публикации чужого черновика или смены его категории — нет, чужая работа не
# должна менять авторство. Кто принял решение о категории, помнит category_set_by.

def _set_field_db(doc_id: str, page_num: str, remark_id: str, apply,
                  action: str = "update", author_id: Optional[int] = None,
                  summary: Optional[str] = None,
                  agent_run_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Общий каркас узких правок: проверить, что замечание есть → применить
    изменение → перечитать голову → записать ревизию, всё в одной транзакции.

    `apply(conn, head, now)` вносит собственно правку; различаются три
    вызывающих только ею. Возврат None означает «замечания нет».
    """
    conn = get_connection()
    now = _now_iso()
    with _lock:
        head = _head_snapshot(conn, doc_id, page_num, remark_id)
        if head is None:
            return None
        apply(conn, head, now)
        ann = _head_snapshot(conn, doc_id, page_num, remark_id)
        _insert_history(conn, doc_id, page_num, remark_id, action, ann, author_id,
                        summary=summary, agent_run_id=agent_run_id)
        conn.commit()
    return ann


def set_status_db(doc_id: str, page_num: str, remark_id: str, status: str,
                  author_id: Optional[int] = None, summary: Optional[str] = None,
                  agent_run_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Сменить статус замечания и записать ревизию. None — замечания нет."""
    def apply(conn, head, now):
        conn.execute(
            "UPDATE remarks SET status = ?, updated_at = ? "
            "WHERE doc_id = ? AND page_num = ? AND remark_id = ?",
            (status, now, doc_id, page_num, remark_id),
        )
    return _set_field_db(doc_id, page_num, remark_id, apply, author_id=author_id,
                         summary=summary, agent_run_id=agent_run_id)


def set_category_db(doc_id: str, page_num: str, remark_id: str,
                    category: Optional[str], category_source: Optional[str] = None,
                    author_id: Optional[int] = None, summary: Optional[str] = None,
                    action: str = "update",
                    agent_run_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Сменить категорию и записать ревизию.

    `category=None` означает сброс в «Прочее» — здесь, в отличие от
    upsert_remark_db, None не может значить «не трогать»: смена категории —
    единственное, зачем эту функцию зовут.
    """
    norm_category = annotation_categories.normalize_category(category)
    norm_source = normalize_category_source(category_source)

    def apply(conn, head, now):
        conn.execute(
            "UPDATE remarks SET category = ?, category_source = ?, category_set_by = ?, "
            "updated_at = ? WHERE doc_id = ? AND page_num = ? AND remark_id = ?",
            (norm_category, norm_source, author_id, now, doc_id, page_num, remark_id),
        )
    return _set_field_db(doc_id, page_num, remark_id, apply, action=action,
                         author_id=author_id, summary=summary, agent_run_id=agent_run_id)


def set_tags_db(doc_id: str, page_num: str, remark_id: str, tags: List[str],
                author_id: Optional[int] = None, summary: Optional[str] = None,
                agent_run_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Заменить набор тегов и записать ревизию. Пустой список очищает теги."""
    norm_tags = normalize_tags(tags)

    def apply(conn, head, now):
        _set_tags(conn, head["rowidPk"], norm_tags)
        conn.execute(
            "UPDATE remarks SET updated_at = ? "
            "WHERE doc_id = ? AND page_num = ? AND remark_id = ?",
            (now, doc_id, page_num, remark_id),
        )
    return _set_field_db(doc_id, page_num, remark_id, apply, author_id=author_id,
                         summary=summary, agent_run_id=agent_run_id)


def list_pages(doc_id: Optional[str] = None) -> List[Tuple[str, str]]:
    """Distinct (doc_id, page_num) pairs that have at least one remark row
    (any status)."""
    conn = get_connection()
    with _lock:
        if doc_id:
            rows = conn.execute(
                "SELECT DISTINCT doc_id, page_num FROM remarks WHERE doc_id = ? ORDER BY doc_id, page_num",
                (doc_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT doc_id, page_num FROM remarks ORDER BY doc_id, page_num"
            ).fetchall()
    return [(row["doc_id"], row["page_num"]) for row in rows]


def list_doc_ids() -> List[str]:
    conn = get_connection()
    with _lock:
        rows = conn.execute("SELECT DISTINCT doc_id FROM remarks ORDER BY doc_id").fetchall()
    return [row["doc_id"] for row in rows]


# ===== Cabinet (stage 3): filtered lists, history, stats =====


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _remark_filters(
    doc_id: Optional[str],
    page_num: Optional[str],
    kind: Optional[str],
    status: Optional[str],
    author_id: Optional[int],
    q: Optional[str],
    tag: Optional[str] = None,
    category: Optional[str] = None,
    category_source: Optional[str] = None,
    section_id: Optional[str] = None,
    in_pool: Optional[bool] = None,
    include_archived: bool = False,
) -> Tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if doc_id is not None:
        clauses.append("a.doc_id = ?")
        params.append(doc_id)
    if page_num is not None:
        clauses.append("a.page_num = ?")
        params.append(page_num)
    if kind is not None:
        clauses.append("a.kind = ?")
        params.append(kind)
    if status is not None:
        clauses.append("a.status = ?")
        params.append(status)
    elif not include_archived:
        # Архив нигде не всплывает сам: увидеть его можно только явным
        # ?status=archived или ?includeArchived=true (вкладка «Архив»).
        clauses.append("a.status != 'archived'")
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
    if section_id is not None:
        # Параграф — это диапазон страниц, отдельной колонки у замечания нет:
        # связь выводится, а не хранится, иначе её пришлось бы чинить при
        # каждой правке манифеста.
        clauses.append(
            "EXISTS (SELECT 1 FROM sections s WHERE s.doc_id = a.doc_id "
            "AND s.section_id = ? AND " + _PAGE_IN_SECTION + ")"
        )
        params.append(section_id)
    if tag:
        # EXISTS rather than a JOIN, so count_remarks needs no DISTINCT.
        clauses.append(
            "EXISTS (SELECT 1 FROM remark_tags t WHERE t.remark_pk = a.rowid_pk AND t.tag = ?)"
        )
        params.append(tag.strip().lower())
    if in_pool is not None:
        # EXISTS/NOT EXISTS, а не JOIN: count_remarks иначе потребовал бы
        # DISTINCT — та же причина, что и у тегов выше.
        clauses.append(
            ("" if in_pool else "NOT ")
            + "EXISTS (SELECT 1 FROM rating_pool p WHERE p.doc_id = a.doc_id "
            "AND p.page_num = a.page_num AND p.remark_id = a.remark_id)"
        )
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def list_remarks(
    doc_id: Optional[str] = None,
    page_num: Optional[str] = None,
    kind: Optional[str] = None,
    status: Optional[str] = None,
    author_id: Optional[int] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    tag: Optional[str] = None,
    category: Optional[str] = None,
    category_source: Optional[str] = None,
    section_id: Optional[str] = None,
    in_pool: Optional[bool] = None,
    with_pool: bool = False,
    include_archived: bool = False,
) -> List[Dict[str, Any]]:
    """`with_pool` не включён по умолчанию: этой же выборкой пользуется
    отрисовка страницы редактора (GET /api/editor/...), которая соседствует с
    публикацией — там сведений о пуле быть не должно.

    `include_archived` действует только когда `status` не задан: без него
    архивные замечания в выдачу не попадают (см. `_remark_filters`)."""
    where, params = _remark_filters(doc_id, page_num, kind, status, author_id, q,
                                        tag, category, category_source, section_id,
                                        in_pool, include_archived)
    conn = get_connection()
    with _lock:
        rows = conn.execute(
            f"""
            SELECT a.*, u.display_name AS author_name
            FROM remarks a
            LEFT JOIN users u ON u.id = a.author_id
            {where}
            ORDER BY a.updated_at DESC, a.rowid_pk DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()
        result = []
        for row in rows:
            item = _remark_row_to_dict(row)
            item["authorName"] = row["author_name"]
            # authorEmail не отдаётся: email в системе больше нет
            # (docs/anonymity-model.md).
            result.append(item)
        _attach_tags(conn, result)
        if with_pool:
            _attach_pool(conn, result)
        return result


def count_remarks(
    doc_id: Optional[str] = None,
    page_num: Optional[str] = None,
    kind: Optional[str] = None,
    status: Optional[str] = None,
    author_id: Optional[int] = None,
    q: Optional[str] = None,
    tag: Optional[str] = None,
    category: Optional[str] = None,
    category_source: Optional[str] = None,
    section_id: Optional[str] = None,
    in_pool: Optional[bool] = None,
    include_archived: bool = False,
) -> int:
    where, params = _remark_filters(doc_id, page_num, kind, status, author_id, q,
                                        tag, category, category_source, section_id,
                                        in_pool, include_archived)
    conn = get_connection()
    with _lock:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM remarks a {where}", params
        ).fetchone()
    return row["n"]


def _history_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Одна сериализация ревизии на всех читателей.

    До этого `get_history_record` отдавала словарь уже, чем `list_history` (без
    `revNo`, `parentRevId`, `agentRunId`, `summary`), и одна и та же ревизия
    выглядела по-разному в зависимости от того, каким путём её запросили.
    """
    try:
        snapshot = normalize_snapshot(json.loads(row["snapshot"]))
    except (TypeError, ValueError):
        snapshot = None
    try:
        changes = json.loads(row["changes"]) if row["changes"] else None
    except (TypeError, ValueError):
        changes = None
    if changes is not None and not isinstance(changes, list):
        changes = None
    return {
        "id": row["id"],
        "docId": row["doc_id"],
        "pageNum": row["page_num"],
        "remarkId": row["remark_id"],
        "action": row["action"],
        "snapshot": snapshot,
        "authorId": row["author_id"],
        "authorName": row["author_name"],
        "createdAt": row["created_at"],
        "revNo": row["rev_no"],
        "parentRevId": row["parent_rev_id"],
        "agentRunId": row["agent_run_id"],
        "summary": row["summary"],
        "changes": changes,
        # Ярлык считается на сервере, чтобы не заводить JS-двойник словаря
        # действий (см. remark_actions).
        "actionLabel": remark_actions.label(changes),
    }


def list_history(
    doc_id: Optional[str] = None,
    page_num: Optional[str] = None,
    remark_id: Optional[str] = None,
    author_id: Optional[int] = None,
    action: Optional[str] = None,
    changed: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Ревизии, новые сверху.

    `action` фильтрует по происхождению записи (create/update/import/...),
    `changed` — по составу изменения (токен remark_actions.ACTIONS). Это разные
    вопросы: «кто это записал» и «что при этом изменилось».
    """
    clauses: List[str] = []
    params: List[Any] = []
    if doc_id is not None:
        clauses.append("h.doc_id = ?")
        params.append(doc_id)
    if page_num is not None:
        clauses.append("h.page_num = ?")
        params.append(page_num)
    if remark_id is not None:
        clauses.append("h.remark_id = ?")
        params.append(remark_id)
    if author_id is not None:
        clauses.append("h.author_id = ?")
        params.append(author_id)
    if action is not None:
        clauses.append("h.action = ?")
        params.append(action)
    if changed is not None:
        # json_each по колонке: точное сравнение с элементом массива, в отличие
        # от LIKE, который спутал бы 'text' с гипотетическим 'text-something'.
        clauses.append(
            "EXISTS (SELECT 1 FROM json_each(h.changes) WHERE json_each.value = ?)"
        )
        params.append(changed)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    conn = get_connection()
    with _lock:
        rows = conn.execute(
            f"""
            SELECT h.*, u.display_name AS author_name
            FROM remark_history h
            LEFT JOIN users u ON u.id = h.author_id
            {where}
            ORDER BY h.id DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()
    return [_history_row_to_dict(row) for row in rows]


def get_history_record(hist_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    with _lock:
        row = conn.execute(
            """
            SELECT h.*, u.display_name AS author_name
            FROM remark_history h
            LEFT JOIN users u ON u.id = h.author_id
            WHERE h.id = ?
            """,
            (hist_id,),
        ).fetchone()
    if row is None:
        return None
    return _history_row_to_dict(row)


# Ревью-подсистема (кворум рецензентов) была отсюда удалена 2026-08-21:
# код существовал с этапа 3, но не был подключён ни к API, ни к кабинету,
# ни к тестам. Восстановить можно из git: scripts/api/db.py до этой даты.
# В DDL init_db таблицы нет; remark_reviews может существовать только в базе,
# приехавшей из-под старого имени с непустым annotation_reviews — см.
# _rename_legacy_to_remarks().

# ===== ОЦЕНКИ =====
#
# Одна оценка на (замечание, шкала, участник). Повторная оценка перезаписывает
# прежнюю: журнала изменения оценок нет сознательно — история «было 2, стало 4»
# удвоила бы объём без ясного применения. Если она понадобится, это отдельная
# работа, а не побочный эффект этой.


def _rating_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "docId": row["doc_id"],
        "pageNum": row["page_num"],
        "remarkId": row["remark_id"],
        "scale": row["scale"],
        "value": row["value"],
        "raterId": row["rater_id"],
        "raterName": row["rater_name"] if "rater_name" in row.keys() else None,
        "note": row["note"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def set_rating(doc_id: str, page_num: str, remark_id: str, scale: str, value: int,
               rater_id: int, note: Optional[str] = None) -> Dict[str, Any]:
    """Поставить или изменить свою оценку. Значения нормализует вызывающий
    (rating_scales), сюда приходят уже проверенные."""
    conn = get_connection()
    now = _now_iso()
    with _lock:
        conn.execute(
            """
            INSERT INTO remark_ratings
              (doc_id, page_num, remark_id, scale, value, rater_id, note,
               created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(doc_id, page_num, remark_id, scale, rater_id) DO UPDATE SET
              value = excluded.value,
              note = excluded.note,
              updated_at = excluded.updated_at
            """,
            (doc_id, page_num, remark_id, scale, value, rater_id, note, now, now),
        )
        row = conn.execute(
            """
            SELECT r.*, u.display_name AS rater_name
            FROM remark_ratings r
            LEFT JOIN users u ON u.id = r.rater_id
            WHERE r.doc_id = ? AND r.page_num = ? AND r.remark_id = ?
              AND r.scale = ? AND r.rater_id = ?
            """,
            (doc_id, page_num, remark_id, scale, rater_id),
        ).fetchone()
        conn.commit()
    return _rating_row_to_dict(row)


def clear_rating(doc_id: str, page_num: str, remark_id: str, scale: str,
                 rater_id: int) -> bool:
    """Снять свою оценку. False — её и не было."""
    conn = get_connection()
    with _lock:
        cur = conn.execute(
            "DELETE FROM remark_ratings WHERE doc_id = ? AND page_num = ? "
            "AND remark_id = ? AND scale = ? AND rater_id = ?",
            (doc_id, page_num, remark_id, scale, rater_id),
        )
        conn.commit()
    return cur.rowcount > 0


def list_ratings(doc_id: str, page_num: str, remark_id: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    with _lock:
        rows = conn.execute(
            """
            SELECT r.*, u.display_name AS rater_name
            FROM remark_ratings r
            LEFT JOIN users u ON u.id = r.rater_id
            WHERE r.doc_id = ? AND r.page_num = ? AND r.remark_id = ?
            ORDER BY r.scale, r.id
            """,
            (doc_id, page_num, remark_id),
        ).fetchall()
    return [_rating_row_to_dict(row) for row in rows]


def summarize_ratings(doc_id: str, page_num: str, remark_id: str,
                      rater_id: Optional[int] = None) -> Dict[str, Any]:
    """Сводка по шкалам: среднее, число оценивших и оценка спрашивающего.

    Среднее округляется до одного знака — большая точность на трёх оценках
    была бы обманом.
    """
    ratings = list_ratings(doc_id, page_num, remark_id)
    by_scale: Dict[str, Dict[str, Any]] = {}
    for scale in rating_scales.names():
        values = [r["value"] for r in ratings if r["scale"] == scale]
        mine = next((r for r in ratings
                     if r["scale"] == scale and r["raterId"] == rater_id), None)
        by_scale[scale] = {
            "scale": scale,
            "count": len(values),
            "average": round(sum(values) / len(values), 1) if values else None,
            "mine": mine["value"] if mine else None,
            "myNote": mine["note"] if mine else None,
        }
    return by_scale


# ===== КОММЕНТАРИИ УЧАСТНИКОВ =====
#
# Рабочее обсуждение замечания. Внутренние: в статику не рендерятся и через
# читательские маршруты не отдаются. Свободный текст — единственное место в
# системе, куда участник может вписать личные данные по своей воле
# (docs/anonymity-model.md).

#: Комментарий — обсуждение, а не второй текст замечания, но и не однострочное
#: резюме правки: разбор источника занимает абзац-другой.
MAX_NOTE_BODY = 4000

#: Чем заменяется тело удалённого комментария. Мягкое удаление сохраняет строку
#: ради связности треда, но «удалить» должно означать удалить.
DELETED_NOTE_BODY = ""


class NoteError(ValueError):
    """Некорректный комментарий: пустой, слишком длинный или ответ на ответ."""


def normalize_note_body(raw: Any) -> str:
    body = str(raw or "").strip()
    if not body:
        raise NoteError("body must not be empty")
    if len(body) > MAX_NOTE_BODY:
        raise NoteError(f"body must be at most {MAX_NOTE_BODY} characters")
    return body


def _note_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    deleted = row["deleted_at"] is not None
    return {
        "id": row["id"],
        "docId": row["doc_id"],
        "pageNum": row["page_num"],
        "remarkId": row["remark_id"],
        "authorId": row["author_id"],
        "authorName": row["author_name"] if "author_name" in row.keys() else None,
        "body": row["body"],
        "parentId": row["parent_id"],
        "resolved": row["resolved_at"] is not None,
        "resolvedAt": row["resolved_at"],
        "resolvedBy": row["resolved_by"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "deleted": deleted,
    }


def add_note(doc_id: str, page_num: str, remark_id: str, author_id: int, body: str,
             parent_id: Optional[int] = None) -> Dict[str, Any]:
    """Добавить комментарий или ответ на него.

    Тред ровно в один уровень: ответить можно только на корневой комментарий.
    Глубже дерево пришлось бы рендерить рекурсивно, а обсуждение одного
    замечания в такой глубине не нуждается.
    """
    body = normalize_note_body(body)
    conn = get_connection()
    now = _now_iso()
    with _lock:
        if parent_id is not None:
            parent = conn.execute(
                "SELECT id, parent_id, doc_id, page_num, remark_id, deleted_at "
                "FROM remark_notes WHERE id = ?",
                (parent_id,),
            ).fetchone()
            if parent is None:
                raise NoteError("parent note not found")
            if parent["parent_id"] is not None:
                raise NoteError("replies are one level deep: reply to the root note")
            if (parent["doc_id"], parent["page_num"], parent["remark_id"]) != \
                    (doc_id, page_num, remark_id):
                raise NoteError("parent note belongs to another remark")
            if parent["deleted_at"] is not None:
                raise NoteError("cannot reply to a deleted note")
        cur = conn.execute(
            """
            INSERT INTO remark_notes
              (doc_id, page_num, remark_id, author_id, body, parent_id,
               created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (doc_id, page_num, remark_id, author_id, body, parent_id, now, now),
        )
        row = conn.execute(
            "SELECT n.*, u.display_name AS author_name FROM remark_notes n "
            "LEFT JOIN users u ON u.id = n.author_id WHERE n.id = ?",
            (cur.lastrowid,),
        ).fetchone()
        conn.commit()
    return _note_row_to_dict(row)


def get_note(note_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    with _lock:
        row = conn.execute(
            "SELECT n.*, u.display_name AS author_name FROM remark_notes n "
            "LEFT JOIN users u ON u.id = n.author_id WHERE n.id = ?",
            (note_id,),
        ).fetchone()
    return _note_row_to_dict(row) if row is not None else None


def list_notes(doc_id: str, page_num: str, remark_id: str,
               include_deleted: bool = False) -> List[Dict[str, Any]]:
    """Комментарии замечания в порядке записи: корни и ответы вперемешку,
    связь — через parentId. Порядок по id, а не по дате: метки времени огрублены
    до дня (docs/anonymity-model.md), и сортировать по ним нечем."""
    conn = get_connection()
    where = "n.doc_id = ? AND n.page_num = ? AND n.remark_id = ?"
    if not include_deleted:
        where += " AND n.deleted_at IS NULL"
    with _lock:
        rows = conn.execute(
            f"""
            SELECT n.*, u.display_name AS author_name
            FROM remark_notes n
            LEFT JOIN users u ON u.id = n.author_id
            WHERE {where}
            ORDER BY n.id
            """,
            (doc_id, page_num, remark_id),
        ).fetchall()
    return [_note_row_to_dict(row) for row in rows]


def count_open_notes(doc_id: str, page_num: str, remark_id: str) -> int:
    """Сколько нерешённых тредов у замечания. Считаются корни: ответ закрывать
    отдельно не нужно, тред решается целиком."""
    conn = get_connection()
    with _lock:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM remark_notes "
            "WHERE doc_id = ? AND page_num = ? AND remark_id = ? "
            "AND parent_id IS NULL AND deleted_at IS NULL AND resolved_at IS NULL",
            (doc_id, page_num, remark_id),
        ).fetchone()
    return row["n"]


def edit_note(note_id: int, body: str) -> Optional[Dict[str, Any]]:
    """Поправить текст комментария. Права проверяет вызывающий."""
    body = normalize_note_body(body)
    conn = get_connection()
    with _lock:
        cur = conn.execute(
            "UPDATE remark_notes SET body = ?, updated_at = ? "
            "WHERE id = ? AND deleted_at IS NULL",
            (body, _now_iso(), note_id),
        )
        conn.commit()
    if cur.rowcount == 0:
        return None
    return get_note(note_id)


def resolve_note(note_id: int, resolved: bool, actor_id: int) -> Optional[Dict[str, Any]]:
    """Пометить тред решённым или снова открыть. Только корень треда."""
    conn = get_connection()
    with _lock:
        row = conn.execute(
            "SELECT parent_id, deleted_at FROM remark_notes WHERE id = ?",
            (note_id,),
        ).fetchone()
        if row is None or row["deleted_at"] is not None:
            return None
        if row["parent_id"] is not None:
            raise NoteError("only the root note of a thread can be resolved")
        conn.execute(
            "UPDATE remark_notes SET resolved_at = ?, resolved_by = ?, updated_at = ? "
            "WHERE id = ?",
            (_now_iso() if resolved else None,
             actor_id if resolved else None,
             _now_iso(), note_id),
        )
        conn.commit()
    return get_note(note_id)


def delete_note(note_id: int) -> Optional[Dict[str, Any]]:
    """Мягко удалить комментарий: строка остаётся ради связности треда, тело
    затирается. Права проверяет вызывающий."""
    conn = get_connection()
    now = _now_iso()
    with _lock:
        cur = conn.execute(
            "UPDATE remark_notes SET deleted_at = ?, updated_at = ?, body = ? "
            "WHERE id = ? AND deleted_at IS NULL",
            (now, now, DELETED_NOTE_BODY, note_id),
        )
        conn.commit()
    if cur.rowcount == 0:
        return None
    return get_note(note_id)


def get_stats() -> Dict[str, Any]:
    conn = get_connection()
    with _lock:
        rows = conn.execute(
            """
            SELECT doc_id, status, COUNT(*) AS n
            FROM remarks
            GROUP BY doc_id, status
            """
        ).fetchall()
        recent_rows = conn.execute(
            """
            SELECT h.doc_id, h.page_num, h.remark_id, h.action, h.created_at, u.display_name AS author_name
            FROM remark_history h
            LEFT JOIN users u ON u.id = h.author_id
            ORDER BY h.id DESC
            LIMIT 10
            """
        ).fetchall()

    docs: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        doc = docs.setdefault(
            row["doc_id"], {"docId": row["doc_id"], "published": 0, "draft": 0, "archived": 0}
        )
        if row["status"] in doc:
            doc[row["status"]] = row["n"]

    recent_activity = [
        {
            "docId": row["doc_id"],
            "pageNum": row["page_num"],
            "remarkId": row["remark_id"],
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


# ===== ОПРОС: ПУЛ, РЕСПОНДЕНТЫ, ОТВЕТЫ =====
#
# Опрос спрашивает у людей вне закрытого круга то, на что круг ответить не
# может: интересен ли факт и можно ли предъявлять замечание в такой
# формулировке. Всё, что здесь есть, — рабочие данные: в статику не попадает
# ничего, ревизиями ответы не становятся (как и оценки участников).

#: Подпись респондента. Приписывается на чтении, в базе не хранится — потому
#: и не подделывается вводом псевдонима (docs/anonymity-model.md).
SURVEY_AUTHOR_PREFIX = "anonymous:"

#: Псевдоним — подпись, а не имя. Верхняя граница не техническая: длинная
#: строка на этом месте почти всегда означает, что человек вписал туда что-то
#: лишнее.
MIN_PSEUDONYM_LENGTH = 2
MAX_PSEUDONYM_LENGTH = 32

#: Сколько замечаний выдаётся за один заход опроса.
SURVEY_BATCH_SIZE = 10
MAX_SURVEY_BATCH_SIZE = 50


class SurveyError(ValueError):
    """Негодный псевдоним или неизвестный респондент."""


def survey_author(pseudonym: str) -> str:
    return SURVEY_AUTHOR_PREFIX + pseudonym


def normalize_pseudonym(raw: Any) -> str:
    """Псевдоним респондента.

    Двоеточие запрещено намеренно: подпись строится как `anonymous:<псевдоним>`,
    и без запрета человек мог бы представиться `anonymous:Пётр` и получить
    подпись, неотличимую от чужой. Управляющие символы убираются по той же
    причине — подпись должна выглядеть в списке так же, как её ввели.
    """
    name = " ".join(str(raw or "").split())
    if ":" in name:
        raise SurveyError("pseudonym must not contain ':'")
    if any(ch < " " or ch == "\x7f" for ch in name):
        raise SurveyError("pseudonym must not contain control characters")
    if not MIN_PSEUDONYM_LENGTH <= len(name) <= MAX_PSEUDONYM_LENGTH:
        raise SurveyError(
            f"pseudonym must be {MIN_PSEUDONYM_LENGTH}..{MAX_PSEUDONYM_LENGTH} characters"
        )
    return name


# --- пул ------------------------------------------------------------------


def _pool_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    item = {
        "docId": row["doc_id"],
        "pageNum": row["page_num"],
        "remarkId": row["remark_id"],
        "addedAt": row["added_at"],
        "addedBy": row["added_by"],
    }
    for extra, key in (("text", "text"), ("status", "status"), ("kind", "kind"),
                       ("answers", "answers")):
        if extra in row.keys():
            item[key] = row[extra]
    return item


def pool_add(doc_id: str, page_num: str, remark_id: str,
             added_by: Optional[int] = None) -> Dict[str, Any]:
    """Внести замечание в пул. Повтор — не ошибка: «уже там» и «положили» для
    вызывающего одно и то же."""
    conn = get_connection()
    now = _now_iso()
    with _lock:
        conn.execute(
            "INSERT OR IGNORE INTO rating_pool (doc_id, page_num, remark_id, added_by, added_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (doc_id, page_num, remark_id, added_by, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM rating_pool WHERE doc_id = ? AND page_num = ? AND remark_id = ?",
            (doc_id, page_num, remark_id),
        ).fetchone()
    return _pool_row_to_dict(row)


def pool_remove(doc_id: str, page_num: str, remark_id: str) -> bool:
    """Убрать из пула. False — его там и не было.

    Уже поставленные ответы не трогаются: убрать вопрос из раздачи и стереть
    полученные ответы — разные действия.
    """
    conn = get_connection()
    with _lock:
        cur = conn.execute(
            "DELETE FROM rating_pool WHERE doc_id = ? AND page_num = ? AND remark_id = ?",
            (doc_id, page_num, remark_id),
        )
        conn.commit()
    return cur.rowcount > 0


def pool_contains(doc_id: str, page_num: str, remark_id: str) -> bool:
    conn = get_connection()
    with _lock:
        row = conn.execute(
            "SELECT 1 FROM rating_pool WHERE doc_id = ? AND page_num = ? AND remark_id = ?",
            (doc_id, page_num, remark_id),
        ).fetchone()
    return row is not None


def pool_list(doc_id: Optional[str] = None, limit: int = 200,
              offset: int = 0) -> Dict[str, Any]:
    """Пул с текстом замечания и числом полученных ответов — это список
    вопросов, и по одним идентификаторам он нечитаем."""
    conn = get_connection()
    where = "WHERE p.doc_id = ?" if doc_id else ""
    params: List[Any] = [doc_id] if doc_id else []
    with _lock:
        total = conn.execute(
            f"SELECT COUNT(*) FROM rating_pool p {where}", tuple(params)
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT p.*, a.text AS text, a.status AS status, a.kind AS kind,
                   (SELECT COUNT(DISTINCT s.respondent_id) FROM survey_answers s
                     WHERE s.doc_id = p.doc_id AND s.page_num = p.page_num
                       AND s.remark_id = p.remark_id) AS answers
            FROM rating_pool p
            LEFT JOIN remarks a
              ON a.doc_id = p.doc_id AND a.page_num = p.page_num
             AND a.remark_id = p.remark_id
            {where}
            ORDER BY p.doc_id, p.page_num, p.remark_id
            LIMIT ? OFFSET ?
            """,
            tuple(params) + (limit, offset),
        ).fetchall()
    return {
        "items": [_pool_row_to_dict(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def pool_pick(respondent_id: int, limit: int = SURVEY_BATCH_SIZE) -> Dict[str, Any]:
    """Случайные замечания из пула, которых этот псевдоним ещё не оценивал.

    Считается по псевдониму, а не по заходу: вернувшемуся человеку раздавать
    заново то, что он уже оценил, незачем (см. start_survey_session).

    Случайность берётся у SQLite (`ORDER BY RANDOM()`): пул — сотни строк, а не
    миллионы, и выбирать иначе значило бы вычитывать его целиком ради десяти
    элементов. Выдаются только идентификаторы: текст замечания опросник берёт
    с обычной читательской страницы (`?only=`), и второго пути к тексту
    заводить не нужно.
    """
    conn = get_connection()
    with _lock:
        remaining = conn.execute(
            """
            SELECT COUNT(*) FROM rating_pool p
            WHERE NOT EXISTS (
              SELECT 1 FROM survey_answers s
              WHERE s.respondent_id = ? AND s.doc_id = p.doc_id
                AND s.page_num = p.page_num AND s.remark_id = p.remark_id
            )
            """,
            (respondent_id,),
        ).fetchone()[0]
        rows = conn.execute(
            """
            SELECT p.doc_id, p.page_num, p.remark_id FROM rating_pool p
            WHERE NOT EXISTS (
              SELECT 1 FROM survey_answers s
              WHERE s.respondent_id = ? AND s.doc_id = p.doc_id
                AND s.page_num = p.page_num AND s.remark_id = p.remark_id
            )
            ORDER BY RANDOM()
            LIMIT ?
            """,
            (respondent_id, limit),
        ).fetchall()
    return {
        "items": [{"docId": r["doc_id"], "pageNum": r["page_num"],
                   "remarkId": r["remark_id"]} for r in rows],
        "remaining": remaining,
    }


# --- респонденты ----------------------------------------------------------


def _hash_survey_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def start_survey_session(pseudonym: str) -> Dict[str, Any]:
    """Начать заход: назваться псевдонимом и получить токен сессии.

    Совпадение псевдонимов теперь значимо: одно имя — один респондент, и
    вернувшийся под ним человек продолжает свой опрос, а не начинает заново
    (до 2026-09-01 каждый заход заводил своего респондента, и уже оценённое
    раздавали повторно). Цена решения названа в docs/anonymity-model.md: имя
    без секрета, и назваться чужим псевдонимом может кто угодно.

    Возвращает и `returning` — были ли у этого имени заходы раньше: опроснику
    этого хватает, чтобы сказать «продолжаем», а не «начинаем».
    """
    name = normalize_pseudonym(pseudonym)
    token = secrets.token_hex(32)
    conn = get_connection()
    now = _now_iso()
    with _lock:
        row = conn.execute(
            "SELECT id FROM survey_respondents WHERE pseudonym = ?", (name,)
        ).fetchone()
        if row is None:
            respondent_id = conn.execute(
                "INSERT INTO survey_respondents (pseudonym, created_at, last_seen_at) "
                "VALUES (?, ?, ?)",
                (name, now, now),
            ).lastrowid
            returning = False
        else:
            respondent_id = row["id"]
            conn.execute("UPDATE survey_respondents SET last_seen_at = ? WHERE id = ?",
                         (now, respondent_id))
            returning = True
        session_id = conn.execute(
            "INSERT INTO survey_sessions (respondent_id, token_hash, created_at, last_seen_at) "
            "VALUES (?, ?, ?, ?)",
            (respondent_id, _hash_survey_token(token), now, now),
        ).lastrowid
        conn.commit()
    return {
        "respondentId": respondent_id,
        "sessionId": session_id,
        "pseudonym": name,
        "author": survey_author(name),
        "token": token,
        "createdAt": now,
        "returning": returning,
    }


def get_session_by_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Сессия по токену вместе с её псевдонимом. Отметка last_seen_at
    обновляется здесь же — и у сессии, и у респондента: другого места, где
    видно, что человек ещё отвечает, нет."""
    if not token:
        return None
    conn = get_connection()
    now = _now_iso()
    with _lock:
        row = conn.execute(
            """
            SELECT s.id AS session_id, s.respondent_id, s.created_at,
                   r.pseudonym AS pseudonym
            FROM survey_sessions s
            JOIN survey_respondents r ON r.id = s.respondent_id
            WHERE s.token_hash = ?
            """,
            (_hash_survey_token(token),),
        ).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE survey_sessions SET last_seen_at = ? WHERE id = ?",
                     (now, row["session_id"]))
        conn.execute("UPDATE survey_respondents SET last_seen_at = ? WHERE id = ?",
                     (now, row["respondent_id"]))
        conn.commit()
    return {
        "sessionId": row["session_id"],
        "respondentId": row["respondent_id"],
        "pseudonym": row["pseudonym"],
        "author": survey_author(row["pseudonym"]),
        "createdAt": row["created_at"],
    }


def list_respondents(limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    """Псевдонимы со счётчиками — админский список «кто отвечал».

    Токен не отдаётся: его в базе и нет, только хеш.
    """
    conn = get_connection()
    with _lock:
        total = conn.execute("SELECT COUNT(*) FROM survey_respondents").fetchone()[0]
        rows = conn.execute(
            """
            SELECT r.id, r.pseudonym, r.created_at, r.last_seen_at,
                   (SELECT COUNT(*) FROM survey_sessions x WHERE x.respondent_id = r.id)
                     AS sessions,
                   (SELECT COUNT(*) FROM survey_answers y WHERE y.respondent_id = r.id)
                     AS answers,
                   (SELECT COUNT(DISTINCT y.doc_id || '/' || y.page_num || '/' || y.remark_id)
                      FROM survey_answers y WHERE y.respondent_id = r.id) AS remarks
            FROM survey_respondents r
            ORDER BY COALESCE(r.last_seen_at, r.created_at) DESC, r.id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
    return {
        "items": [
            {
                "id": row["id"],
                "pseudonym": row["pseudonym"],
                "author": survey_author(row["pseudonym"]),
                "createdAt": row["created_at"],
                "lastSeenAt": row["last_seen_at"],
                "sessions": row["sessions"],
                "answers": row["answers"],
                "remarks": row["remarks"],
            }
            for row in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def list_survey_sessions(respondent_id: int) -> List[Dict[str, Any]]:
    """Заходы одного псевдонима, свежие сверху."""
    conn = get_connection()
    with _lock:
        rows = conn.execute(
            """
            SELECT s.id, s.created_at, s.last_seen_at,
                   (SELECT COUNT(*) FROM survey_answers y WHERE y.session_id = s.id)
                     AS answers,
                   (SELECT COUNT(DISTINCT y.doc_id || '/' || y.page_num || '/' || y.remark_id)
                      FROM survey_answers y WHERE y.session_id = s.id) AS remarks
            FROM survey_sessions s
            WHERE s.respondent_id = ?
            ORDER BY s.id DESC
            """,
            (respondent_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "createdAt": row["created_at"],
            "lastSeenAt": row["last_seen_at"],
            "answers": row["answers"],
            "remarks": row["remarks"],
        }
        for row in rows
    ]


def delete_survey_session(session_id: int) -> Optional[Dict[str, Any]]:
    """Стереть один заход опроса вместе с его ответами. Необратимо.

    Имя с суффиксом `survey_`, потому что `delete_session` в этом модуле уже
    занято сессией участника круга — это разные субъекты и разные таблицы.

    Псевдоним остаётся: у него могли быть и другие заходы, а пустой респондент
    — это просто имя, под которым больше ничего не сказано.

    Связанные строки вычищаются явно: `PRAGMA foreign_keys` на рабочем
    соединении не включён (см. get_connection), каскада не будет. Ревизией
    это не является — ответы опроса ревизиями замечания не были и не станут.
    """
    conn = get_connection()
    with _lock:
        row = conn.execute("SELECT respondent_id FROM survey_sessions WHERE id = ?",
                           (session_id,)).fetchone()
        if row is None:
            return None
        answers = conn.execute("DELETE FROM survey_answers WHERE session_id = ?",
                               (session_id,)).rowcount
        conn.execute("DELETE FROM survey_sessions WHERE id = ?", (session_id,))
        conn.commit()
    return {"answers": answers, "sessions": 1, "respondentId": row["respondent_id"]}


def delete_respondent(respondent_id: int) -> Optional[Dict[str, Any]]:
    """Стереть псевдоним целиком: все его заходы и все ответы. Необратимо."""
    conn = get_connection()
    with _lock:
        row = conn.execute("SELECT pseudonym FROM survey_respondents WHERE id = ?",
                           (respondent_id,)).fetchone()
        if row is None:
            return None
        answers = conn.execute("DELETE FROM survey_answers WHERE respondent_id = ?",
                               (respondent_id,)).rowcount
        sessions = conn.execute("DELETE FROM survey_sessions WHERE respondent_id = ?",
                                (respondent_id,)).rowcount
        conn.execute("DELETE FROM survey_respondents WHERE id = ?", (respondent_id,))
        conn.commit()
    return {"answers": answers, "sessions": sessions, "pseudonym": row["pseudonym"]}


# --- ответы ---------------------------------------------------------------


def set_survey_answer(respondent_id: int, session_id: int, doc_id: str, page_num: str,
                      remark_id: str, question: str, value: Optional[int] = None,
                      text: Optional[str] = None) -> None:
    """Ответ на один вопрос: либо число (шкала), либо текст (открытый вопрос).
    Значения нормализует вызывающий (rating_scales), сюда приходят уже
    проверенные.

    Ключ посессионный: повтор внутри одного захода перезаписывает — человек
    передумал, и это исправление, а не второй голос. Другой заход того же
    псевдонима оставит отдельную строку; в сводке (survey_results) числовой
    голос всё равно сведётся к одному, а тексты показываются все.
    """
    if (value is None) == (text is None):
        raise ValueError("survey answer must carry exactly one of value/text")
    conn = get_connection()
    now = _now_iso()
    with _lock:
        conn.execute(
            """
            INSERT INTO survey_answers
              (respondent_id, session_id, doc_id, page_num, remark_id, question,
               value, text, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, doc_id, page_num, remark_id, question) DO UPDATE SET
              value = excluded.value,
              text = excluded.text,
              updated_at = excluded.updated_at
            """,
            (respondent_id, session_id, doc_id, page_num, remark_id, question,
             value, text, now, now),
        )
        conn.commit()


def delete_survey_answer(session_id: int, doc_id: str, page_num: str,
                         remark_id: str, question: str) -> None:
    """Стереть ответ на один вопрос. Нужно открытым вопросам: пустое поле
    означает «ответа нет», а строка без числа и без текста запрещена CHECK.
    По заходу, а не по псевдониму: чужой заход человек не переписывает."""
    conn = get_connection()
    with _lock:
        conn.execute(
            "DELETE FROM survey_answers WHERE session_id = ? AND doc_id = ? "
            "AND page_num = ? AND remark_id = ? AND question = ?",
            (session_id, doc_id, page_num, remark_id, question),
        )
        conn.commit()


def list_survey_answers(doc_id: str, page_num: str, remark_id: str) -> List[Dict[str, Any]]:
    """Ответы по одному замечанию — для ленты (timeline). Подпись собирается
    на чтении, префикс в базе не лежит.

    Отдаются все строки, включая повторы одного псевдонима из разных заходов:
    лента показывает события, а не сводку. Сводит голоса survey_results.
    `value` и `text` взаимоисключающи — какой из них заполнен, решает вопрос."""
    conn = get_connection()
    with _lock:
        rows = conn.execute(
            """
            SELECT s.*, r.pseudonym AS pseudonym
            FROM survey_answers s
            LEFT JOIN survey_respondents r ON r.id = s.respondent_id
            WHERE s.doc_id = ? AND s.page_num = ? AND s.remark_id = ?
            ORDER BY s.id
            """,
            (doc_id, page_num, remark_id),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "respondentId": row["respondent_id"],
            "sessionId": row["session_id"],
            "author": survey_author(row["pseudonym"]) if row["pseudonym"] else None,
            "question": row["question"],
            "value": row["value"],
            "text": row["text"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]


def _survey_comments_batch(
    conn: sqlite3.Connection,
    keys: List[Tuple[str, str, str]],
) -> Dict[Tuple[str, str, str], List[Dict[str, Any]]]:
    """Тексты открытых ответов по адресам текущей страницы результатов — одним
    запросом (форма как у `_pool_state_batch`: составной ключ склеен через
    `\n`, который не проходит во входные регекспы docId/page/id).

    Отдаются все, а не последний по псевдониму: текст не усредняется, и второй
    ответ того же человека — не поправка к первому, а вторая мысль. Повтор при
    этом редок — pool_pick не раздаёт уже отвеченное."""
    if not keys:
        return {}
    open_questions = rating_scales.open_names()
    if not open_questions:
        return {}
    joined = ["\n".join(k) for k in keys]
    key_marks = ",".join("?" for _ in joined)
    question_marks = ",".join("?" for _ in open_questions)
    rows = conn.execute(
        f"""
        SELECT s.doc_id, s.page_num, s.remark_id, s.question, s.text, s.created_at,
               r.pseudonym AS pseudonym
        FROM survey_answers s
        LEFT JOIN survey_respondents r ON r.id = s.respondent_id
        WHERE s.question IN ({question_marks}) AND s.text IS NOT NULL
          AND s.doc_id || char(10) || s.page_num || char(10) || s.remark_id
              IN ({key_marks})
        ORDER BY s.id
        """,
        tuple(open_questions) + tuple(joined),
    ).fetchall()
    out: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(
            (row["doc_id"], row["page_num"], row["remark_id"]), []
        ).append({
            "author": survey_author(row["pseudonym"]) if row["pseudonym"] else None,
            "question": row["question"],
            "text": row["text"],
            "createdAt": row["created_at"],
        })
    return out


def survey_results(doc_id: Optional[str] = None, limit: int = 100,
                   offset: int = 0) -> Dict[str, Any]:
    """Сводка ответов по замечаниям — таблица результатов опроса.

    Считается по `survey_answers`, а не по пулу: замечание могли убрать из
    раздачи, но полученные ответы от этого никуда не делись и в отчёте остаются.
    Шкалы меры сводятся средним, «можно ли публиковать» — двумя счётчиками:
    среднее по вопросу «да или нет» ничего не сообщает.

    Числовой голос сводится к псевдониму, а не к строке: ключ в
    `survey_answers` посессионный, и один человек, дважды добравшийся до
    замечания из разных заходов, оставил две строки. В сводку идёт только
    последняя из них — иначе повтор двоил бы голос. Открытые ответы, наоборот,
    показываются все (`_survey_comments_batch`): текст не усредняется, и вторая
    мысль — не поправка к первой.
    """
    conn = get_connection()
    where = "WHERE s.doc_id = ?" if doc_id else ""
    params: List[Any] = [doc_id] if doc_id else []
    with _lock:
        # Тот же отбор, что и у строк ниже (только числовые ответы) — иначе
        # замечание с одним лишь текстом попадало бы в счётчик, но не в
        # страницу, и постраничная выдача разъезжалась бы. Через API такое
        # замечание и не завести: текст без единой оценки отвергается (400).
        total = conn.execute(
            f"SELECT COUNT(*) FROM (SELECT 1 FROM survey_answers s {where} "
            f"{'AND' if where else 'WHERE'} s.value IS NOT NULL "
            f"GROUP BY s.doc_id, s.page_num, s.remark_id)",
            tuple(params),
        ).fetchone()[0]
        # `answered` — только числовые ответы: открытые в средние не идут и
        # `raters` не поднимают (человек мог написать текст, не ответив ни по
        # одной шкале, — считать его «оценившим» было бы неверно).
        rows = conn.execute(
            f"""
            WITH answered AS (
              SELECT * FROM survey_answers WHERE value IS NOT NULL
            ), latest AS (
              SELECT * FROM answered s
              WHERE s.id = (
                SELECT s2.id FROM answered s2
                WHERE s2.respondent_id = s.respondent_id AND s2.doc_id = s.doc_id
                  AND s2.page_num = s.page_num AND s2.remark_id = s.remark_id
                  AND s2.question = s.question
                ORDER BY s2.updated_at DESC, s2.id DESC LIMIT 1
              )
            )
            SELECT s.doc_id, s.page_num, s.remark_id,
                   COUNT(DISTINCT s.respondent_id) AS raters,
                   SUM(CASE WHEN s.question = 'interest' THEN 1 ELSE 0 END) AS interest_n,
                   AVG(CASE WHEN s.question = 'interest' THEN s.value END) AS interest_avg,
                   SUM(CASE WHEN s.question = 'importance' THEN 1 ELSE 0 END) AS importance_n,
                   AVG(CASE WHEN s.question = 'importance' THEN s.value END) AS importance_avg,
                   SUM(CASE WHEN s.question = 'admissibility' AND s.value = 2 THEN 1 ELSE 0 END) AS yes_n,
                   SUM(CASE WHEN s.question = 'admissibility' AND s.value = 1 THEN 1 ELSE 0 END) AS no_n,
                   MAX(s.updated_at) AS updated_at,
                   a.text AS text, a.status AS status
            FROM latest s
            LEFT JOIN remarks a
              ON a.doc_id = s.doc_id AND a.page_num = s.page_num
             AND a.remark_id = s.remark_id
            {where}
            GROUP BY s.doc_id, s.page_num, s.remark_id
            ORDER BY s.doc_id, s.page_num, s.remark_id
            LIMIT ? OFFSET ?
            """,
            tuple(params) + (limit, offset),
        ).fetchall()
        comments = _survey_comments_batch(
            conn,
            [(row["doc_id"], row["page_num"], row["remark_id"]) for row in rows],
        )
    items = []
    for row in rows:
        items.append({
            "docId": row["doc_id"],
            "pageNum": row["page_num"],
            "remarkId": row["remark_id"],
            "text": row["text"],
            "status": row["status"],
            "raters": row["raters"],
            "updatedAt": row["updated_at"],
            "interest": {
                "count": row["interest_n"],
                "average": round(row["interest_avg"], 1) if row["interest_avg"] is not None else None,
            },
            "importance": {
                "count": row["importance_n"],
                "average": round(row["importance_avg"], 1) if row["importance_avg"] is not None else None,
            },
            "admissibility": {"yes": row["yes_n"], "no": row["no_n"]},
        })
        thread = comments.get((row["doc_id"], row["page_num"], row["remark_id"]), [])
        items[-1]["commentsN"] = len(thread)
        items[-1]["comments"] = thread
    return {"items": items, "total": total, "limit": limit, "offset": offset}
