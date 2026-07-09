"""
Renders published (status='published') annotations from the SQLite store
(db.py) into the static "bare array" JSON files the viewer reads
(<PUBLISH_DIR>/<docId>/annotations/page_<NNN>.json), and computes the sha256
used as serverPageSha for optimistic locking.

PUBLISH_DIR is empty by default (publication disabled) -- see config.py.
"""

import hashlib
import json
import logging
import os
import tempfile
from typing import Any, Dict, List

import config
import db

logger = logging.getLogger("redpen.api")

_SHA_JSON_KWARGS = dict(ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def render_page(doc_id: str, page_num: str) -> List[Dict[str, Any]]:
    """Bare array of published annotations for a page, in the format the
    static viewer expects: {id, text, annType[, coords]}."""
    rendered: List[Dict[str, Any]] = []
    for ann in db.list_page_annotations(doc_id, page_num, include_deleted=False):
        item: Dict[str, Any] = {
            "id": ann["annId"],
            "text": ann["text"],
            "annType": ann["annType"],
        }
        if ann["coordX"] is not None and ann["coordY"] is not None:
            item["coords"] = [ann["coordX"], ann["coordY"]]
        rendered.append(item)
    return rendered


def compute_page_sha(rendered: List[Dict[str, Any]]) -> str:
    payload = json.dumps(rendered, **_SHA_JSON_KWARGS)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _page_file_path(doc_id: str, page_num: str) -> str:
    return os.path.join(config.PUBLISH_DIR, doc_id, "annotations", f"page_{page_num}.json")


def publish_page(doc_id: str, page_num: str) -> bool:
    """Atomically write the rendered bare array for a page to PUBLISH_DIR.
    Returns False (without raising) if publication is disabled or fails --
    the DB write already succeeded, and the volume can be repaired later via
    publish_all()."""
    if not config.PUBLISH_DIR:
        return False

    rendered = render_page(doc_id, page_num)
    target = _page_file_path(doc_id, page_num)
    target_dir = os.path.dirname(target)

    try:
        os.makedirs(target_dir, exist_ok=True)
        data_bytes = json.dumps(rendered, ensure_ascii=False, indent=2).encode("utf-8")
        fd, tmp_path = tempfile.mkstemp(dir=target_dir, prefix="._tmp_", suffix=".json")
        try:
            with os.fdopen(fd, "wb") as tmp:
                tmp.write(data_bytes)
                tmp.flush()
                os.fsync(tmp.fileno())
            # mkstemp() creates the file mode 0600 (owner-only); this directory
            # is served directly by nginx (a different uid), so it must be
            # world-readable like a normal checked-out file.
            os.chmod(tmp_path, 0o644)
            os.replace(tmp_path, target)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
    except Exception:
        logger.error("publish_page failed doc_id=%s page_num=%s", doc_id, page_num, exc_info=True)
        return False
    return True


def publish_all() -> Dict[str, int]:
    """Republish every page that has at least one annotation row. Used by the
    admin endpoint and on startup to self-heal the volume."""
    pages = db.list_pages()
    failed = 0
    for doc_id, page_num in pages:
        if not publish_page(doc_id, page_num):
            failed += 1
    return {"pages": len(pages), "failed": failed}
