"""Каталоги remarks/ и pages/ заводятся только книгам.

До 2026-08-31 content-sync создавал их в каждом каталоге первого уровня тома,
включая js/, css/, survey/ — мусор без вреда, но и без смысла.
"""
import importlib.util
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "content-sync" / "content_sync.py"


@pytest.fixture(scope="module")
def content_sync():
    spec = importlib.util.spec_from_file_location("content_sync", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_owned_dirs_only_for_docs(content_sync, tmp_path, monkeypatch):
    monkeypatch.setattr(content_sync.subprocess, "call", lambda *a, **kw: 0)

    public = tmp_path / "public"
    doc = public / "medinsky11klass"
    doc.mkdir(parents=True)
    (doc / "metadata.json").write_text("{}", encoding="utf-8")
    (public / "js").mkdir()
    (public / "survey").mkdir()
    (public / "robots.txt").write_text("", encoding="utf-8")

    content_sync._ensure_api_owned_dirs(public)

    for name in content_sync._API_OWNED_DIRS:
        assert (doc / name).is_dir()
        assert not (public / "js" / name).exists()
        assert not (public / "survey" / name).exists()
