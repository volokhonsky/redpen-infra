"""
Shared pytest configuration for the RedPen test suite.

This module runs before any test module is imported, so it is the right place
to point the API at throwaway storage/log directories. ``scripts/api`` reads
``STORAGE_DIR`` and ``LOG_DIR`` from the environment at import time (via
``config.py``), therefore these must be set here rather than inside a fixture.
"""

import os
import sys
import tempfile

import pytest

# Repository root (this file lives in <root>/tests).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Make the pure-Python modules importable without installing the package.
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "api"))

# Isolate all API side effects (inbox files, page writes, log files) into a
# temporary directory so tests never touch real data or require /app to exist.
_TMP_ROOT = tempfile.mkdtemp(prefix="redpen_pytest_")
os.environ.setdefault("STORAGE_DIR", os.path.join(_TMP_ROOT, "data"))
os.environ.setdefault("LOG_DIR", os.path.join(_TMP_ROOT, "logs"))
os.environ.setdefault("DB_PATH", os.path.join(_TMP_ROOT, "db", "redpen.db"))
# Keep CORS permissive for tests unless the environment says otherwise.
os.environ.setdefault("CORS_ALLOW_ORIGINS", "*")
# TestClient talks over plain http; a Secure cookie would be dropped.
os.environ.setdefault("COOKIE_SECURE", "false")


@pytest.fixture(autouse=True)
def _ensure_db_initialized():
    """
    scripts/api/db.py needs init_db() called once before any query. main.py
    normally does this from a FastAPI startup hook, but plenty of tests use a
    bare ``TestClient(main.app)`` (no ``with`` block), which never runs
    startup. Idempotent and cheap, so just make sure it's ready before every
    test regardless of what touched db._conn last (e.g. test_db.py resets it
    to None between its own cases).
    """
    import db

    if db._conn is None:
        db.init_db()
