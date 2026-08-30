"""Оценки замечания: три шкалы вместо одного вердикта.

Удалённая ревью-подсистема сводила в одно значение три разных решения —
«факт неинтересен», «замечание неважно», «так предъявлять нельзя». Здесь они
разведены по шкалам, и проверяется, что шкалы независимы, оценка одна на
участника и что она ничего не публикует.
"""

import os

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import db  # noqa: E402
import publisher  # noqa: E402
import rating_scales  # noqa: E402
from _auth_helpers import anon, login  # noqa: E402

DOC = "ratedoc"
PAGE = "42"
PAGE_KEY = "042"


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setattr(config, "BOOTSTRAP_INVITE_CODE", "")
    monkeypatch.setattr(config, "DB_PATH", os.path.join(tmp_path, "redpen.db"))
    if db._conn is not None:
        db._conn.close()
    db._conn = None
    db.init_db()
    yield
    if db._conn is not None:
        db._conn.close()
    db._conn = None


def _editor(monkeypatch, sub="rate-editor"):
    return login(monkeypatch, sub, role="editor")


def _create(client, remark_id="r-1"):
    r = client.post(f"/api/editor/{DOC}/{PAGE}",
                    json={"id": remark_id, "kind": "minor", "text": "текст",
                          "coords": [10, 20], "status": "published"})
    assert r.status_code == 200, r.text
    return remark_id


def _url(scale, remark_id="r-1"):
    return f"/api/remarks/{DOC}/{PAGE}/{remark_id}/ratings/{scale}"


# --- словарь шкал ----------------------------------------------------------

def test_scales_are_served_to_the_ui(monkeypatch):
    editor = _editor(monkeypatch)
    body = editor.get("/api/rating-scales").json()
    names = [s["name"] for s in body["scales"]]
    assert names == ["interest", "importance", "admissibility"]
    assert all(s["min"] == 1 and s["max"] == 5 for s in body["scales"])


def test_value_validation_rejects_booleans():
    """bool — подкласс int, и True прошёл бы как 1: оценка «истина» бессмысленна."""
    with pytest.raises(rating_scales.ScaleError):
        rating_scales.normalize_value(True)


# --- постановка оценки -----------------------------------------------------

def test_rating_is_stored_per_scale(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    assert editor.put(_url("interest"), json={"value": 5}).status_code == 200
    assert editor.put(_url("importance"), json={"value": 2}).status_code == 200

    summary = editor.get(f"/api/remarks/{DOC}/{PAGE}/r-1/ratings").json()["summary"]
    assert summary["interest"]["mine"] == 5
    assert summary["importance"]["mine"] == 2
    # Третья шкала не оценена — и это видно, а не подменяется нулём.
    assert summary["admissibility"]["mine"] is None
    assert summary["admissibility"]["average"] is None


def test_re_rating_replaces_the_previous_value(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    editor.put(_url("interest"), json={"value": 2})
    editor.put(_url("interest"), json={"value": 4})
    items = db.list_ratings(DOC, PAGE_KEY, "r-1")
    assert len(items) == 1
    assert items[0]["value"] == 4


def test_ratings_of_different_people_average(monkeypatch):
    first = _editor(monkeypatch, "rate-one")
    _create(first)
    second = _editor(monkeypatch, "rate-two")
    first.put(_url("importance"), json={"value": 5})
    second.put(_url("importance"), json={"value": 2})

    summary = second.get(f"/api/remarks/{DOC}/{PAGE}/r-1/ratings").json()["summary"]
    assert summary["importance"]["count"] == 2
    assert summary["importance"]["average"] == 3.5
    assert summary["importance"]["mine"] == 2


def test_rating_note_is_optional_and_bounded(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    r = editor.put(_url("admissibility"), json={"value": 3, "note": "нужен источник"})
    assert r.status_code == 200
    assert r.json()["rating"]["note"] == "нужен источник"
    assert editor.put(_url("admissibility"),
                      json={"value": 3, "note": "x" * 600}).status_code == 400


@pytest.mark.parametrize("value", [0, 6, -1, "три", None, 2.5])
def test_value_out_of_range_is_refused(monkeypatch, value):
    editor = _editor(monkeypatch)
    _create(editor)
    assert editor.put(_url("interest"), json={"value": value}).status_code == 400


def test_unknown_scale_is_refused(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    assert editor.put(_url("красота"), json={"value": 3}).status_code == 400


def test_rating_an_unknown_remark_404(monkeypatch):
    editor = _editor(monkeypatch)
    assert editor.put(_url("interest", "nope"), json={"value": 3}).status_code == 404


# --- снятие оценки ---------------------------------------------------------

def test_clearing_own_rating(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    editor.put(_url("interest"), json={"value": 3})
    assert editor.delete(_url("interest")).status_code == 200
    assert db.list_ratings(DOC, PAGE_KEY, "r-1") == []


def test_clearing_a_rating_that_was_never_set_404(monkeypatch):
    editor = _editor(monkeypatch)
    _create(editor)
    assert editor.delete(_url("interest")).status_code == 404


def test_clearing_does_not_touch_other_peoples_ratings(monkeypatch):
    first = _editor(monkeypatch, "rate-keep-one")
    _create(first)
    second = _editor(monkeypatch, "rate-keep-two")
    first.put(_url("interest"), json={"value": 5})
    second.put(_url("interest"), json={"value": 1})
    second.delete(_url("interest"))
    remaining = db.list_ratings(DOC, PAGE_KEY, "r-1")
    assert [r["value"] for r in remaining] == [5]


# --- права -----------------------------------------------------------------

def test_viewer_cannot_rate(monkeypatch):
    editor = _editor(monkeypatch, "rate-owner")
    _create(editor)
    viewer = login(monkeypatch, "rate-viewer", role="viewer")
    assert viewer.put(_url("interest"), json={"value": 3}).status_code == 403
    assert anon().put(_url("interest"), json={"value": 3}).status_code == 401


# --- главный инвариант -----------------------------------------------------

def test_ratings_never_reach_the_static_site(monkeypatch):
    """Оценка — рабочая пометка редактора. Просмотрщик о ней знать не должен, и
    страница от неё не меняется: sha остаётся прежним."""
    editor = _editor(monkeypatch)
    _create(editor)
    before = publisher.compute_page_sha(publisher.render_page(DOC, PAGE_KEY))
    editor.put(_url("interest"), json={"value": 5, "note": "секретная пометка"})

    rendered = publisher.render_page(DOC, PAGE_KEY)
    assert publisher.compute_page_sha(rendered) == before
    blob = repr(rendered)
    assert "секретная пометка" not in blob
    assert "interest" not in blob
    for item in rendered:
        assert "ratings" not in item and "rating" not in item
