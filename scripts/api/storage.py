import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime
from typing import Any, Dict, List


# ---------------- Inbox storage (from step 1) ----------------

def get_inbox_dir(base_dir: str) -> str:
    """Return absolute path to base_dir/inbox/YYYYMMDD."""
    today = datetime.now().strftime("%Y%m%d")
    return os.path.join(base_dir, "inbox", today)


essential_json_kwargs = dict(ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def save_inbox(obj: Any, base_dir: str) -> str:
    """
    Save given Python object as JSON into base_dir/inbox/YYYYMMDD/<uuid>.json atomically.
    Returns the relative path like: inbox/YYYYMMDD/<uuid>.json
    """
    inbox_abs = get_inbox_dir(base_dir)
    os.makedirs(inbox_abs, exist_ok=True)

    filename = f"{uuid.uuid4()}.json"
    abs_path = os.path.join(inbox_abs, filename)

    # Serialize per spec
    data_str = json.dumps(obj, **essential_json_kwargs)
    data_bytes = data_str.encode("utf-8")

    # Atomic write: write to temp file then replace
    fd, tmp_path = tempfile.mkstemp(dir=inbox_abs, prefix="._tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(data_bytes)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, abs_path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

    # Return relative path
    today = os.path.basename(inbox_abs)
    rel_path = os.path.join("inbox", today, filename)
    return rel_path


# ---------------- Pages storage ----------------

def get_pages_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "pages")


def page_path(base_dir: str, page_id: str) -> str:
    return os.path.join(get_pages_dir(base_dir), f"{page_id}.json")


def _default_page(page_id: str) -> Dict[str, Any]:
    return {
        "pageId": page_id,
        "imageUrl": "",
        "origW": 0,
        "origH": 0,
        "serverPageSha": "",
        "annotations": [],
    }


def load_page(base_dir: str, page_id: str) -> Dict[str, Any]:
    """Load page JSON or return default structure if not exists."""
    p = page_path(base_dir, page_id)
    if not os.path.exists(p):
        return _default_page(page_id)
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _default_page(page_id)
        # ensure required fields
        data.setdefault("pageId", page_id)
        data.setdefault("imageUrl", "")
        data.setdefault("origW", 0)
        data.setdefault("origH", 0)
        data.setdefault("serverPageSha", "")
        anns = data.get("annotations")
        if not isinstance(anns, list):
            data["annotations"] = []
        return data
    except Exception:
        # On parse error, return default (fail-safe)
        return _default_page(page_id)


def compute_sha(page_obj: Dict[str, Any]) -> str:
    """Compute sha256 over JSON of page excluding serverPageSha field."""
    to_hash = dict(page_obj)
    to_hash.pop("serverPageSha", None)
    payload = json.dumps(to_hash, **essential_json_kwargs)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def save_page(base_dir: str, page_obj: Dict[str, Any]) -> str:
    """Atomically write page to /data/pages/{pageId}.json.
    Before write, compute serverPageSha (excluding that field) and set it. Return the sha.
    """
    page_id = str(page_obj.get("pageId") or "").strip()
    if not page_id:
        raise ValueError("pageId is required to save page")

    # ensure directory exists
    pages_dir = get_pages_dir(base_dir)
    os.makedirs(pages_dir, exist_ok=True)

    # compute sha and set
    sha = compute_sha(page_obj)
    page_obj = dict(page_obj)
    page_obj["serverPageSha"] = sha

    # serialize
    data_str = json.dumps(page_obj, **essential_json_kwargs)
    data_bytes = data_str.encode("utf-8")

    # atomic write
    target = page_path(base_dir, page_id)
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(target) or ".", prefix=f"._tmp_{page_id}_", suffix=".json")
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(data_bytes)
            tmp.flush()
            os.fsync(tmp.fileno())
        # Ensure target dir exists (again) before replace
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        os.replace(tmp_path, target)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

    return sha


def _normalize_annotation(a: Dict[str, Any]) -> Dict[str, Any]:
    res: Dict[str, Any] = {}
    if a is None:
        return res
    # copy known fields
    if "id" in a:
        res["id"] = a["id"]
    if "annType" in a:
        res["annType"] = a["annType"]
    if "text" in a:
        res["text"] = a["text"]
    if "coords" in a:
        res["coords"] = a["coords"]
    return res


def upsert_annotation(page_obj: Dict[str, Any], ann: Dict[str, Any]) -> None:
    """Insert or update annotation by id. If id matches existing, update; otherwise append."""
    if not isinstance(page_obj.get("annotations"), list):
        page_obj["annotations"] = []
    anns: List[Dict[str, Any]] = page_obj["annotations"]

    ann = _normalize_annotation(ann)
    ann_id = ann.get("id")

    if ann_id:
        for existing in anns:
            if isinstance(existing, dict) and existing.get("id") == ann_id:
                # update existing fields
                existing.update({k: v for k, v in ann.items() if k in ("id", "annType", "text", "coords")})
                return
    # else append new
    anns.append(ann)


def update_annotation(page_obj: Dict[str, Any], ann_id: str, patch: Dict[str, Any]) -> bool:
    """Find by id and update fields annType/text/coords. Return True if updated, False if not found."""
    if not isinstance(page_obj.get("annotations"), list):
        return False
    for existing in page_obj["annotations"]:
        if isinstance(existing, Dict) and existing.get("id") == ann_id:
            for k in ("annType", "text", "coords"):
                if k in patch:
                    existing[k] = patch[k]
            return True
    return False
