#!/usr/bin/env python3
"""Залить параграфы документа в БД из manifest `metadata.json`.

Параграф — единица работы редактора: задание агенту-аннотатору нарезается по
параграфам, приёмка идёт параграфами, доска работ считает прогресс по ним.
Сама разметка давно есть в манифесте (`chapters[].sections[]` с диапазонами
страниц), но API не читает контент-файлы, поэтому диапазоны нужно положить
рядом с замечаниями.

Список переписывается целиком: манифест — источник правды, и параграф,
исчезнувший из него, должен исчезнуть и в БД. Аннотации на параграфы не
ссылаются (связь выводится по диапазону страниц), так что это безопасно.

По умолчанию скрипт НИЧЕГО не пишет — печатает отчёт. Запись включается
`--apply`, как в backfill_tags.py/backfill_categories.py: скрипт ходит в
боевую БД.

Примеры:
    python3 import_sections.py ../../redpen-publish/medinsky11klass/metadata.json
    python3 import_sections.py .../metadata.json --apply
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402


def sections_from_manifest(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Плоский список параграфов из chapters[].sections[].

    Разделы без номера (хронология, словарь терминов в конце книги) — тоже
    единицы работы со своим диапазоном страниц, поэтому они не выбрасываются:
    им выдаётся ключ по первой странице (`p420`).

    Глава без параграфов (например «Введение») параграфом не становится: у неё
    нет ни номера, ни задания агенту. Её страницы просто не принадлежат ни
    одному параграфу — как и аппарат главы между параграфами.
    """
    out: List[Dict[str, Any]] = []
    for chapter in manifest.get("chapters") or []:
        for section in chapter.get("sections") or []:
            out.append({
                "sectionId": _section_id(section),
                "chapterId": chapter.get("id"),
                "chapterTitle": chapter.get("name"),
                "title": section.get("name") or str(section["id"]),
                "pageStart": section.get("startPage"),
                "pageEnd": section.get("endPage"),
            })
    return out


def _section_id(section: Dict[str, Any]) -> str:
    """Ключ параграфа: номер из манифеста, иначе — по первой странице.

    Нумерованных § в учебнике 40, но в `sections[]` попадают и безномерные
    разделы конца книги. Ключ `p420` стабилен, пока стабилен манифест, и сразу
    читается человеком.
    """
    if "id" in section:
        return str(section["id"])
    start = section.get("startPage")
    if start is None:
        raise SystemExit(f"раздел без id и без startPage: {section!r}")
    return f"p{start}"


def _doc_id(manifest: Dict[str, Any], path: str) -> str:
    """docId замечаний — имя каталога документа, а не поле `id` манифеста.

    В `metadata.json` лежит `"id": "med3"`, тогда как замечания хранятся под
    `medinsky11klass`. Каталог — то, что реально совпадает с doc_id в БД.
    """
    return os.path.basename(os.path.dirname(os.path.abspath(path)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest", help="путь к <doc>/metadata.json")
    parser.add_argument("--doc", help="переопределить docId (по умолчанию — имя каталога)")
    parser.add_argument("--apply", action="store_true",
                        help="записать в БД (по умолчанию — только отчёт)")
    args = parser.parse_args()

    with open(args.manifest, encoding="utf-8") as handle:
        manifest = json.load(handle)

    doc_id = args.doc or _doc_id(manifest, args.manifest)
    sections = sections_from_manifest(manifest)
    if not sections:
        print(f"{args.manifest}: параграфов не найдено (chapters[].sections[] пуст)")
        return 1

    print(f"документ: {doc_id}")
    print(f"параграфов: {len(sections)}")
    gaps = _page_gaps(sections)
    for item in sections:
        print(f"  §{item['sectionId']:<4} стр. {item['pageStart']}–{item['pageEnd']}  {item['title'][:60]}")
    if gaps:
        # Эти страницы конвейером не покрываются: аппарат главы, шмуцтитулы.
        # Молчать о них нельзя — именно так теряются стр. 269–277.
        print("\nстраницы вне параграфов (в работу не попадают):")
        for start, end in gaps:
            print(f"  {start}–{end}" if start != end else f"  {start}")

    if not args.apply:
        print("\n[dry-run] ничего не записано; повторите с --apply")
        return 0

    db.init_db()
    written = db.replace_sections(doc_id, sections)
    print(f"\n[+] записано параграфов: {written}")
    return 0


def _page_gaps(sections: List[Dict[str, Any]]) -> List[tuple]:
    """Разрывы между соседними параграфами — страницы, не покрытые ни одним."""
    gaps = []
    ordered = [s for s in sections if s["pageStart"] and s["pageEnd"]]
    for prev, nxt in zip(ordered, ordered[1:]):
        if nxt["pageStart"] > prev["pageEnd"] + 1:
            gaps.append((prev["pageEnd"] + 1, nxt["pageStart"] - 1))
    return gaps


if __name__ == "__main__":
    raise SystemExit(main())
