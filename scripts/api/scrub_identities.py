#!/usr/bin/env python3
"""Перевести существующих участников на хеши и стереть личности из БД.

До этой миграции `users` хранила `email`, `name` и `picture_url` открытым
текстом, а доступ выдавался таблицей `editor_allowlist` по email. Ежедневный
бэкап БД был, таким образом, готовым именным списком авторов — ровно тем, чего
в проекте про авторитарный режим быть не должно (docs/anonymity-model.md).

Что делает скрипт:

  1. считает `sub_hash = HMAC(IDENTITY_PEPPER, google_sub)` для каждого участника,
     у кого есть `google_sub`;
  2. затирает `google_sub`, `email`, `name`, `picture_url`;
  3. выдаёт псевдоним «Участник №N» тем, у кого его нет, — настоящий человек
     заменит его сам в кабинете;
  4. чистит `editor_allowlist`: роли давно живут в `users.role`, а таблица
     хранит только email.

Участник без `google_sub` (входивший по токену) хешу не подлежит: это агент,
его опознают по имени токена.

**Роли не пересчитываются.** Они уже проставлены в `users.role` при последнем
входе; после миграции они остаются как есть, а новые участники получают роль
из приглашения.

По умолчанию скрипт НИЧЕГО не пишет — печатает отчёт. Запись по `--apply`.
Перед боевым запуском обязателен именной бэкап (`VACUUM INTO`).

Идемпотентен: участник, у которого уже есть `sub_hash` и нет `google_sub`,
пропускается.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import db  # noqa: E402

#: Колонки, которых после миграции в БД быть не должно ни в одной строке.
IDENTITY_COLUMNS = ("google_sub", "email", "name", "picture_url")


def _existing_identity_columns(conn) -> list:
    present = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    return [c for c in IDENTITY_COLUMNS if c in present]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="записать в БД (по умолчанию — только отчёт)")
    args = parser.parse_args()

    if not config.IDENTITY_PEPPER:
        print("[!] IDENTITY_PEPPER не задан: без перца хеш вырождается в sha256 "
              "от google_sub. Задайте перец в .env.secrets и повторите.",
              file=sys.stderr)
        return 2

    db.init_db()
    conn = db.get_connection()
    columns = _existing_identity_columns(conn)
    if not columns:
        print("колонок с личностями в users нет — миграция уже проведена")
        return 0

    rows = list(conn.execute(f"SELECT id, kind, sub_hash, {', '.join(columns)} FROM users"))
    to_hash = [r for r in rows if "google_sub" in columns and r["google_sub"] and not r["sub_hash"]]
    to_clear = [r for r in rows if any(r[c] for c in columns)]

    print(f"участников: {len(rows)}")
    print(f"  получат sub_hash: {len(to_hash)}")
    print(f"  будут очищены от личных данных: {len(to_clear)}")
    allowlist = conn.execute("SELECT COUNT(*) AS n FROM editor_allowlist").fetchone()["n"]
    print(f"  записей editor_allowlist к удалению: {allowlist}")

    agents = [r for r in rows if r["kind"] == "agent"]
    if agents:
        print(f"  акторов-агентов (хешу не подлежат): {len(agents)}")

    if not args.apply:
        print("\n[dry-run] ничего не записано; повторите с --apply")
        print("[!] перед боевым запуском сделайте именной бэкап: VACUUM INTO")
        return 0

    with db._lock:  # noqa: SLF001 — тот же приём, что в остальных backfill-скриптах
        for row in to_hash:
            conn.execute("UPDATE users SET sub_hash = ? WHERE id = ?",
                         (db.hash_subject(row["google_sub"]), row["id"]))
        conn.execute(
            "UPDATE users SET " + ", ".join(f"{c} = NULL" for c in columns)
        )
        conn.execute(
            "UPDATE users SET display_name = 'Участник №' || id "
            "WHERE display_name IS NULL AND kind = 'human'"
        )
        conn.execute("DELETE FROM editor_allowlist")
        conn.commit()

    left = conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE " +
        " OR ".join(f"{c} IS NOT NULL" for c in columns)
    ).fetchone()["n"]
    print(f"\n[+] проставлено хешей: {len(to_hash)}")
    print(f"[+] строк с остатками личных данных: {left}")
    return 0 if left == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
