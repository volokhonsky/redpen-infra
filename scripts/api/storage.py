import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import config

# Storage base directory (constant from config)
STORAGE_BASE_DIR = config.STORAGE_DIR


def sanitize_bucket(candidate: str, for_page_id: bool = False, max_len: int = 120, max_depth: int = 3) -> str:
    """Sanitize bucket name or derived value from pageId.

    Rules:
    - lowercase; spaces -> '-'
    - for pageId additionally convert ':' and '.' to '-'
    - allowed chars: [a-z0-9/_-]; replace others with '-'
    - collapse repeated '/'
    - remove leading/trailing '/'
    - enforce max depth (by '/'), up to max_depth segments
    - enforce max length (truncate); then strip trailing '-' or '/'
    - if empty after sanitation -> return ''
    - special: if original contained '..' (path traversal attempt), flatten by replacing remaining '/' with '-'
    """
    if not isinstance(candidate, str):
        return ""
    s_raw = candidate
    traversal = ".." in s_raw
    s = s_raw.strip().lower()
    if not s:
        return ""
    s = s.replace(" ", "-")
    if for_page_id:
        s = s.replace(":", "-").replace(".", "-")
    # replace disallowed chars
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789/_-")
    s = "".join(ch if ch in allowed else "-" for ch in s)
    # collapse multiple slashes
    while "//" in s:
        s = s.replace("//", "/")
    # strip leading/trailing slashes
    s = s.strip("/")
    # enforce max depth
    if s:
        parts = [p for p in s.split("/") if p]
        if len(parts) > max_depth:
            parts = parts[:max_depth]
        s = "/".join(parts)
    # If traversal attempt, do not create nested dirs: replace '/' with '-'
    if traversal:
        s = s.replace("/", "-")
    # enforce max length
    if len(s) > max_len:
        s = s[:max_len]
    s = s.strip("-/")
    # collapse any accidental repeats again
    while "//" in s:
        s = s.replace("//", "/")
    return s

def get_inbox_dir() -> str:
    """Return absolute path to STORAGE_BASE_DIR/inbox/YYYYMMDD (UTC)."""
    today = datetime.utcnow().strftime("%Y%m%d")
    return os.path.join(STORAGE_BASE_DIR, "inbox", today)


essential_json_kwargs = dict(ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def save_inbox(obj: Any, bucket: Optional[str] = None, filename: Optional[str] = None) -> str:
    """
    Save given Python object as JSON into STORAGE_BASE_DIR/inbox/YYYYMMDD[/bucket]/<uuid>.json atomically.
    Returns the relative path like: inbox/YYYYMMDD[/bucket]/<uuid>.json
    """
    inbox_abs = get_inbox_dir()
    if bucket:
        inbox_abs = os.path.join(inbox_abs, bucket)
    os.makedirs(inbox_abs, exist_ok=True)

    filename = filename or f"{uuid.uuid4()}.json"
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
    today = os.path.basename(get_inbox_dir())
    parts = ["inbox", today]
    if bucket:
        parts.append(bucket)
    parts.append(filename)
    rel_path = os.path.join(*parts)
    return rel_path


# ===== Pages storage =====

def page_path(doc_id: str, page_num: str) -> str:
    """
    Construct path for page file.
    
    Args:
        doc_id: Document ID (e.g., "medinsky11klass")
        page_num: Page number as string (e.g., "006")
    
    Returns: /var/redpen-data/medinsky11klass/annotations/page_006.json
    """
    return os.path.join(STORAGE_BASE_DIR, doc_id, "annotations", f"page_{page_num}.json")


def _default_page(page_id: str) -> Dict[str, Any]:
    return {
        "pageId": page_id,
        "imageUrl": "",
        "origW": 0,
        "origH": 0,
        "serverPageSha": "",
        "annotations": [],
    }


def load_page(doc_id: str, page_num: str) -> Dict[str, Any]:
    """Load page JSON or return default structure if not exists.
    
    Args:
        doc_id: Document ID
        page_num: Page number as string
    
    Returns: Page object with annotations
    """
    page_id = f"{doc_id}_page_{page_num}"
    p = page_path(doc_id, page_num)
    
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


def save_page(doc_id: str, page_num: str, page_obj: Dict[str, Any]) -> str:
    """Atomically write page to docId/annotations/page_NNN.json.
    
    Before write, compute serverPageSha (excluding that field) and set it.
    
    Args:
        doc_id: Document ID
        page_num: Page number as string
        page_obj: Page object to save
    
    Returns: SHA256 hash of page content
    """
    # Ensure required fields
    if not doc_id or not page_num:
        raise ValueError("doc_id and page_num are required")
    
    page_id = f"{doc_id}_page_{page_num}"
    
    # Get target path
    target = page_path(doc_id, page_num)
    target_dir = os.path.dirname(target)
    
    # Ensure directory exists
    os.makedirs(target_dir, exist_ok=True)

    # compute sha and set
    sha = compute_sha(page_obj)
    page_obj = dict(page_obj)
    page_obj["pageId"] = page_id
    page_obj["serverPageSha"] = sha

    # serialize
    data_str = json.dumps(page_obj, **essential_json_kwargs)
    data_bytes = data_str.encode("utf-8")

    # atomic write
    fd, tmp_path = tempfile.mkstemp(dir=target_dir, prefix=f"._tmp_{page_id}_", suffix=".json")
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(data_bytes)
            tmp.flush()
            os.fsync(tmp.fileno())
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
