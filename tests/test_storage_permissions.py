"""
Regression test for scripts/api/storage.py::save_inbox file permissions.

tempfile.mkstemp() defaults to mode 0600 (owner-only); the identical pattern
in publisher.py/export_remarks.py caused a live 403 once those files
started being served by nginx (see docs/deployment-log.md, 2026-07-09 entry).
save_inbox() writes into STORAGE_DIR/inbox/, which nginx never serves, but
the file should still be world-readable for consistency and to avoid
surprises if that ever changes.
"""

import os
import stat

import storage


def test_save_inbox_writes_world_readable_file():
    rel_path = storage.save_inbox({"hello": "world"})
    abs_path = os.path.join(storage.STORAGE_BASE_DIR, rel_path)
    mode = stat.S_IMODE(os.stat(abs_path).st_mode)
    assert mode & stat.S_IROTH, f"expected world-readable, got {oct(mode)}"
