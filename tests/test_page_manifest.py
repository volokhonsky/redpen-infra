"""
Tests for scripts/generate_page_manifest.py (stage 2 / B.2).
"""

import importlib.util
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_module():
    path = os.path.join(ROOT, "scripts", "generate_page_manifest.py")
    spec = importlib.util.spec_from_file_location("generate_page_manifest", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def gpm():
    return _load_module()


def _make_doc(tmp_path, keys, extra_files=()):
    """Create <tmp_path>/doc/images/page_<key>.png for each key, plus any
    extra (non-page_*) filenames, and an empty metadata.json."""
    doc_dir = tmp_path / "doc"
    images_dir = doc_dir / "images"
    images_dir.mkdir(parents=True)
    for key in keys:
        (images_dir / f"page_{key}.png").write_bytes(b"\x89PNG")
    for name in extra_files:
        (images_dir / name).write_bytes(b"x")
    (doc_dir / "metadata.json").write_text(json.dumps({"title": "Test Doc"}), encoding="utf-8")
    return doc_dir


def _write_meta(tmp_path, numbering):
    meta_path = tmp_path / "meta.json"
    meta_path.write_text(json.dumps({"pageNumbering": numbering} if numbering is not None else {}), encoding="utf-8")
    return meta_path


def test_generates_manifest_with_frontmatter_and_printed_pages(gpm, tmp_path):
    doc_dir = _make_doc(tmp_path, ["-01", "000", "001", "002", "003"])
    meta_path = _write_meta(tmp_path, {
        "frontMatter": ["-01", "000"],
        "printedStartFile": "001",
        "printedStartNumber": 1,
    })

    pages = gpm.generate(str(doc_dir), str(meta_path))

    assert pages == [
        {"file": "page_-01", "label": "A1"},
        {"file": "page_000", "label": "A2"},
        {"file": "page_001", "label": "1"},
        {"file": "page_002", "label": "2"},
        {"file": "page_003", "label": "3"},
    ]

    metadata = json.loads((doc_dir / "metadata.json").read_text("utf-8"))
    assert metadata["pages"] == pages
    assert metadata["totalPages"] == 5
    assert metadata["title"] == "Test Doc"  # other fields preserved


def test_frontmatter_entry_can_carry_a_name(gpm, tmp_path):
    doc_dir = _make_doc(tmp_path, ["-01", "001"])
    meta_path = _write_meta(tmp_path, {
        "frontMatter": [{"file": "-01", "name": "Обложка"}],
        "printedStartFile": "001",
        "printedStartNumber": 1,
    })

    pages = gpm.generate(str(doc_dir), str(meta_path))
    assert pages[0] == {"file": "page_-01", "label": "A1", "name": "Обложка"}


def test_missing_section_leaves_legacy_mode(gpm, tmp_path, capsys):
    doc_dir = _make_doc(tmp_path, ["001", "002"])
    meta_path = _write_meta(tmp_path, None)

    pages = gpm.generate(str(doc_dir), str(meta_path))
    assert pages is None

    metadata = json.loads((doc_dir / "metadata.json").read_text("utf-8"))
    assert "pages" not in metadata

    out = capsys.readouterr().out
    assert "legacy mode" in out


def test_legacy_pagenumbering_without_printed_start_is_also_legacy_mode(gpm, tmp_path):
    # The pre-existing pageNumbering shape used by main.js's old arithmetic
    # (physicalStart/logicalStart) must NOT be mistaken for the new section.
    doc_dir = _make_doc(tmp_path, ["001", "002"])
    meta_path = _write_meta(tmp_path, {"physicalStart": 1, "logicalStart": 1})

    pages = gpm.generate(str(doc_dir), str(meta_path))
    assert pages is None


def test_ignore_mask_excludes_non_page_files(gpm, tmp_path):
    doc_dir = _make_doc(tmp_path, ["001", "002"], extra_files=["202-afghanistan.jpeg"])
    meta_path = _write_meta(tmp_path, {
        "frontMatter": [],
        "printedStartFile": "001",
        "printedStartNumber": 1,
    })

    pages = gpm.generate(str(doc_dir), str(meta_path))
    assert [p["file"] for p in pages] == ["page_001", "page_002"]


def test_ignore_flag_excludes_explicit_page_keys(gpm, tmp_path):
    # A page_* file that isn't part of the sequence (e.g. a misnamed asset)
    # can be excluded explicitly instead of breaking continuity validation.
    doc_dir = _make_doc(tmp_path, ["001", "002", "999"])
    meta_path = _write_meta(tmp_path, {
        "frontMatter": [],
        "printedStartFile": "001",
        "printedStartNumber": 1,
    })

    pages = gpm.generate(str(doc_dir), str(meta_path), ignore=["999"])
    assert [p["file"] for p in pages] == ["page_001", "page_002"]


def test_missing_frontmatter_file_fails_validation(gpm, tmp_path):
    doc_dir = _make_doc(tmp_path, ["001"])  # "-01" is declared but doesn't exist
    meta_path = _write_meta(tmp_path, {
        "frontMatter": ["-01"],
        "printedStartFile": "001",
        "printedStartNumber": 1,
    })

    with pytest.raises(gpm.ManifestError, match="frontMatter file"):
        gpm.generate(str(doc_dir), str(meta_path))


def test_gap_in_printed_sequence_fails_validation(gpm, tmp_path):
    doc_dir = _make_doc(tmp_path, ["001", "003"])  # missing 002
    meta_path = _write_meta(tmp_path, {
        "frontMatter": [],
        "printedStartFile": "001",
        "printedStartNumber": 1,
    })

    with pytest.raises(gpm.ManifestError, match="gap"):
        gpm.generate(str(doc_dir), str(meta_path))


def test_uncovered_page_before_printed_start_fails_validation(gpm, tmp_path):
    # "000" exists but is neither in frontMatter nor ignored -> must fail
    # instead of silently dropping it.
    doc_dir = _make_doc(tmp_path, ["000", "001"])
    meta_path = _write_meta(tmp_path, {
        "frontMatter": [],
        "printedStartFile": "001",
        "printedStartNumber": 1,
    })

    with pytest.raises(gpm.ManifestError):
        gpm.generate(str(doc_dir), str(meta_path))


def test_duplicate_frontmatter_entry_fails_validation(gpm, tmp_path):
    doc_dir = _make_doc(tmp_path, ["-01", "001"])
    meta_path = _write_meta(tmp_path, {
        "frontMatter": ["-01", "-01"],
        "printedStartFile": "001",
        "printedStartNumber": 1,
    })

    with pytest.raises(gpm.ManifestError, match="duplicate"):
        gpm.generate(str(doc_dir), str(meta_path))


def test_labels_are_unique_across_a_valid_manifest(gpm, tmp_path):
    doc_dir = _make_doc(tmp_path, ["-01", "000", "001", "002"])
    meta_path = _write_meta(tmp_path, {
        "frontMatter": ["-01", "000"],
        "printedStartFile": "001",
        "printedStartNumber": 1,
    })
    pages = gpm.generate(str(doc_dir), str(meta_path))
    labels = [p["label"] for p in pages]
    assert len(labels) == len(set(labels))
