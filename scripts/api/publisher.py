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


def _render_item(ann: Dict[str, Any], draft: bool = False, with_tags: bool = True) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "id": ann["annId"],
        "text": ann["text"],
        "annType": ann["annType"],
    }
    if ann["coordX"] is not None and ann["coordY"] is not None:
        item["coords"] = [ann["coordX"], ann["coordY"]]
    if not with_tags:
        return item
    # `status` is canonical in the DB; the static file mirrors it as a tag so
    # the viewer has one uniform thing to filter on (?tags= / ?notags=).
    tags = list(ann.get("tags") or [])
    if draft:
        item["draft"] = True
        tags = ["draft"] + tags
    if tags:
        item["tags"] = tags
    return item


def render_page(doc_id: str, page_num: str) -> List[Dict[str, Any]]:
    """Bare array of published annotations for a page, in the legacy format:
    {id, text, annType[, coords]} -- no tags, no drafts.

    This is deliberately frozen: compute_page_sha() runs on it, and that hash is
    the editor's optimistic lock (main._current_page_sha). Adding fields here
    would 409 every editor session open across a deploy, and make draft/tag
    edits collide with unrelated ones. The file on disk comes from
    render_page_static() instead."""
    return [
        _render_item(ann, with_tags=False)
        for ann in db.list_page_annotations(doc_id, page_num, include_deleted=False)
    ]


def render_page_static(doc_id: str, page_num: str) -> List[Dict[str, Any]]:
    """What actually gets written to page_<NNN>.json: published AND draft
    annotations in one array, each carrying its tags. Drafts additionally get
    "draft": true (kept for older viewers) and a leading "draft" tag.

    The viewer hides drafts by default and reveals them per URL parameter; see
    getTagFilter() in templates/js/main.js."""
    rendered = [
        _render_item(ann, draft=False)
        for ann in db.list_page_annotations(doc_id, page_num, include_deleted=False)
    ]
    rendered += [_render_item(ann, draft=True) for ann in db.list_page_drafts(doc_id, page_num)]
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
    """Atomically write the rendered bare array for a page to PUBLISH_DIR.
    Returns False (without raising) if publication is disabled or fails -- the
    DB write already succeeded, and the volume can be repaired later via
    publish_all()."""
    if not config.PUBLISH_DIR:
        return False

    try:
        _atomic_write_json(_page_file_path(doc_id, page_num), render_page_static(doc_id, page_num))

        # Drafts used to live in a sibling page_<NNN>.drafts.json; they are now
        # part of the file above. Removing the leftovers here means publish_all()
        # on the next restart cleans the volume by itself. Drop this branch (and
        # _drafts_file_path) a release after the merged format is everywhere.
        drafts_target = _drafts_file_path(doc_id, page_num)
        if os.path.exists(drafts_target):
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
