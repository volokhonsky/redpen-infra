"""
Unit tests for ``scripts/api/storage.py`` — page persistence and annotation
mutation helpers. ``conftest.py`` points ``STORAGE_DIR`` at a temp directory,
so ``save_page``/``load_page`` write there.
"""

import json
import os

import storage


# ---------------------------------------------------------------------------
# page_path / default page
# ---------------------------------------------------------------------------

def test_page_path_layout():
    p = storage.page_path("medinsky11klass", "006")
    assert p.endswith(os.path.join("medinsky11klass", "annotations", "page_006.json"))
    assert p.startswith(storage.STORAGE_BASE_DIR)


def test_load_missing_page_returns_default():
    page = storage.load_page("someunwritten_doc", "999")
    assert page["pageId"] == "someunwritten_doc_page_999"
    assert page["annotations"] == []
    assert page["serverPageSha"] == ""
    assert page["origW"] == 0 and page["origH"] == 0


# ---------------------------------------------------------------------------
# save_page / load_page round-trip + sha
# ---------------------------------------------------------------------------

def test_save_and_load_round_trip():
    doc, num = "roundtrip_doc", "003"
    page = storage._default_page(f"{doc}_page_{num}")
    page["annotations"] = [{"id": "a1", "annType": "comment", "text": "hi", "coords": [1, 2]}]

    sha = storage.save_page(doc, num, page)
    assert sha

    loaded = storage.load_page(doc, num)
    assert loaded["serverPageSha"] == sha
    assert loaded["pageId"] == f"{doc}_page_{num}"
    assert loaded["annotations"][0]["text"] == "hi"

    # File really exists on disk at the computed path.
    assert os.path.exists(storage.page_path(doc, num))


def test_save_page_requires_ids():
    try:
        storage.save_page("", "003", storage._default_page("x"))
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for empty doc_id")


def test_compute_sha_ignores_server_sha_field():
    base = {"pageId": "d_page_001", "annotations": [], "serverPageSha": "AAA"}
    other = dict(base)
    other["serverPageSha"] = "BBB"
    assert storage.compute_sha(base) == storage.compute_sha(other)

    changed = dict(base)
    changed["annotations"] = [{"id": "x"}]
    assert storage.compute_sha(changed) != storage.compute_sha(base)


def test_load_corrupt_json_returns_default():
    doc, num = "corrupt_doc", "004"
    path = storage.page_path(doc, num)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{ this is not valid json")
    page = storage.load_page(doc, num)
    assert page["pageId"] == f"{doc}_page_{num}"
    assert page["annotations"] == []


# ---------------------------------------------------------------------------
# upsert_annotation / update_annotation
# ---------------------------------------------------------------------------

def test_upsert_appends_new_annotation():
    page = storage._default_page("d_page_001")
    storage.upsert_annotation(page, {"id": "a1", "annType": "comment", "text": "one"})
    storage.upsert_annotation(page, {"id": "a2", "annType": "main", "text": "two"})
    assert [a["id"] for a in page["annotations"]] == ["a1", "a2"]


def test_upsert_updates_existing_by_id():
    page = storage._default_page("d_page_001")
    storage.upsert_annotation(page, {"id": "a1", "annType": "comment", "text": "one"})
    storage.upsert_annotation(page, {"id": "a1", "annType": "main", "text": "changed"})
    assert len(page["annotations"]) == 1
    assert page["annotations"][0]["text"] == "changed"
    assert page["annotations"][0]["annType"] == "main"


def test_upsert_drops_unknown_fields():
    page = storage._default_page("d_page_001")
    storage.upsert_annotation(
        page, {"id": "a1", "annType": "comment", "text": "x", "bogus": "drop me"}
    )
    assert "bogus" not in page["annotations"][0]


def test_update_annotation_returns_false_when_not_found():
    page = storage._default_page("d_page_001")
    assert storage.update_annotation(page, "missing", {"text": "x"}) is False


def test_update_annotation_patches_fields():
    page = storage._default_page("d_page_001")
    storage.upsert_annotation(page, {"id": "a1", "annType": "comment", "text": "one", "coords": [1, 2]})
    ok = storage.update_annotation(page, "a1", {"text": "two", "coords": [9, 9]})
    assert ok is True
    assert page["annotations"][0]["text"] == "two"
    assert page["annotations"][0]["coords"] == [9, 9]


def test_saved_file_is_valid_json_with_expected_keys():
    doc, num = "jsoncheck_doc", "007"
    page = storage._default_page(f"{doc}_page_{num}")
    storage.save_page(doc, num, page)
    with open(storage.page_path(doc, num), encoding="utf-8") as f:
        data = json.load(f)
    for key in ("pageId", "imageUrl", "origW", "origH", "serverPageSha", "annotations"):
        assert key in data
