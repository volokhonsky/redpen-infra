"""
Tests for the cabinet API endpoints (stage 3): GET /api/remarks,
GET /api/history, GET /api/stats, POST /api/history/{histId}/revert,
GET /api/admin/users.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import config  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402


from _auth_helpers import anon, login, with_csrf  # noqa: E402


@pytest.fixture(autouse=True)
def _google_configured(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setattr(config, "BOOTSTRAP_INVITE_CODE", "")


def _login_google(monkeypatch, sub, admin=False) -> TestClient:
    return login(monkeypatch, sub, role="admin" if admin else "viewer", csrf=False)


def _with_csrf(c: TestClient) -> TestClient:
    return with_csrf(c)


def _editor(monkeypatch, sub) -> TestClient:
    """Клиент с ролью editor. Роль приезжает с приглашением, а не со списка
    email-ов: списка больше нет (docs/anonymity-model.md)."""
    return login(monkeypatch, sub, role="editor")


# ---------------------------------------------------------------------------
# GET /api/remarks -- permission matrix
# ---------------------------------------------------------------------------


def test_remarks_anon_401():
    anon = TestClient(main.app)
    assert anon.get("/api/remarks").status_code == 401


def test_remarks_viewer_403(monkeypatch):
    viewer = _login_google(monkeypatch, "viewer-ann@example.com")
    assert viewer.get("/api/remarks").status_code == 403


def test_remarks_editor_200(monkeypatch):
    editor = _editor(monkeypatch, "editor-ann@example.com")
    assert editor.get("/api/remarks").status_code == 200


def test_remarks_admin_200(monkeypatch):
    admin = _login_google(monkeypatch, "admin-ann@example.com", admin=True)
    assert admin.get("/api/remarks").status_code == 200


def test_remarks_filters_and_pagination(monkeypatch):
    editor = _editor(monkeypatch, "editor-ann2@example.com")
    _with_csrf(editor)
    for i in range(3):
        editor.post(
            f"/api/editor/cabdoc/{60 + i}",
            json={"kind": "minor", "text": f"item {i}", "coords": [1, 1]},
        )

    r = editor.get("/api/remarks", params={"docId": "cabdoc", "limit": 2, "offset": 0})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["limit"] == 2
    assert body["offset"] == 0

    r2 = editor.get("/api/remarks", params={"docId": "cabdoc", "pageKey": "60"})
    assert r2.status_code == 200
    assert r2.json()["total"] == 1


@pytest.mark.parametrize(
    "params",
    [
        {"docId": "Bad Doc"},
        {"pageKey": "abc"},
        {"status": "bogus"},
        {"kind": "bogus"},
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"q": "x" * 201},
    ],
)
def test_remarks_rejects_invalid_params(monkeypatch, params):
    editor = _editor(monkeypatch, "editor-ann3@example.com")
    r = editor.get("/api/remarks", params=params)
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/history -- permission matrix
# ---------------------------------------------------------------------------


def test_history_anon_401():
    assert TestClient(main.app).get("/api/history").status_code == 401


def test_history_viewer_403(monkeypatch):
    viewer = _login_google(monkeypatch, "viewer-hist@example.com")
    assert viewer.get("/api/history").status_code == 403


def test_history_editor_200_and_hasmore(monkeypatch):
    editor = _editor(monkeypatch, "editor-hist@example.com")
    _with_csrf(editor)
    for i in range(3):
        editor.post(
            f"/api/editor/histdoc/{60 + i}",
            json={"kind": "minor", "text": f"h{i}", "coords": [1, 1]},
        )
    r = editor.get("/api/history", params={"docId": "histdoc", "limit": 2})
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 2
    assert body["hasMore"] is True

    r2 = editor.get("/api/history", params={"docId": "histdoc", "limit": 50})
    assert r2.json()["hasMore"] is False


# ---------------------------------------------------------------------------
# GET /api/stats
# ---------------------------------------------------------------------------


def test_stats_anon_401():
    assert TestClient(main.app).get("/api/stats").status_code == 401


def test_stats_viewer_200(monkeypatch):
    viewer = _login_google(monkeypatch, "viewer-stats@example.com")
    r = viewer.get("/api/stats")
    assert r.status_code == 200
    assert "docs" in r.json() and "recentActivity" in r.json()


# ---------------------------------------------------------------------------
# POST /api/history/{histId}/revert
# ---------------------------------------------------------------------------


def test_revert_to_update_restores_text_and_coords_and_republishes(monkeypatch):
    editor = _editor(monkeypatch, "editor-revert1@example.com")
    _with_csrf(editor)
    doc, page = "revertdoc", "80"
    created = editor.post(
        f"/api/editor/{doc}/{page}", json={"kind": "minor", "text": "v1", "coords": [1, 1]}
    ).json()
    remark_id = created["id"]
    editor.put(
        f"/api/editor/{doc}/{page}/{remark_id}", json={"kind": "major", "text": "v2", "coords": [9, 9]}
    )

    hist = editor.get("/api/history", params={"docId": doc, "remarkId": remark_id}).json()["items"]
    v1_record = next(h for h in hist if h["snapshot"]["text"] == "v1")

    r = editor.post(f"/api/history/{v1_record['id']}/revert")
    assert r.status_code == 200
    body = r.json()
    assert body["remarkId"] == remark_id
    assert body["published"] is True

    page_data = editor.get(f"/api/editor/{doc}/{page}").json()
    ann = next(a for a in page_data["remarks"] if a["id"] == remark_id)
    assert ann["text"] == "v1"
    assert ann["kind"] == "minor"
    assert ann["coords"] == [1, 1]

    import json as _json
    import os as _os
    path = _os.path.join(config.PUBLISH_DIR, doc, "remarks", f"page_0{page}.json")
    with open(path, encoding="utf-8") as f:
        published = _json.load(f)
    assert any(a["text"] == "v1" for a in published)


def test_revert_before_delete_resurrects_remark(monkeypatch):
    editor = _editor(monkeypatch, "editor-revert2@example.com")
    _with_csrf(editor)
    doc, page = "revertdoc2", "81"
    created = editor.post(
        f"/api/editor/{doc}/{page}", json={"kind": "minor", "text": "alive", "coords": [1, 1]}
    ).json()
    remark_id = created["id"]
    editor.delete(f"/api/editor/{doc}/{page}/{remark_id}")

    # confirm gone
    page_data = editor.get(f"/api/editor/{doc}/{page}").json()
    assert remark_id not in [a["id"] for a in page_data["remarks"]]

    hist = editor.get("/api/history", params={"docId": doc, "remarkId": remark_id}).json()["items"]
    create_record = next(h for h in hist if h["action"] == "create")

    r = editor.post(f"/api/history/{create_record['id']}/revert")
    assert r.status_code == 200

    page_data2 = editor.get(f"/api/editor/{doc}/{page}").json()
    assert remark_id in [a["id"] for a in page_data2["remarks"]]


def test_revert_delete_record_redeletes(monkeypatch):
    editor = _editor(monkeypatch, "editor-revert3@example.com")
    _with_csrf(editor)
    doc, page = "revertdoc3", "82"
    created = editor.post(
        f"/api/editor/{doc}/{page}", json={"kind": "minor", "text": "temp", "coords": [1, 1]}
    ).json()
    remark_id = created["id"]
    editor.delete(f"/api/editor/{doc}/{page}/{remark_id}")

    hist = editor.get("/api/history", params={"docId": doc, "remarkId": remark_id}).json()["items"]
    delete_record = next(h for h in hist if h["action"] == "delete")

    r = editor.post(f"/api/history/{delete_record['id']}/revert")
    assert r.status_code == 200

    page_data = editor.get(f"/api/editor/{doc}/{page}").json()
    assert remark_id not in [a["id"] for a in page_data["remarks"]]

    hist_after = editor.get("/api/history", params={"docId": doc, "remarkId": remark_id}).json()["items"]
    assert hist_after[0]["action"] == "revert"


def test_revert_anon_401(monkeypatch):
    editor = _editor(monkeypatch, "editor-revert4@example.com")
    _with_csrf(editor)
    created = editor.post(
        "/api/editor/revertdoc4/83", json={"kind": "minor", "text": "x", "coords": [1, 1]}
    ).json()
    hist = editor.get("/api/history", params={"docId": "revertdoc4", "remarkId": created["id"]}).json()["items"]

    anon = TestClient(main.app)
    r = anon.post(f"/api/history/{hist[0]['id']}/revert")
    assert r.status_code == 401


def test_revert_without_csrf_403(monkeypatch):
    editor = _editor(monkeypatch, "editor-revert5@example.com")
    _with_csrf(editor)
    created = editor.post(
        "/api/editor/revertdoc5/84", json={"kind": "minor", "text": "x", "coords": [1, 1]}
    ).json()
    hist = editor.get("/api/history", params={"docId": "revertdoc5", "remarkId": created["id"]}).json()["items"]

    editor.headers.pop("X-CSRF-Token", None)
    r = editor.post(f"/api/history/{hist[0]['id']}/revert")
    assert r.status_code == 403


def test_revert_viewer_403(monkeypatch):
    editor = _editor(monkeypatch, "editor-revert6@example.com")
    _with_csrf(editor)
    created = editor.post(
        "/api/editor/revertdoc6/85", json={"kind": "minor", "text": "x", "coords": [1, 1]}
    ).json()
    hist = editor.get("/api/history", params={"docId": "revertdoc6", "remarkId": created["id"]}).json()["items"]

    viewer = _login_google(monkeypatch, "viewer-revert@example.com")
    _with_csrf(viewer)
    r = viewer.post(f"/api/history/{hist[0]['id']}/revert")
    assert r.status_code == 403


def test_revert_missing_history_id_404(monkeypatch):
    editor = _editor(monkeypatch, "editor-revert7@example.com")
    _with_csrf(editor)
    r = editor.post("/api/history/999999/revert")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/admin/users
# ---------------------------------------------------------------------------


def test_admin_users_editor_403(monkeypatch):
    editor = _editor(monkeypatch, "editor-users@example.com")
    assert editor.get("/api/admin/users").status_code == 403


def test_admin_users_admin_200_no_google_sub(monkeypatch):
    admin = _login_google(monkeypatch, "admin-users@example.com", admin=True)
    r = admin.get("/api/admin/users")
    assert r.status_code == 200
    users = r.json()["users"]
    assert len(users) >= 1
    assert all("googleSub" not in u for u in users)


# ---------------------------------------------------------------------------
# GET /api/remarks -- фильтры по категории и её источнику
# ---------------------------------------------------------------------------


def _seed_categorized(doc_id):
    """Три аннотации с одинаковой категорией 'other', но разной судьбой."""
    db.upsert_remark_db(doc_id, "006", "ann-untouched", "minor", "никто не смотрел",
                            coord_x=1, coord_y=1, action="create")
    db.upsert_remark_db(doc_id, "006", "ann-by-human", "minor", "человек выбрал Прочее",
                            coord_x=2, coord_y=2, action="create",
                            category="other", author_id=1)
    db.upsert_remark_db(doc_id, "006", "ann-by-agent", "minor", "решение агента",
                            coord_x=3, coord_y=3, action="create",
                            category="today", category_source="agent", author_id=2)


def test_remarks_expose_category_source(monkeypatch):
    doc = "catdoc-expose"
    _seed_categorized(doc)
    c = _editor(monkeypatch, "cat-expose@example.com")
    r = c.get(f"/api/remarks?docId={doc}")
    assert r.status_code == 200, r.text
    by_id = {item["id"] if "id" in item else item["remarkId"]: item for item in r.json()["items"]}
    assert by_id["ann-untouched"]["categorySource"] == "default"
    assert by_id["ann-by-human"]["categorySource"] == "human"
    assert by_id["ann-by-agent"]["categorySource"] == "agent"


def test_filter_by_category_source_separates_unclassified_from_other(monkeypatch):
    doc = "catdoc-filter"
    _seed_categorized(doc)
    c = _editor(monkeypatch, "cat-filter@example.com")

    # По значению категории «Прочее» неотличимо: две аннотации.
    r = c.get(f"/api/remarks?docId={doc}&category=other")
    assert r.status_code == 200
    assert r.json()["total"] == 2

    # По источнику — ровно одна неразобранная. Это и есть очередь приёмки.
    r = c.get(f"/api/remarks?docId={doc}&categorySource=default")
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["remarkId"] == "ann-untouched"

    r = c.get(f"/api/remarks?docId={doc}&categorySource=agent")
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["remarkId"] == "ann-by-agent"


def test_bad_category_and_source_are_rejected(monkeypatch):
    c = _editor(monkeypatch, "cat-bad@example.com")
    assert c.get("/api/remarks?category=nonsense").status_code == 400
    assert c.get("/api/remarks?categorySource=oracle").status_code == 400


# ---------------------------------------------------------------------------
# GET /api/remarks/{docId}/{pageKey}/{remarkId} — вход карточки комментария
# ---------------------------------------------------------------------------


def test_single_remark_returns_everything_the_card_needs(monkeypatch):
    doc = "carddoc"
    db.upsert_remark_db(doc, "006", "ann-1", "major", "текст комментария",
                            coord_x=10, coord_y=20, action="create",
                            category="omission", author_id=1, tags=["framing"])
    db.replace_sections(doc, [{"sectionId": "1", "title": "§ 1. Раздел",
                               "pageStart": 6, "pageEnd": 20}])
    c = _editor(monkeypatch, "card-one@example.com")

    r = c.get(f"/api/remarks/{doc}/006/ann-1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["remark"]["text"] == "текст комментария"
    assert body["remark"]["category"] == "omission"
    assert body["remark"]["categorySource"] == "human"
    assert body["remark"]["tags"] == ["framing"]
    assert body["remark"]["coordX"] == 10
    # Параграф нужен карточке для навигации и заголовка.
    assert body["section"]["sectionId"] == "1"


def test_single_remark_404_when_missing(monkeypatch):
    c = _editor(monkeypatch, "card-missing@example.com")
    assert c.get("/api/remarks/carddoc/006/нет-такого").status_code == 404


def test_single_remark_requires_editor(monkeypatch):
    db.upsert_remark_db("carddoc2", "006", "ann-1", "major", "t", action="create")
    assert anon().get("/api/remarks/carddoc2/006/ann-1").status_code == 401
    viewer = _login_google(monkeypatch, "card-viewer-sub")
    assert viewer.get("/api/remarks/carddoc2/006/ann-1").status_code == 403


def test_page_outside_any_section_is_not_an_error(monkeypatch):
    db.upsert_remark_db("carddoc3", "000", "ann-cover", "major", "обложка",
                            action="create")
    c = _editor(monkeypatch, "card-front@example.com")
    r = c.get("/api/remarks/carddoc3/000/ann-cover")
    assert r.status_code == 200
    assert r.json()["section"] is None
