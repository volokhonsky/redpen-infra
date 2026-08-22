#!/usr/bin/env python3
"""Проставить категорию существующим аннотациям.

Категория — своё поле, ровно одно на аннотацию, по умолчанию `other`
(«Прочее»). Все аннотации, заведённые до появления поля, лежат с этим
дефолтом; здесь они получают настоящее значение.

Два источника решений:

  --from-file FILE   решения классифицирующего агента: JSON вида
                     [{"docId": "...", "pageNum": "006", "annId": "ann-p006-1",
                       "category": "omission", "why": "..."}]
                     Это основной путь — см. docs/category-agent-prompt.md.

  --from-tags        грубая догадка по имеющимся тегам (таблица приоритетов
                     в scripts/annotation_categories.py). Годится как стартовое
                     приближение или как база для сравнения с решениями агента,
                     но не как окончательная разметка: у 337 аннотаций главный
                     тег — `framing`, который сам по себе ничего не значит.

По умолчанию скрипт НИЧЕГО не пишет — печатает отчёт. Запись включается
`--apply`. Как и у backfill_tags.py, это осознанное умолчание: скрипт ходит
в боевую БД.

Примеры:
    python3 backfill_categories.py --from-tags --doc medinsky11klass
    python3 backfill_categories.py --from-file decisions.json --apply
    python3 backfill_categories.py --from-tags --only-default --apply
"""

import argparse
import collections
import json
import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import annotation_categories as ac  # noqa: E402
import db  # noqa: E402
import publisher  # noqa: E402


def _load_rows(conn: sqlite3.Connection, doc_id: Optional[str]) -> List[sqlite3.Row]:
    sql = ("SELECT rowid_pk, doc_id, page_num, ann_id, category, category_source "
           "FROM annotations")
    params: List[Any] = []
    if doc_id:
        sql += " WHERE doc_id = ?"
        params.append(doc_id)
    sql += " ORDER BY doc_id, page_num, ann_id"
    return list(conn.execute(sql, params))


def _decisions_from_tags(conn: sqlite3.Connection, rows: List[sqlite3.Row]) -> Dict[int, str]:
    tags_by_pk: Dict[int, List[str]] = collections.defaultdict(list)
    for row in conn.execute(
        "SELECT annotation_pk, tag FROM annotation_tags ORDER BY annotation_pk, tag"
    ):
        tags_by_pk[row["annotation_pk"]].append(row["tag"])
    return {
        row["rowid_pk"]: ac.category_for_tags(tags_by_pk.get(row["rowid_pk"], []))
        for row in rows
    }


def _decisions_from_file(path: str, rows: List[sqlite3.Row]) -> Dict[int, str]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise SystemExit(f"{path}: ожидался JSON-массив решений")

    by_key = {(r["doc_id"], r["page_num"], r["ann_id"]): r["rowid_pk"] for r in rows}
    decisions: Dict[int, str] = {}
    missing: List[str] = []
    for item in payload:
        key = (item.get("docId"), str(item.get("pageNum")), item.get("annId"))
        pk = by_key.get(key)
        if pk is None:
            missing.append("/".join(str(part) for part in key))
            continue
        decisions[pk] = ac.normalize_category(item.get("category"))
    if missing:
        print(f"[!] не найдено в БД: {len(missing)} (первые пять: {missing[:5]})")
    return decisions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--from-file", help="JSON с решениями классифицирующего агента")
    source.add_argument("--from-tags", action="store_true",
                        help="грубая догадка по тегам (переходная мера)")
    parser.add_argument("--doc", help="ограничиться одним docId")
    parser.add_argument("--only-default", action="store_true",
                        help="трогать только неразобранные (category_source='default'); "
                             "решения человека и агента не перезаписывать")
    parser.add_argument("--apply", action="store_true",
                        help="записать в БД (по умолчанию — только отчёт)")
    parser.add_argument("--no-publish", action="store_true",
                        help="не перерисовывать статику после записи")
    args = parser.parse_args()

    db.init_db()
    conn = db.get_connection()
    rows = _load_rows(conn, args.doc)
    if not rows:
        print("аннотаций не найдено")
        return 0

    if args.from_tags:
        decisions = _decisions_from_tags(conn, rows)
    else:
        decisions = _decisions_from_file(args.from_file, rows)

    changes: List[sqlite3.Row] = []
    stats = collections.Counter()
    for row in rows:
        new = decisions.get(row["rowid_pk"])
        if new is None:
            stats["без решения"] += 1
            continue
        current = row["category"] or ac.DEFAULT_CATEGORY
        # Смотрим на источник, а не на значение: сознательно выбранное человеком
        # «Прочее» неотличимо по значению от неразобранного, и раньше этот флаг
        # такое решение затирал.
        if args.only_default and row["category_source"] != db.DEFAULT_CATEGORY_SOURCE:
            stats["пропущено (уже размечено)"] += 1
            continue
        if new == current:
            # Решение совпало со значением. Если его уже кто-то принимал —
            # писать нечего. Если источник всё ещё «никто не смотрел», записать
            # надо: осознанно выбранное «Прочее» ровно тем и отличается от
            # неразобранного, что кто-то его выбрал.
            if row["category_source"] != db.DEFAULT_CATEGORY_SOURCE:
                stats["без изменений"] += 1
                continue
            changes.append((row, new))
            stats[f"{current}: подтверждено"] += 1
            continue
        changes.append((row, new))
        stats[f"{current} → {new}"] += 1

    print(f"аннотаций: {len(rows)}, к изменению: {len(changes)}")
    for key, count in stats.most_common():
        print(f"  {key}: {count}")

    after = collections.Counter()
    changed_pks = {row["rowid_pk"] for row, _ in changes}
    for row in rows:
        if row["rowid_pk"] in changed_pks:
            after[decisions[row["rowid_pk"]]] += 1
        else:
            after[row["category"] or ac.DEFAULT_CATEGORY] += 1
    print("\nстанет:")
    for slug in list(ac.PRECEDENCE) + [ac.OTHER]:
        count = after.get(slug, 0)
        share = count / len(rows) * 100
        print(f"  {ac.CATEGORY_TITLES[slug]:22} {count:5}  {share:5.1f}%")

    if not args.apply:
        print("\n[dry-run] ничего не записано; повторите с --apply")
        return 0

    # Откуда взялось решение — служебное поле, в статику не попадает; по нему
    # очередь приёмки отличает догадку по тегам от разбора агента.
    source = "tags-backfill" if args.from_tags else "agent"
    pages = set()
    with db._lock:  # noqa: SLF001 — тот же приём, что в backfill_tags.py
        for row, new in changes:
            conn.execute(
                "UPDATE annotations SET category = ?, category_source = ? "
                "WHERE rowid_pk = ?",
                (new, source, row["rowid_pk"]),
            )
            pages.add((row["doc_id"], row["page_num"]))
        conn.commit()
    print(f"\n[+] записано изменений: {len(changes)} на {len(pages)} страницах")

    if args.no_publish:
        print("[i] статика не перерисована (--no-publish)")
        return 0
    published = sum(1 for doc_id, page_num in sorted(pages)
                    if publisher.publish_page(doc_id, page_num))
    print(f"[+] перерисовано страниц статики: {published}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
