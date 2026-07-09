"""
Tests for ``scripts/build_website.py``.

The build script is hard-wired to ``<project_root>/redpen-content`` and
``<project_root>/templates``. To keep the test fast and independent of the real
(400-page) content repo, we point ``build_website.project_root`` at a temporary
directory containing a minimal synthetic book, and symlink the real templates.

These tests assert the CURRENT publish layout, where each document is published
into ``<target>/<doc>/{annotations,images,text}`` (not flat at the target root,
which is what the previous version of this test incorrectly expected).
"""

import importlib.util
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = "medinsky11klass"

# A tiny valid PNG (1x1) so PIL can open/resize it for cover generation.
_PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

_SAMPLE_MD = """~~~meta
type: main
id: ann-1
target: page_007_line003
~~~

Main annotation.

~~~meta
type: general
~~~

General comment.
"""


def _load_build_website():
    path = os.path.join(ROOT, "scripts", "build_website.py")
    spec = importlib.util.spec_from_file_location("build_website", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def synthetic_project(tmp_path):
    """Create a temp project root with one minimal book and real templates."""
    content = tmp_path / "redpen-content" / DOC
    (content / "annotations").mkdir(parents=True)
    (content / "images").mkdir()
    (content / "text").mkdir()

    (content / "annotations" / "page_007.md").write_text(_SAMPLE_MD, encoding="utf-8")
    (content / "text" / "page_007.json").write_text("[]", encoding="utf-8")
    (content / "images" / "page_007.png").write_bytes(_PNG_1x1)
    (content / "meta.json").write_text('{"title": "Test Book"}', encoding="utf-8")

    # Reuse the real templates directory (CSS/JS/favicon/document_index.html).
    os.symlink(os.path.join(ROOT, "templates"), tmp_path / "templates")

    build = _load_build_website()
    build.project_root = str(tmp_path)
    return build, tmp_path


def test_build_produces_per_document_layout(synthetic_project, tmp_path):
    build, _ = synthetic_project
    target = tmp_path / "out"

    assert build.convert_annotations(str(target)) is True
    assert build.publish_website_data(str(target)) is True
    build.create_index_page(str(target))

    doc_dir = target / DOC
    # Per-document content lives under <target>/<doc>/...
    assert (doc_dir / "annotations" / "page_007.json").is_file()
    assert (doc_dir / "images" / "page_007.png").is_file()
    assert (doc_dir / "text" / "page_007.json").is_file()
    assert (doc_dir / "index.html").is_file()
    assert (doc_dir / "metadata.json").is_file()

    # Shared assets are copied to the target root.
    assert (target / "css").is_dir() and any((target / "css").iterdir())
    assert (target / "js").is_dir() and any((target / "js").iterdir())
    assert (target / "favicon.svg").is_file()
    assert (target / "index.html").is_file()

    # Cabinet page (stage 3, C.10): copied alongside css/js.
    assert (target / "cabinet" / "index.html").is_file()
    assert (target / "cabinet" / "cabinet.js").is_file()
    assert (target / "cabinet" / "cabinet.css").is_file()


def test_converted_annotation_json_is_valid(synthetic_project, tmp_path):
    build, _ = synthetic_project
    target = tmp_path / "out"
    build.convert_annotations(str(target))

    import json

    data = json.loads((target / DOC / "annotations" / "page_007.json").read_text("utf-8"))
    assert isinstance(data, list) and len(data) == 2
    assert data[0]["annType"] == "main"
    assert data[0]["targetBlock"] == "page_007_line003"
    assert data[1]["annType"] == "general"


# ---------------------------------------------------------------------------
# --annotations-from-md (stage 2, A2.7): md->json conversion is archive-only
# and off by default so a routine build doesn't clobber annotations/*.json
# exported from the SQLite DB by scripts/api/export_annotations.py.
# ---------------------------------------------------------------------------

def test_default_build_does_not_touch_existing_annotations_json(synthetic_project, tmp_path, monkeypatch):
    build, _ = synthetic_project
    target = tmp_path / "out"

    # Simulate a page_007.json already exported from the DB, distinct from
    # what md_to_json would produce from the synthetic page_007.md.
    ann_dir = target / DOC / "annotations"
    ann_dir.mkdir(parents=True)
    exported_marker = '[{"id": "from-db", "text": "exported from db", "annType": "comment"}]'
    (ann_dir / "page_007.json").write_text(exported_marker, encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        ["build_website.py", "--target-dir", str(target), "--skip-tests", "--skip-push"],
    )
    build.main()

    assert (ann_dir / "page_007.json").read_text("utf-8") == exported_marker


def test_annotations_from_md_flag_converts(synthetic_project, tmp_path, monkeypatch):
    build, _ = synthetic_project
    target = tmp_path / "out"

    monkeypatch.setattr(
        "sys.argv",
        ["build_website.py", "--target-dir", str(target), "--skip-tests", "--skip-push", "--annotations-from-md"],
    )
    build.main()

    import json

    data = json.loads((target / DOC / "annotations" / "page_007.json").read_text("utf-8"))
    assert isinstance(data, list) and len(data) == 2
    assert data[0]["annType"] == "main"


# ---------------------------------------------------------------------------
# Page manifest generation (stage 2, B.2): wired in after publish_website_data.
# ---------------------------------------------------------------------------

def test_manifest_not_written_without_pagenumbering_section(synthetic_project, tmp_path):
    # The synthetic fixture's meta.json has no pageNumbering section at all ->
    # legacy mode, zero regression for documents not yet migrated.
    import json

    build, project_root_path = synthetic_project
    target = tmp_path / "out"
    build.publish_website_data(str(target))

    assert build.generate_page_manifests(str(target)) is True

    metadata = json.loads((target / DOC / "metadata.json").read_text("utf-8"))
    assert "pages" not in metadata


def test_manifest_written_when_pagenumbering_section_present(synthetic_project, tmp_path):
    import json

    build, project_root_path = synthetic_project
    target = tmp_path / "out"
    build.publish_website_data(str(target))

    meta_path = os.path.join(project_root_path, "redpen-content", DOC, "meta.json")
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    meta["pageNumbering"] = {"frontMatter": [], "printedStartFile": "007", "printedStartNumber": 1}
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f)

    assert build.generate_page_manifests(str(target)) is True

    metadata = json.loads((target / DOC / "metadata.json").read_text("utf-8"))
    assert metadata["pages"] == [{"file": "page_007", "label": "1"}]
    assert metadata["totalPages"] == 1


def test_index_page_lists_document_title(synthetic_project, tmp_path):
    build, _ = synthetic_project
    target = tmp_path / "out"
    build.publish_website_data(str(target))
    build.create_index_page(str(target))

    index_html = (target / "index.html").read_text("utf-8")
    assert "Test Book" in index_html          # title from meta.json
    assert f"{DOC}/index.html" in index_html   # link to the document
