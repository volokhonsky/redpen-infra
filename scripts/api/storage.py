import json
import os
import tempfile
import uuid
from datetime import datetime
from typing import Any, Optional

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
        os.chmod(tmp_path, 0o644)  # mkstemp() defaults to 0600 (owner-only)
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
