"""
CLI: свести оценки по шкале «допустимость» к двум значениям.

Usage:
    python migrate_admissibility.py [--apply]

До 2026-08-31 «допустимость» была пятибалльной, как и две другие шкалы. Затем
она стала вопросом о решении — «можно ли публиковать в текущем виде», да или
нет, — и её диапазон сузился до 1..2 (`rating_scales.SCALES`). Оценки,
поставленные по старой шкале, остаются в базе как есть: значения 3..5 не
показываются ни одной кнопкой и в среднее входят с чужим весом.

Правило сведения: 1..2 → 1 («нет»), 3..5 → 2 («да»). Середина отнесена к «да»
сознательно — тройка по прежней шкале означала «предъявить можно, формулировку
бы поправить», а не «предъявлять нельзя».

На проде оценок нет: подсистема оценок за пределы локального стенда не
выкладывалась. Скрипт нужен локальным базам и тем, кто поднимет стенд из
старого дампа. Идемпотентен: значения 1 и 2 не трогает.

Отчёт без записи, пока не передан --apply.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402

SCALE = "admissibility"


def new_value(old: int) -> int:
    return 1 if old <= 2 else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="записать изменения (без флага — только отчёт)")
    args = parser.parse_args()

    db.init_db()
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT value, COUNT(*) AS n FROM remark_ratings WHERE scale = ? GROUP BY value "
        "ORDER BY value",
        (SCALE,),
    ).fetchall()

    if not rows:
        print("оценок по шкале «допустимость» нет — сводить нечего")
        return 0

    print("текущее распределение:")
    stale = 0
    for row in rows:
        mark = ""
        if row["value"] > 2:
            stale += row["n"]
            mark = f"  → {new_value(row['value'])}"
        print(f"  {row['value']}: {row['n']:6}{mark}")
    print(f"\nвне нового диапазона: {stale}")

    if not stale:
        print("всё уже в диапазоне 1..2")
        return 0
    if not args.apply:
        print("\n[dry-run] ничего не записано; повторите с --apply")
        return 0

    with db._lock:  # noqa: SLF001
        cur = conn.execute(
            "UPDATE remark_ratings SET value = 2 WHERE scale = ? AND value > 2",
            (SCALE,),
        )
        conn.commit()
    print(f"\n[+] сведено оценок: {cur.rowcount}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
