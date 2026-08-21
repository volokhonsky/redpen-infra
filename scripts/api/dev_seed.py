#!/usr/bin/env python3
"""Поднять локальный стенд: наполнить БД и выдать вход без Google.

Только для локальной разработки. Вход через Google на localhost не работает
(нужен OAuth-клиент, настроенный на этот origin), а смотреть на кабинет и
редактор как-то надо. Скрипт заводит участника напрямую и печатает готовую
строку для установки cookie сессии в браузере.

Почему это не дыра в проде: скрипт не эндпоинт, а CLI. Он пишет в ту базу, на
которую указывает DB_PATH, и на боевой машине его просто не запускают. Никакого
кода в API он не добавляет.

Что делает:
  1. заливает параграфы из metadata.json (scripts/api/import_sections.py);
  2. импортирует аннотации указанных страниц из redpen-publish в БД;
  3. заводит участника с нужной ролью и печатает cookie сессии.

Пример:
    DB_PATH=/tmp/redpen-dev.db IDENTITY_PEPPER=dev \\
      python3 scripts/api/dev_seed.py --publish-dir redpen-publish --pages 6-20
"""

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import import_sections  # noqa: E402
import publisher  # noqa: E402


def _parse_pages(spec):
    """'6-20,100' -> {'006', …, '020', '100'}. Пусто -> все страницы."""
    if not spec:
        return None
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part.lstrip("-"):
            lo, hi = part.split("-", 1)
            out.update(f"{n:03d}" for n in range(int(lo), int(hi) + 1))
        else:
            out.add(f"{int(part):03d}")
    return out


def _import_page(doc_id, page_num, path):
    with open(path, encoding="utf-8") as handle:
        items = json.load(handle)
    if not isinstance(items, list):
        return 0
    count = 0
    for item in items:
        tags = [t for t in (item.get("tags") or []) if not t.startswith("cat:")]
        tags = [t for t in tags if t not in db.RESERVED_TAGS]
        db.upsert_annotation_db(
            doc_id, page_num, item["id"], item.get("annType", "comment"),
            item.get("text", ""),
            coord_x=(item.get("coords") or [None, None])[0],
            coord_y=(item.get("coords") or [None, None])[1],
            status="draft" if item.get("draft") else "published",
            action="import", tags=tags,
            category=item.get("category"),
            # Категория приехала из конвейера агента, значит ждёт приёмки.
            category_source="agent" if item.get("category") else None,
        )
        count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--publish-dir", default="redpen-publish",
                        help="каталог собранного сайта, откуда брать аннотации")
    parser.add_argument("--doc", default="medinsky11klass")
    parser.add_argument("--pages", default="6-40",
                        help="диапазоны страниц, например '6-20,100'. Пусто = все")
    parser.add_argument("--role", default="admin",
                        choices=("viewer", "editor", "reviewer", "admin"))
    parser.add_argument("--name", default="Локальный админ", help="псевдоним участника")
    args = parser.parse_args()

    db.init_db()

    manifest_path = os.path.join(args.publish_dir, args.doc, "metadata.json")
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as handle:
            sections = import_sections.sections_from_manifest(json.load(handle))
        db.replace_sections(args.doc, sections)
        print(f"[+] параграфов: {len(sections)}")
    else:
        print(f"[!] нет {manifest_path}: параграфы не залиты")

    wanted = _parse_pages(args.pages)
    total = 0
    pages = 0
    pattern = os.path.join(args.publish_dir, args.doc, "annotations", "page_*.json")
    for path in sorted(glob.glob(pattern)):
        page_num = os.path.basename(path)[len("page_"):-len(".json")]
        if wanted is not None and page_num not in wanted:
            continue
        imported = _import_page(args.doc, page_num, path)
        if imported:
            pages += 1
            total += imported
            publisher.publish_page(args.doc, page_num)
    print(f"[+] аннотаций: {total} на {pages} страницах")

    # Участник-человек заводится напрямую: приглашение здесь только мешало бы.
    conn = db.get_connection()
    with db._lock:  # noqa: SLF001
        row = conn.execute(
            "SELECT * FROM users WHERE kind = 'human' AND display_name = ?", (args.name,)
        ).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO users (role, created_at, last_login_at, kind, display_name)"
                " VALUES (?, ?, ?, 'human', ?)",
                (args.role, db._now_iso(), db._now_iso(), args.name),
            )
            user_id = cur.lastrowid
        else:
            user_id = row["id"]
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (args.role, user_id))
        conn.commit()

    session_id = db.create_session(user_id)
    print(f"\n[+] участник #{user_id} «{args.name}», роль {args.role}")
    print("\nЧтобы войти, открой кабинет и выполни в консоли браузера:")
    print(f"\n  document.cookie = 'redpen_session={session_id}; path=/'; location.reload()\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
