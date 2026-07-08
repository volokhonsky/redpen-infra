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
# Keep CORS permissive for tests unless the environment says otherwise.
os.environ.setdefault("CORS_ALLOW_ORIGINS", "*")
