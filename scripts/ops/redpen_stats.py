#!/usr/bin/env python3
"""
Счётчики контента RedPen: что читают в разрезе параграфов и разбора.

Это **не** веб-аналитика. Посещаемость, источники, устройства, страны, визиты и
глубину просмотра считает Matomo — импорт серверных логов его штатная функция
(`scripts/matomo_export.py` готовит для него лог). Здесь остаётся ровно то, о
чём готовый инструмент знать не может, потому что это про наш контент:

  * просмотры в свёртке по параграфам — единице работы проекта;
  * **спрос против покрытия**: какие страницы читают, хотя разбора там нет;
  * открытия по `?only=` — какие замечания пересылают по прямой ссылке.

Ни адреса, ни производной от него здесь не хранится: опознанием читателя
занимается Matomo, второй копии таких данных мы не заводим
(`docs/anonymity-model.md`, раздел «Читатели сайта»).

Команды:
    ingest   дочитать свежие строки логов в базу (крон, раз в час)
    report   отчёт в консоль
    prune    удалить старые записи

Пример (в контейнере API — там и логи, и обе базы):
    python3 /app/scripts/ops/redpen_stats.py ingest
    python3 /app/scripts/ops/redpen_stats.py report --days 30
"""

import argparse
import gzip
import hashlib
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import analytics  # noqa: E402
import page_sections  # noqa: E402

DEFAULT_DB = os.getenv("STATS_DB_PATH", "/var/redpen-stats/stats.db")
DEFAULT_LOG_DIR = os.getenv("ANALYTICS_LOG_DIR", "/var/log/caddy")
DEFAULT_SITE_DIR = os.getenv("PUBLISH_DIR", "/srv/public")
DEFAULT_CONTENT_DB = os.getenv("DB_PATH", "/var/redpen-db/redpen.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS hits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  day TEXT NOT NULL,
  kind TEXT NOT NULL,
  doc_id TEXT,
  page_label TEXT,
  remark_id TEXT,
  legacy_param TEXT,
  tag_filter INTEGER NOT NULL DEFAULT 0,
  path TEXT NOT NULL,
  status INTEGER NOT NULL,
  referer_source TEXT,
  ua_class TEXT
);
CREATE INDEX IF NOT EXISTS idx_hits_day ON hits(day);
CREATE INDEX IF NOT EXISTS idx_hits_page ON hits(doc_id, page_label);
CREATE INDEX IF NOT EXISTS idx_hits_kind ON hits(kind, ts);

-- Позиция в каждом файле лога: повторный прогон не должен считать одно и то же
-- дважды. `signature` ловит подмену файла ротацией — тогда читаем с нуля.
CREATE TABLE IF NOT EXISTS ingest_state (
  source TEXT PRIMARY KEY,
  signature TEXT NOT NULL,
  offset INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);
"""


def open_db(path: str) -> sqlite3.Connection:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _rename_legacy_column(conn)
    return conn


def _rename_legacy_column(conn: sqlite3.Connection) -> None:
    """`hits.ann_id` -> `hits.remark_id` на базе, заведённой до переименования.

    CREATE TABLE IF NOT EXISTS не трогает существующую таблицу, поэтому без
    этого шага INSERT ушёл бы в несуществующую колонку. База счётчиков
    восстановима из логов, но переливать её ради имени колонки незачем."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(hits)")}
    if "ann_id" in columns and "remark_id" not in columns:
        conn.execute("ALTER TABLE hits RENAME COLUMN ann_id TO remark_id")
        conn.commit()


# --------------------------------------------------------------------------
# ingest
# --------------------------------------------------------------------------

def _open_log(path: str):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def _signature(path: str) -> str:
    # Именно первая строка, а не первые N байт: дописанная в конец строка
    # меняла бы «первые 512 байт» у короткого файла, и весь лог читался бы
    # заново — то есть уезжал бы в Matomo дважды.
    try:
        with _open_log(path) as f:
            head = f.readline()
    except OSError:
        return ""
    return hashlib.sha256(head.encode("utf-8", "replace")).hexdigest()[:16]


def log_files(log_dir: str) -> List[str]:
    """Все файлы логов каталога, от старых к свежим."""
    if not os.path.isdir(log_dir):
        return []
    names = [n for n in os.listdir(log_dir)
             if n.startswith("access") and (n.endswith(".log") or n.endswith(".gz"))]
    paths = [os.path.join(log_dir, n) for n in names]
    return sorted(paths, key=lambda p: (os.path.getmtime(p), p))


def ingest(conn: sqlite3.Connection, log_dir: str,
           own_hosts: Tuple[str, ...] = (), verbose: bool = False) -> Dict[str, int]:
    stats = {"files": 0, "lines": 0, "hits": 0, "skipped": 0}

    for path in log_files(log_dir):
        signature = _signature(path)
        if not signature:
            continue
        row = conn.execute("SELECT * FROM ingest_state WHERE source = ?",
                           (path,)).fetchone()
        offset = row["offset"] if row and row["signature"] == signature else 0
        stats["files"] += 1

        batch: List[Dict[str, Any]] = []
        # Смещение — в строках, а не в байтах: у .gz байтовая позиция
        # бессмысленна, а строк тут немного. Считается по прочитанным строкам,
        # а не по записанным: пропущенную строку второй раз читать незачем.
        consumed = offset
        with _open_log(path) as f:
            for lineno, line in enumerate(f):
                if lineno < offset:
                    continue
                consumed = lineno + 1
                stats["lines"] += 1
                parsed = analytics.parse_line(line)
                if not parsed:
                    stats["skipped"] += 1
                    continue
                hit = analytics.build_hit(parsed, own_hosts)
                if hit is None:
                    stats["skipped"] += 1
                    continue
                batch.append(hit)

        conn.executemany(
            """INSERT INTO hits (ts, day, kind, doc_id, page_label, remark_id,
                                 legacy_param, tag_filter, path, status,
                                 referer_source, ua_class)
               VALUES (:ts, :day, :kind, :doc_id, :page_label, :remark_id,
                       :legacy_param, :tag_filter, :path, :status,
                       :referer_source, :ua_class)""",
            batch,
        )
        stats["hits"] += len(batch)
        conn.execute(
            """INSERT INTO ingest_state (source, signature, offset, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(source) DO UPDATE SET
                 signature = excluded.signature,
                 offset = excluded.offset,
                 updated_at = excluded.updated_at""",
            (path, signature, consumed, datetime.now(timezone.utc).isoformat()),
        )
        if verbose:
            print(f"  {os.path.basename(path)}: +{len(batch)}")

    conn.commit()
    return stats


# --------------------------------------------------------------------------
# Покрытие из канонической базы
# --------------------------------------------------------------------------

def load_coverage(content_db: str) -> Dict[Tuple[str, str], Dict[str, int]]:
    """{(doc_id, page_num): {published, draft}} из канонической базы.

    Открывается только на чтение: аналитика к канону не прикасается.
    """
    if not os.path.exists(content_db):
        return {}
    try:
        conn = sqlite3.connect(f"file:{content_db}?mode=ro", uri=True)
        # `annotations` — имя таблицы до переименования сущности. Аналитика
        # ходит в канон снаружи и может застать его до рестарта API, который и
        # проводит миграцию; молча пустой раздел отчёта хуже лишней ветки.
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        table = "remarks" if "remarks" in tables else "annotations"
        rows = conn.execute(
            f"SELECT doc_id, page_num, status, COUNT(*) c "
            f"FROM {table} GROUP BY doc_id, page_num, status").fetchall()
        conn.close()
    except sqlite3.Error:
        return {}
    out: Dict[Tuple[str, str], Dict[str, int]] = defaultdict(
        lambda: {"published": 0, "draft": 0})
    for doc_id, page_num, status, count in rows:
        key = (doc_id, str(page_num))
        out[key][status if status in ("published", "draft") else "published"] += count
    return out


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def _table(rows: List[Tuple[Any, ...]], headers: Tuple[str, ...], limit: int = 0) -> str:
    rows = rows[:limit] if limit else rows
    if not rows:
        return "  (пусто)"
    columns = list(zip(*([headers] + [tuple(str(c) for c in r) for r in rows])))
    widths = [max(len(str(c)) for c in col) for col in columns]
    out = ["  " + "  ".join(str(h).ljust(w) for h, w in zip(headers, widths))]
    out.append("  " + "  ".join("-" * w for w in widths))
    for row in rows:
        out.append("  " + "  ".join(str(c).ljust(w) for c, w in zip(row, widths)))
    return "\n".join(out)


def report(conn: sqlite3.Connection, days: int, site_dir: str,
           content_db: str, top: int = 20) -> str:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    # Роботы — не спрос: обход всей книги подряд иначе перекрыл бы живой интерес.
    human = " AND ua_class != 'bot'"
    manifests = page_sections.ManifestCache(site_dir)
    out: List[str] = []
    add = out.append

    add(f"RedPen — спрос на контент за {days} дн. (с {since[:10]}, UTC)")
    add("Посещаемость, источники и устройства — в Matomo; здесь только контент.")
    add("")

    page_rows = conn.execute(
        f"""SELECT doc_id, page_label, COUNT(*) views
            FROM hits WHERE ts >= ? AND kind = 'page'{human}
            GROUP BY doc_id, page_label ORDER BY views DESC""", (since,)).fetchall()
    total = sum(r["views"] for r in page_rows)
    add(f"  просмотров страниц: {total}, из них уникальных страниц: {len(page_rows)}")
    add("")

    # --- параграфы
    per_section: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in page_rows:
        section = manifests.section_of(r["doc_id"], r["page_label"])
        key = (r["doc_id"], section["id"] if section else "—")
        entry = per_section.setdefault(key, {
            "name": section["name"] if section else "вне параграфов",
            "views": 0, "pages": set(),
        })
        entry["views"] += r["views"]
        entry["pages"].add(r["page_label"])
    add("== Самые читаемые параграфы ==")
    ordered = sorted(per_section.items(), key=lambda kv: -kv[1]["views"])
    add(_table([(k[0], k[1], v["name"][:58], v["views"], len(v["pages"]))
                for k, v in ordered],
               ("книга", "§", "название", "просмотры", "стр."), limit=top))
    add("")

    add("== Самые читаемые страницы ==")
    add(_table([(r["doc_id"], r["page_label"], r["views"]) for r in page_rows],
               ("книга", "стр.", "просмотры"), limit=top))
    add("")

    # --- спрос против покрытия
    coverage = load_coverage(content_db)
    add("== Спрос против покрытия (читают, а разбора нет) ==")
    if not coverage:
        add(f"  (каноническая база недоступна: {content_db})")
    else:
        rows = []
        for r in page_rows:
            key = manifests.get(r["doc_id"]).get("label_to_key", {}).get(str(r["page_label"]))
            if key is None:
                continue
            counts = coverage.get((r["doc_id"], key))
            if counts and counts["published"]:
                continue
            section = manifests.section_of(r["doc_id"], r["page_label"])
            rows.append((r["doc_id"], r["page_label"],
                         f"§{section['id']}" if section else "—",
                         r["views"], counts["draft"] if counts else 0))
        add(_table(rows, ("книга", "стр.", "§", "просмотры", "черновиков"), limit=top))
    add("")

    # --- расшаренные замечания
    add("== Замечания по прямой ссылке (?only=) ==")
    rows = conn.execute(
        f"""SELECT doc_id, page_label, remark_id, COUNT(*) n
            FROM hits WHERE ts >= ? AND remark_id IS NOT NULL{human}
            GROUP BY doc_id, page_label, remark_id ORDER BY n DESC""", (since,)).fetchall()
    add(_table([(r["doc_id"], r["page_label"], r["remark_id"], r["n"]) for r in rows],
               ("книга", "стр.", "замечание", "открытий"), limit=top))
    add("")

    # --- прочее по нашей части
    legacy = conn.execute(
        f"SELECT COUNT(*) FROM hits WHERE ts >= ? AND legacy_param IS NOT NULL{human}",
        (since,)).fetchone()[0]
    drafts = conn.execute(
        f"SELECT COUNT(*) FROM hits WHERE ts >= ? AND tag_filter = 1{human}",
        (since,)).fetchone()[0]
    downloads = conn.execute(
        f"SELECT COUNT(*) FROM hits WHERE ts >= ? AND kind = 'download'{human}",
        (since,)).fetchone()[0]
    add("== Прочее ==")
    add(f"  заходов по старым адресам (?page=/?p=): {legacy}")
    add(f"  заходов с фильтром тегов или ?showDrafts=1: {drafts}")
    add(f"  скачиваний офлайн-архива: {downloads}")
    return "\n".join(out)


def prune(conn: sqlite3.Connection, days: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    cur = conn.execute("DELETE FROM hits WHERE ts < ?", (cutoff,))
    conn.commit()
    conn.execute("VACUUM")
    return cur.rowcount


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DEFAULT_DB, help="файл счётчиков")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="дочитать логи в базу")
    p_ingest.add_argument("--log-dir", default=DEFAULT_LOG_DIR)
    p_ingest.add_argument("--host", action="append", default=[])
    p_ingest.add_argument("-v", "--verbose", action="store_true")

    p_report = sub.add_parser("report", help="отчёт в консоль")
    p_report.add_argument("--days", type=int, default=30)
    p_report.add_argument("--top", type=int, default=20)
    p_report.add_argument("--site-dir", default=DEFAULT_SITE_DIR)
    p_report.add_argument("--content-db", default=DEFAULT_CONTENT_DB)

    p_prune = sub.add_parser("prune", help="удалить записи старше N дней")
    p_prune.add_argument("--days", type=int, required=True)

    args = parser.parse_args(argv)
    conn = open_db(args.db)

    if args.command == "ingest":
        hosts = tuple(h.lower() for h in
                      (args.host or [os.getenv("DOMAIN", "medinsky.net")]))
        stats = ingest(conn, args.log_dir, hosts, args.verbose)
        print("redpen-stats ingest: файлов {files}, строк {lines}, "
              "записано {hits}, пропущено {skipped}".format(**stats))
        return 0

    if args.command == "report":
        print(report(conn, args.days, args.site_dir, args.content_db, args.top))
        return 0

    if args.command == "prune":
        print(f"redpen-stats prune: удалено строк {prune(conn, args.days)}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
