"""
CLI: import existing annotations/page_*.json files into the SQLite store
(db.py), stage 2 (docs/agent-instructions-stage-2.md, A2.3).

Usage:
    python import_annotations.py <source_dir> [--doc <docId>] [--dry-run] [--overwrite]

Walks <source_dir>/<docId>/annotations/page_*.json for every docId under
source_dir (or just the one given via --doc). Accepts both the current bare
-array format and the legacy page-object format ({"annotations": [...]})
that older versions of the API wrote to the mounted working copy.

Idempotent by default: an existing (doc_id, page_num, ann_id) row is left
alone (counted as skipped). --overwrite updates text/annType/coords instead.
Imported rows get action="import" in annotation_history with author_id=NULL.
This script never publishes -- run publish_all() (or restart the API) after.
"""

import argparse
import json
import os
import re
import secrets
import sys
from typing import Any, Dict, List, Optional, Tuple

# Общий модуль категорий лежит в scripts/, а на sys.path у контейнера только
# scripts/api (см. scripts/api/Dockerfile). Репозиторий скопирован целиком,
# поэтому каталог достаточно добавить руками.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import annotation_categories  # noqa: E402
import config
import db

PAGE_FILE_RE = re.compile(r"^page_(-?\d+)\.json$")


def _iter_doc_dirs(source_dir: str, doc_id: Optional[str]) -> List[str]:
    if doc_id:
        return [doc_id] if os.path.isdir(os.path.join(source_dir, doc_id, "annotations")) else []
    result = []
    if not os.path.isdir(source_dir):
        return result
    for name in sorted(os.listdir(source_dir)):
        if os.path.isdir(os.path.join(source_dir, name, "annotations")):
            result.append(name)
    return result


def _iter_page_files(source_dir: str, doc_id: str) -> List[Tuple[str, str]]:
    """Return [(page_num, file_path), ...], page_num taken verbatim from the
    filename (so "000"/"-01" survive as-is), sorted by filename."""
    ann_dir = os.path.join(source_dir, doc_id, "annotations")
    if not os.path.isdir(ann_dir):
        return []
    out = []
    for name in sorted(os.listdir(ann_dir)):
        m = PAGE_FILE_RE.match(name)
        if not m:
            continue
        out.append((m.group(1), os.path.join(ann_dir, name)))
    return out


def _extract_annotations(data: Any) -> List[Dict[str, Any]]:
    """Accept both the bare-array format and the legacy {"annotations": [...]}
    page-object format."""
    if isinstance(data, list):
        return [a for a in data if isinstance(a, dict)]
    if isinstance(data, dict):
        anns = data.get("annotations")
        if isinstance(anns, list):
            return [a for a in anns if isinstance(a, dict)]
    return []


def _normalize_coords(ann: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    coords = ann.get("coords")
    if not isinstance(coords, list) or len(coords) < 2:
        return None, None
    try:
        return int(coords[0]), int(coords[1])
    except (TypeError, ValueError):
        return None, None


def import_file(doc_id: str, page_num: str, path: str, overwrite: bool, dry_run: bool, status: str = "published") -> Dict[str, int]:
    """Import one page_*.json file. Returns per-file imported/skipped/errors counts."""
    counts = {"imported": 0, "skipped": 0, "errors": 0}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        counts["errors"] += 1
        return counts

    for ann in _extract_annotations(data):
        raw_id = ann.get("id")
        ann_id = raw_id.strip() if isinstance(raw_id, str) and raw_id.strip() else f"srv-import-{secrets.token_hex(4)}"

        ann_type = ann.get("annType")
        if not isinstance(ann_type, str) or not ann_type.strip():
            ann_type = "comment"

        text = ann.get("text")
        if not isinstance(text, str):
            text = "" if text is None else str(text)

        coord_x, coord_y = _normalize_coords(ann)

        # Absent "tags" leaves an existing annotation's tags alone; unknown or
        # reserved tags abort the file rather than land half-imported.
        # Категория: как и теги, отсутствие ключа = «не трогать», чтобы повторный
        # импорт не сбрасывал уже проставленные категории в «Прочее».
        category = None
        if "category" in ann:
            try:
                category = annotation_categories.normalize_category(ann["category"])
            except annotation_categories.CategoryError as exc:
                raise SystemExit(f"{path}: {exc}")

        tags = None
        if "tags" in ann:
            try:
                tags = db.normalize_tags(ann["tags"])
            except db.TagError as exc:
                print(f"  {path}: {ann_id}: {exc}", file=sys.stderr)
                counts["errors"] += 1
                continue

        existing = db.get_annotation(doc_id, page_num, ann_id)
        if existing is not None and not overwrite:
            counts["skipped"] += 1
            continue

        if not dry_run:
            db.upsert_annotation_db(
                doc_id,
                page_num,
                ann_id,
                ann_type,
                text,
                coord_x=coord_x,
                coord_y=coord_y,
                status=status,
                author_id=None,
                action="import",
                tags=tags,
                category=category,
                # Импорт идёт из конвейера агента-аннотатора, значит категория
                # в файле — машинное решение и ждёт приёмки, а не решение
                # человека. None здесь означало бы «человек», см. db.upsert.
                category_source="agent" if category is not None else None,
            )
        counts["imported"] += 1

    return counts


def run(source_dir: str, doc_id: Optional[str], overwrite: bool, dry_run: bool, status: str = "published") -> Dict[str, int]:
    totals = {"docs": 0, "pages": 0, "imported": 0, "skipped": 0, "errors": 0}
    for d in _iter_doc_dirs(source_dir, doc_id):
        pages = _iter_page_files(source_dir, d)
        if not pages:
            continue
        totals["docs"] += 1
        for page_num, path in pages:
            totals["pages"] += 1
            counts = import_file(d, page_num, path, overwrite=overwrite, dry_run=dry_run, status=status)
            for key in ("imported", "skipped", "errors"):
                totals[key] += counts[key]
    return totals


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", help="Directory containing <docId>/annotations/page_*.json")
    parser.add_argument("--doc", dest="doc_id", default=None, help="Import only this docId")
    parser.add_argument("--dry-run", action="store_true", help="Report counts without writing to the DB")
    parser.add_argument("--overwrite", action="store_true", help="Update existing annotations instead of skipping them")
    parser.add_argument("--status", choices=["published", "draft"], default="published",
                        help="Status to assign imported annotations (default: published). Use 'draft' to add "
                             "reviewer-only annotations: they render into page_<NNN>.json carrying the 'draft' "
                             "tag, which the viewer hides unless the URL asks for it (?showDrafts=1 / ?tags=draft).")
    args = parser.parse_args(argv)

    db.init_db()
    totals = run(args.source_dir, args.doc_id, overwrite=args.overwrite, dry_run=args.dry_run, status=args.status)

    print(
        "docs=%(docs)d pages=%(pages)d imported=%(imported)d skipped=%(skipped)d errors=%(errors)d"
        % totals
        + (" (dry-run)" if args.dry_run else "")
    )
    return 1 if totals["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
