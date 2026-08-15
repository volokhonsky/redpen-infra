"""
CLI: export annotations from the SQLite store (db.py) into the "bare array"
page_NNN.json files used by the portable redpen-publish git snapshot (stage 2,
docs/agent-instructions-stage-2.md, A2.7).

Usage:
    python export_annotations.py --to <dir> [--doc <docId>]

Writes <dir>/<docId>/annotations/page_<NNN>.json for every page that has at
least one annotation row, using the same renderer as the live publisher
(publisher.render_page_static) -- published and draft annotations together,
with their tags, exactly as the volume gets it. Run this wherever the DB
lives (inside the api container), then commit/push the target directory
(typically the mounted redpen-publish working copy) separately.
"""

import argparse
import json
import os
import sys
import tempfile
from typing import List, Optional

import db
import publisher


def _write_page(target_dir: str, doc_id: str, page_num: str) -> None:
    rendered = publisher.render_page_static(doc_id, page_num)
    out_dir = os.path.join(target_dir, doc_id, "annotations")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"page_{page_num}.json")

    data_bytes = json.dumps(rendered, ensure_ascii=False, indent=2).encode("utf-8")
    fd, tmp_path = tempfile.mkstemp(dir=out_dir, prefix="._tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(data_bytes)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.chmod(tmp_path, 0o644)  # mkstemp() defaults to 0600 (owner-only)
        os.replace(tmp_path, out_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def run(target_dir: str, doc_id: Optional[str] = None) -> int:
    pages = db.list_pages(doc_id)
    for d, page_num in pages:
        _write_page(target_dir, d, page_num)
    return len(pages)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to", dest="target_dir", required=True, help="Directory to write <docId>/annotations/page_*.json into")
    parser.add_argument("--doc", dest="doc_id", default=None, help="Export only this docId")
    args = parser.parse_args(argv)

    db.init_db()
    count = run(args.target_dir, args.doc_id)
    print(f"pages={count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
