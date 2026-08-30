"""
CLI: заполнить `remark_history.changes` у ревизий, записанных до её появления.

Usage:
    python backfill_history_changes.py [--doc <docId>] [--apply]

Состав изменения (какие именно поля правка затронула) с 2026-08-30 вычисляется
на записи, в `db._insert_history`. У ревизий, записанных раньше, колонка пуста.
Этот скрипт проходит по цепочкам `(doc_id, page_num, remark_id)` в порядке `id`
— порядок записи надёжнее `created_at`, у которого огрублена точность, а у
импорта метки и вовсе одинаковы в пределах пакета — сравнивает соседние снимки
и заполняет `changes` там, где сейчас NULL.

Скрипт вынесен из старта API намеренно: на проде тысячи ревизий, и разовая
операция не должна удлинять запуск. Идемпотентен: уже заполненные строки не
трогает. Ничего не публикует — статика от журнала ревизий не зависит.

Отчёт без записи, пока не передан --apply.
"""

import argparse
import collections
import json
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import remark_actions  # noqa: E402


def _load_snapshot(raw: Any) -> Optional[Dict[str, Any]]:
    try:
        snapshot = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(snapshot, dict):
        return None
    return db.normalize_snapshot(snapshot)


def compute(doc_id: Optional[str] = None) -> List[tuple]:
    """[(history_id, changes | None), ...] для строк с пустой `changes`.

    `None` во втором элементе означает «вычислить не удалось» (нечитаемый снимок
    у самой ревизии или у её предшественницы): такие строки остаются пустыми,
    потому что догадка в журнале аудита хуже пропуска.
    """
    conn = db.get_connection()
    sql = ("SELECT id, doc_id, page_num, remark_id, action, snapshot, changes "
           "FROM remark_history")
    params: List[Any] = []
    if doc_id:
        sql += " WHERE doc_id = ?"
        params.append(doc_id)
    sql += " ORDER BY doc_id, page_num, remark_id, id"
    with db._lock:  # noqa: SLF001 — тот же приём, что в backfill_tags.py
        rows = conn.execute(sql, params).fetchall()

    out = []
    prev_key = None
    prev_snapshot: Optional[Dict[str, Any]] = None
    prev_readable = False
    for row in rows:
        key = (row["doc_id"], row["page_num"], row["remark_id"])
        if key != prev_key:
            prev_key = key
            prev_snapshot = None
            prev_readable = True  # начало цепочки: предшественницы нет, и это норма
        snapshot = _load_snapshot(row["snapshot"])
        if row["changes"] is None:
            if snapshot is None or not prev_readable:
                out.append((row["id"], None))
            else:
                out.append((row["id"], remark_actions.with_provenance(
                    row["action"], remark_actions.diff_snapshots(prev_snapshot, snapshot)
                )))
        prev_snapshot = snapshot
        prev_readable = snapshot is not None
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc", dest="doc_id", default=None,
                        help="ограничить одной книгой (по умолчанию — все)")
    parser.add_argument("--apply", action="store_true",
                        help="записать (без флага — только отчёт)")
    args = parser.parse_args(argv)

    db.init_db()
    pending = compute(args.doc_id)
    if not pending:
        print("нечего заполнять: у всех ревизий состав изменения уже вычислен")
        return 0

    stats = collections.Counter()
    unresolved = 0
    for _, changes in pending:
        if changes is None:
            unresolved += 1
            continue
        if not changes:
            stats["без изменений"] += 1
            continue
        for token in changes:
            stats[token] += 1

    print(f"ревизий без состава изменения: {len(pending)}")
    print(f"  вычислено: {len(pending) - unresolved}")
    if unresolved:
        print(f"  не удалось (нечитаемый снимок): {unresolved}")
    print("\nтокены действий (ревизия может дать несколько):")
    for token, count in stats.most_common():
        print(f"  {token:12} {count:6}")

    if not args.apply:
        print("\n[dry-run] ничего не записано; повторите с --apply")
        return 0

    conn = db.get_connection()
    written = 0
    with db._lock:  # noqa: SLF001
        for hist_id, changes in pending:
            if changes is None:
                continue
            conn.execute(
                "UPDATE remark_history SET changes = ? WHERE id = ? AND changes IS NULL",
                (json.dumps(changes, ensure_ascii=False), hist_id),
            )
            written += 1
        conn.commit()
    print(f"\n[+] заполнено ревизий: {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
