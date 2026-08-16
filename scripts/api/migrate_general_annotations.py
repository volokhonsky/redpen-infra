#!/usr/bin/env python3
"""
Retire annType='general': convert the remaining ones to anchored annotations
and drop the ones that were never annotations at all.

Why: the "общий комментарий" panel did not work as a reading device -- with
ordinary comments on the page nobody looked at it. With the move to per-page
static addresses every comment needs an anchor on the scan, so the type goes
away entirely. What to do with each of the 16 rows is decided editorially in
docs/general-migration-map.json (coordinates taken off images_with_grid).

Safety rules, because this rewrites published content:

* dry-run by default; --apply is required to write;
* refuses to run if a row named in the map is not currently `general`
  (someone edited it in the meantime);
* refuses to run if the database still holds a `general` row the map does not
  mention (the map must be exhaustive, or the type could not be dropped from
  the API afterwards);
* status is preserved -- 8 of the 14 are drafts and must stay drafts;
* tags are left alone (upsert_annotation_db treats tags=None as "don't touch");
* every change goes through upsert_annotation_db / soft_delete_annotation, so
  annotation_history records it and the edits are revertible from the cabinet.

Deletions are soft (status='deleted'), like every other deletion in the system.

Usage:
    python migrate_general_annotations.py [--map PATH] [--apply]
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db          # noqa: E402
import publisher   # noqa: E402

DEFAULT_MAP = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "docs", "general-migration-map.json",
)


class MigrationError(RuntimeError):
    pass


def load_map(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not data.get("docId"):
        raise MigrationError(f"{path}: docId is required")
    for entry in data.get("convert", []):
        coords = entry.get("coords")
        if entry.get("annType") not in ("main", "comment"):
            raise MigrationError(f"{entry.get('annId')}: annType must be main or comment")
        if not (isinstance(coords, list) and len(coords) == 2 and all(isinstance(c, int) for c in coords)):
            raise MigrationError(f"{entry.get('annId')}: coords must be [x, y] integers")
    return data


def find_general_rows(doc_id: str) -> List[Dict[str, Any]]:
    """Every live (published or draft) general annotation in the database.

    Must not use list_page_annotations(): that returns published rows only, and
    8 of the 14 rows to convert are drafts -- they would silently go missing.
    """
    rows = db.list_annotations(doc_id=doc_id, ann_type="general", limit=10000)
    return [r for r in rows if r["status"] != "deleted"]


def check(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Validate the map against the database. Returns the general rows found."""
    doc_id = plan["docId"]
    listed = {(e["pageKey"], e["annId"]) for e in plan.get("convert", [])}
    listed |= {(e["pageKey"], e["annId"]) for e in plan.get("delete", [])}

    found = find_general_rows(doc_id)
    found_keys = {(r["pageNum"], r["annId"]) for r in found}

    missing = sorted(listed - found_keys)
    if missing:
        raise MigrationError(
            "these rows are named in the map but are not general annotations in the DB "
            f"(already migrated, deleted or edited?): {missing}"
        )

    unlisted = sorted(found_keys - listed)
    if unlisted:
        raise MigrationError(
            "the DB holds general annotations the map does not mention; the map must be "
            f"exhaustive or the type cannot be dropped afterwards: {unlisted}"
        )

    return found


def migrate(plan: Dict[str, Any], apply: bool) -> Dict[str, int]:
    doc_id = plan["docId"]
    found = check(plan)
    by_key = {(r["pageNum"], r["annId"]): r for r in found}

    touched_pages = set()
    stats = {"converted": 0, "deleted": 0}

    for entry in plan.get("convert", []):
        key = (entry["pageKey"], entry["annId"])
        current = by_key[key]
        x, y = entry["coords"]
        print(
            f"  convert {entry['annId']:<22} page {entry['pageKey']:>4}  "
            f"general -> {entry['annType']:<7} coords=[{x}, {y}]  "
            f"status={current['status']}  ({entry.get('anchor', '')[:60]})"
        )
        if apply:
            db.upsert_annotation_db(
                doc_id,
                entry["pageKey"],
                entry["annId"],
                entry["annType"],
                current["text"],
                coord_x=x,
                coord_y=y,
                # Drafts must stay drafts: upsert defaults to "published".
                status=current["status"],
                action="update",
                # tags=None leaves the tag set alone.
            )
        touched_pages.add(entry["pageKey"])
        stats["converted"] += 1

    for entry in plan.get("delete", []):
        print(f"  delete  {entry['annId']:<22} page {entry['pageKey']:>4}  ({entry.get('why', '')[:70]})")
        if apply:
            db.soft_delete_annotation(doc_id, entry["pageKey"], entry["annId"])
        touched_pages.add(entry["pageKey"])
        stats["deleted"] += 1

    if apply:
        for page in sorted(touched_pages):
            publisher.publish_page(doc_id, page)
        remaining = find_general_rows(doc_id)
        if remaining:
            raise MigrationError(f"general annotations still present after migration: {remaining}")
        print(f"\nrepublished {len(touched_pages)} pages; no general annotations remain")
    else:
        print(f"\nDRY RUN -- nothing written. {len(touched_pages)} pages would be republished.")

    return stats


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--map", default=DEFAULT_MAP, help="migration map JSON (default: docs/general-migration-map.json)")
    parser.add_argument("--apply", action="store_true", help="actually write; without it this is a dry run")
    args = parser.parse_args(argv)

    db.init_db()

    try:
        plan = load_map(args.map)
        print(f"doc={plan['docId']}  map={args.map}  mode={'APPLY' if args.apply else 'dry-run'}\n")
        stats = migrate(plan, apply=args.apply)
    except MigrationError as e:
        print(f"[migrate_general] refusing to run: {e}", file=sys.stderr)
        return 1

    print(f"converted={stats['converted']} deleted={stats['deleted']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
