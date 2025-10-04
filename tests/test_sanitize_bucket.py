import sys
import os

# Make scripts/api importable
ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, 'scripts', 'api'))

import storage  # noqa: E402


def test_editor_drafts():
    assert storage.sanitize_bucket("Editor Drafts") == "editor-drafts"


def test_book_slug_page():
    assert storage.sanitize_bucket("book:slug/page.007", for_page_id=True) == "book-slug/page-007"


def test_path_traversal():
    assert storage.sanitize_bucket("../etc/passwd") == "etc-passwd"


def test_collapse_slashes():
    assert storage.sanitize_bucket("a///b////c") == "a/b/c"


def test_empty_or_invalid():
    assert storage.sanitize_bucket("") == ""
    assert storage.sanitize_bucket("***") == ""
