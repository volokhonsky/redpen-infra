"""
CLI: backfill annotation tags from the markdown drafts into the SQLite store.

Usage:
    python backfill_tags.py <md_dir> --doc <docId> [--apply] [--overwrite]

The `tags:`/`confidence:` meta fields have always been written by the
annotator agent but dropped by the md -> JSON conversion, so the ~1200
annotations already in the DB carry none. This walks <md_dir>/page_*.md,
parses each meta block with the very same parser the converter uses
(scripts/annotation_converter.py), and attaches the tags to the matching
(doc_id, page_num, ann_id) row.

Reports without writing unless --apply is given. Annotations that already have
tags are left alone unless --overwrite is given; unmatched ids are listed so a
mismatch between the md corpus and the DB is visible rather than silent.

This script never publishes -- run publish_all() (or restart the API) after.
"""

import argparse
import os
import re
import sys
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
from annotation_converter import parse_markdown_annotation  # noqa: E402

PAGE_MD_RE = re.compile(r"^page_(-?\d+)\.md$")


def iter_page_files(md_dir: str) -> List[tuple]:
    """[(page_num, path), ...] -- page_num verbatim from the filename, so
    "000"/"-01" survive. Service files (_report_*, _typical_comments) are
    skipped by the pattern."""
    if not os.path.isdir(md_dir):
        return []
    out = []
    for name in sorted(os.listdir(md_dir)):
        m = PAGE_MD_RE.match(name)
        if m:
            out.append((m.group(1), os.path.join(md_dir, name)))
    return out


def run(md_dir: str, doc_id: str, apply: bool, overwrite: bool) -> Dict[str, int]:
    totals = {"pages": 0, "annotations": 0, "tagged": 0, "skipped": 0, "unmatched": 0, "errors": 0}
    tag_counts: Dict[str, int] = {}

    for page_num, path in iter_page_files(md_dir):
        totals["pages"] += 1
        try:
            with open(path, "r", encoding="utf-8") as f:
                parsed = parse_markdown_annotation(f.read())
        except Exception as exc:
            print(f"{path}: unreadable ({exc})", file=sys.stderr)
            totals["errors"] += 1
            continue

        for ann in parsed:
            totals["annotations"] += 1
            ann_id = (ann.get("id") or "").strip()
            raw_tags = ann.get("tags") or []
            if not ann_id or not raw_tags:
                continue

            try:
                tags = db.normalize_tags(raw_tags)
            except db.TagError as exc:
                print(f"{path}: {ann_id}: {exc}", file=sys.stderr)
                totals["errors"] += 1
                continue

            existing = db.get_annotation(doc_id, page_num, ann_id)
            if existing is None:
                print(f"unmatched: {doc_id}/{page_num}/{ann_id}", file=sys.stderr)
                totals["unmatched"] += 1
                continue
            if existing["tags"] and not overwrite:
                totals["skipped"] += 1
                continue

            if apply:
                # Tags only: text/type/coords stay whatever the DB already holds,
                # which is canon. status is passed back unchanged for the same
                # reason -- upsert always writes the status column.
                db.upsert_annotation_db(
                    doc_id,
                    page_num,
                    ann_id,
                    existing["annType"],
                    existing["text"],
                    coord_x=existing["coordX"],
                    coord_y=existing["coordY"],
                    status=existing["status"],
                    author_id=existing["authorId"],
                    action="backfill-tags",
                    tags=tags,
                )
            totals["tagged"] += 1
            for tag in tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

    if tag_counts:
        print("tags:")
        for tag, n in sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {tag}: {n}")
    return totals


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("md_dir", help="Directory containing page_*.md annotation drafts")
    parser.add_argument("--doc", dest="doc_id", required=True, help="docId the markdown belongs to")
    parser.add_argument("--apply", action="store_true",
                        help="Actually write to the DB (default: report only)")
    parser.add_argument("--overwrite", action="store_true",
                        help="Replace tags on annotations that already have some")
    args = parser.parse_args(argv)

    db.init_db()
    totals = run(args.md_dir, args.doc_id, apply=args.apply, overwrite=args.overwrite)

    print(
        "{}: pages={pages} annotations={annotations} tagged={tagged} "
        "skipped={skipped} unmatched={unmatched} errors={errors}".format(
            "applied" if args.apply else "dry-run", **totals
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
