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


def render_drafts(doc_id: str, page_num: str) -> List[Dict[str, Any]]:
    """Bare array of draft (status='draft') annotations for a page, same shape
    as render_page() but each item carries "draft": true. Published to a
    separate page_<NNN>.drafts.json the viewer loads only in ?showDrafts=1
    preview mode -- kept out of page_<NNN>.json so drafts never affect the
    serverPageSha used for the editor's optimistic locking."""
    rendered: List[Dict[str, Any]] = []
    for ann in db.list_page_drafts(doc_id, page_num):
        item: Dict[str, Any] = {
            "id": ann["annId"],
            "text": ann["text"],
            "annType": ann["annType"],
            "draft": True,
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


def _drafts_file_path(doc_id: str, page_num: str) -> str:
    return os.path.join(config.PUBLISH_DIR, doc_id, "annotations", f"page_{page_num}.drafts.json")


def _atomic_write_json(target: str, rendered: List[Dict[str, Any]]) -> None:
    """Atomically write `rendered` as pretty JSON to `target`, world-readable."""
    target_dir = os.path.dirname(target)
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


def publish_page(doc_id: str, page_num: str) -> bool:
    """Atomically write the rendered bare array for a page to PUBLISH_DIR, plus
    a sibling page_<NNN>.drafts.json for the ?showDrafts=1 preview mode. Returns
    False (without raising) if publication is disabled or fails -- the DB write
    already succeeded, and the volume can be repaired later via publish_all()."""
    if not config.PUBLISH_DIR:
        return False

    try:
        _atomic_write_json(_page_file_path(doc_id, page_num), render_page(doc_id, page_num))

        drafts = render_drafts(doc_id, page_num)
        drafts_target = _drafts_file_path(doc_id, page_num)
        if drafts:
            _atomic_write_json(drafts_target, drafts)
        elif os.path.exists(drafts_target):
            # No drafts remain (all published/deleted): drop any stale file so
            # the preview mode doesn't keep showing gone drafts.
            os.remove(drafts_target)
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
