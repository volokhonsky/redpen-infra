import json
import os
import tempfile
import uuid
from datetime import datetime
from typing import Any


def get_inbox_dir(base_dir: str) -> str:
    """Return inbox/YYYYMMDD relative path joined to base_dir.
    Returns absolute path when joined with base_dir, but this function returns just the full absolute dir path for convenience.
    """
    today = datetime.now().strftime("%Y%m%d")
    return os.path.join(base_dir, "inbox", today)


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
    data_str = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
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
