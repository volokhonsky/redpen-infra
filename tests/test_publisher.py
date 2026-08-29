"""
Unit tests for scripts/api/publisher.py (stage 2: rendering the SQLite
remarks store into the static bare-array JSON the viewer reads).
"""

import json
import os
import stat

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402
import publisher  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db_path = os.path.join(tmp_path, "redpen.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "PUBLISH_DIR", os.path.join(tmp_path, "public"))
    db.init_db()
    yield
    db._conn.close()
    db._conn = None


def test_render_page_bare_array_with_coords():
    db.upsert_remark_db("doc1", "006", "ann-1", "major", "hi", coord_x=10, coord_y=20)
    rendered = publisher.render_page("doc1", "006")
    # render_page — вход compute_page_sha, он намеренно заморожен в именах,
    # действовавших до переименования сущности (см. его docstring).
    assert rendered == [{"id": "ann-1", "text": "hi", "annType": "main", "coords": [10, 20]}]


def test_render_page_omits_coords_when_they_are_null():
    """Defensive path: remarks always carry coordinates now (the anchorless
    "general" type is retired), but a NULL pair must not render as [null, null]."""
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "note")
    rendered = publisher.render_page("doc1", "006")
    assert rendered == [{"id": "ann-1", "text": "note", "annType": "comment"}]
    assert "coords" not in rendered[0]


def test_render_page_excludes_deleted():
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "one")
    db.upsert_remark_db("doc1", "006", "ann-2", "minor", "two")
    db.soft_delete_remark("doc1", "006", "ann-1")
    rendered = publisher.render_page("doc1", "006")
    assert [a["id"] for a in rendered] == ["ann-2"]


def test_compute_page_sha_is_deterministic():
    rendered = [{"id": "a", "text": "x", "kind": "minor"}]
    assert publisher.compute_page_sha(rendered) == publisher.compute_page_sha(list(rendered))


def test_compute_page_sha_changes_with_content():
    a = [{"id": "a", "text": "x", "kind": "minor"}]
    b = [{"id": "a", "text": "y", "kind": "minor"}]
    assert publisher.compute_page_sha(a) != publisher.compute_page_sha(b)


def test_publish_page_disabled_returns_false(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PUBLISH_DIR", "")
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "one")
    assert publisher.publish_page("doc1", "006") is False


def test_publish_page_writes_valid_bare_array(tmp_path):
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "one", coord_x=1, coord_y=2)
    ok = publisher.publish_page("doc1", "006")
    assert ok is True

    path = os.path.join(config.PUBLISH_DIR, "doc1", "remarks", "page_006.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert data == [{
        "id": "ann-1", "text": "one", "kind": "minor", "coords": [1, 2],
        # Категория — своё поле, по умолчанию «Прочее», плюс зеркальный тег.
        "category": "other",
    }]


def test_publish_page_writes_world_readable_file(tmp_path):
    # PUBLISH_DIR is served directly by nginx (a different uid than the api
    # container). tempfile.mkstemp() defaults to mode 0600 (owner-only); the
    # published file must stay world-readable or nginx 403s on it.
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "one")
    publisher.publish_page("doc1", "006")
    path = os.path.join(config.PUBLISH_DIR, "doc1", "remarks", "page_006.json")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode & stat.S_IROTH, f"expected world-readable, got {oct(mode)}"


def test_publish_page_omits_coords_key_when_null(tmp_path):
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "note")
    publisher.publish_page("doc1", "006")
    path = os.path.join(config.PUBLISH_DIR, "doc1", "remarks", "page_006.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert "coords" not in data[0]


def test_publish_page_is_idempotent(tmp_path):
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "one")
    publisher.publish_page("doc1", "006")
    path = os.path.join(config.PUBLISH_DIR, "doc1", "remarks", "page_006.json")
    with open(path, encoding="utf-8") as f:
        first = f.read()
    publisher.publish_page("doc1", "006")
    with open(path, encoding="utf-8") as f:
        second = f.read()
    assert first == second


def test_render_page_static_marks_drafts_with_flag_and_tag():
    db.upsert_remark_db("doc1", "006", "d-1", "minor", "wip", coord_x=3, coord_y=4, status="draft")
    rendered = publisher.render_page_static("doc1", "006")
    assert rendered == [
        {"id": "d-1", "text": "wip", "kind": "minor", "coords": [3, 4],
         "draft": True, "category": "other", "tags": ["draft"]}
    ]


def test_render_page_static_puts_published_and_drafts_in_one_array():
    db.upsert_remark_db("doc1", "006", "p-1", "minor", "live")
    db.upsert_remark_db("doc1", "006", "d-1", "minor", "wip", status="draft")
    rendered = publisher.render_page_static("doc1", "006")
    assert [a["id"] for a in rendered] == ["p-1", "d-1"]
    assert "draft" not in rendered[0]
    assert rendered[1]["draft"] is True


def test_render_page_static_carries_tags():
    db.upsert_remark_db("doc1", "006", "p-1", "minor", "live", tags=["omission", "framing"])
    assert publisher.render_page_static("doc1", "006")[0]["tags"] == ["framing", "omission"]


def test_render_page_excludes_drafts():
    db.upsert_remark_db("doc1", "006", "p-1", "minor", "live")
    db.upsert_remark_db("doc1", "006", "d-1", "minor", "wip", status="draft")
    assert [a["id"] for a in publisher.render_page("doc1", "006")] == ["p-1"]


def test_render_page_omits_tags_so_the_sha_stays_stable():
    """render_page feeds compute_page_sha, which is the editor's optimistic
    lock -- tagging or drafting must not move it."""
    db.upsert_remark_db("doc1", "006", "p-1", "minor", "live")
    before = publisher.compute_page_sha(publisher.render_page("doc1", "006"))

    db.upsert_remark_db("doc1", "006", "p-1", "minor", "live", tags=["omission"])
    db.upsert_remark_db("doc1", "006", "d-1", "minor", "wip", status="draft")

    assert "tags" not in publisher.render_page("doc1", "006")[0]
    assert publisher.compute_page_sha(publisher.render_page("doc1", "006")) == before


def test_publish_page_writes_drafts_into_the_page_file(tmp_path):
    db.upsert_remark_db("doc1", "006", "p-1", "minor", "live")
    db.upsert_remark_db("doc1", "006", "d-1", "minor", "wip", coord_x=1, coord_y=2, status="draft")
    assert publisher.publish_page("doc1", "006") is True

    main_path = os.path.join(config.PUBLISH_DIR, "doc1", "remarks", "page_006.json")
    with open(main_path, encoding="utf-8") as f:
        data = json.load(f)
    assert [a["id"] for a in data] == ["p-1", "d-1"]
    assert data[1]["tags"] == ["draft"]

    drafts_path = os.path.join(config.PUBLISH_DIR, "doc1", "remarks", "page_006.drafts.json")
    assert not os.path.exists(drafts_path)


def test_sha_input_survives_the_rename_to_remarks():
    """Хеш страницы — оптимистическая блокировка редактора, и переименование
    сущности не должно его сдвинуть: иначе все открытые сессии получают 409
    разом, не получая взамен ничего (наружу этот массив не отдаётся)."""
    db.upsert_remark_db("doc1", "006", "ann-1", "major", "hi", coord_x=10, coord_y=20)
    db.upsert_remark_db("doc1", "006", "ann-2", "minor", "note")
    # Ровно то, что считалось до переименования, — записано литералом.
    legacy = [
        {"id": "ann-1", "text": "hi", "annType": "main", "coords": [10, 20]},
        {"id": "ann-2", "text": "note", "annType": "comment"},
    ]
    assert publisher.compute_page_sha(publisher.render_page("doc1", "006")) == \
        publisher.compute_page_sha(legacy)


def test_publish_page_also_writes_the_legacy_directory():
    """Пока в ходу прежние адреса и уже розданные офлайн-копии, тот же файл
    пишется и в annotations/. Снимается в фазе 6 переименования."""
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "one")
    publisher.publish_page("doc1", "006")
    new_path = os.path.join(config.PUBLISH_DIR, "doc1", "remarks", "page_006.json")
    old_path = os.path.join(config.PUBLISH_DIR, "doc1",
                            publisher.LEGACY_PAGE_DIRNAME, "page_006.json")
    assert os.path.exists(new_path) and os.path.exists(old_path)
    with open(new_path, encoding="utf-8") as f:
        new_data = json.load(f)
    with open(old_path, encoding="utf-8") as f:
        assert json.load(f) == new_data


def test_publish_all_counts_pages(tmp_path):
    db.upsert_remark_db("doc1", "006", "ann-1", "minor", "one")
    db.upsert_remark_db("doc1", "007", "ann-1", "minor", "one")
    db.upsert_remark_db("doc2", "-01", "ann-1", "minor", "cover")

    result = publisher.publish_all()
    assert result == {"pages": 3, "failed": 0}

    for doc_id, page_num in [("doc1", "006"), ("doc1", "007"), ("doc2", "-01")]:
        path = os.path.join(config.PUBLISH_DIR, doc_id, "remarks", f"page_{page_num}.json")
        assert os.path.exists(path)


def test_render_page_stays_frozen_when_category_changes():
    """render_page() — вход compute_page_sha(), то есть оптимистической блокировки
    редактора. Категория в него попасть не должна: иначе классификация всех 1272
    аннотаций отвалила бы 409 каждой открытой сессии редактора."""
    db.upsert_remark_db("doc1", "006", "p-1", "minor", "live", coord_x=1, coord_y=2)
    before = publisher.render_page("doc1", "006")
    sha_before = publisher.compute_page_sha(before)

    db.upsert_remark_db("doc1", "006", "p-1", "minor", "live", coord_x=1, coord_y=2,
                            category="today")

    after = publisher.render_page("doc1", "006")
    assert after == before
    assert "category" not in after[0]
    assert publisher.compute_page_sha(after) == sha_before
    # А в файл на диске категория, наоборот, обязана попасть.
    static = publisher.render_page_static("doc1", "006")
    assert static[0]["category"] == "today"
    assert "cat:today" in static[0]["tags"]
