"""
Tests for the FastAPI service in ``scripts/api`` driven through Starlette's
``TestClient`` (no running server or Docker required).

Storage and log directories are redirected to a temp dir by ``conftest.py``,
so these tests never touch real data. The one endpoint with repo side effects
(``/api/rebuild``) is exercised only on its validation/404 paths, which do not
write anything.
"""

import pytest

# Skip the whole module gracefully if the optional web stack is missing.
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("jinja2")

from fastapi.testclient import TestClient

import main  # noqa: E402  (imported after conftest sets env vars)


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


# ---------------------------------------------------------------------------
# Basic / smoke endpoints
# ---------------------------------------------------------------------------

def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_hello(client):
    r = client.get("/api/hello")
    assert r.status_code == 200
    body = r.json()
    assert body["message"] == "Hello, RedPen!"
    assert "version" in body and "now" in body


# ---------------------------------------------------------------------------
# /api/store
# ---------------------------------------------------------------------------

def test_store_valid_object(client):
    r = client.post("/api/store", json={"foo": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "stored"
    assert body["path"].startswith("inbox/")


def test_store_rejects_non_object(client):
    r = client.post("/api/store", json=[1, 2, 3])
    assert r.status_code == 400


def test_store_rejects_invalid_json(client):
    r = client.post(
        "/api/store",
        content="not-json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /api/store-raw (bucket / pageId sanitization)
# ---------------------------------------------------------------------------

def test_store_raw_without_bucket(client):
    r = client.post("/api/store-raw", json={"foo": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["stored"] is True
    assert body["bucket"] is None
    assert body["relPath"] == f"inbox/{body['dateDir']}/{body['id']}.json"


def test_store_raw_with_bucket_is_sanitized(client):
    r = client.post("/api/store-raw", json={"bucket": "Editor Drafts", "msg": "hi"})
    assert r.status_code == 200
    body = r.json()
    assert body["bucket"] == "editor-drafts"
    assert "/editor-drafts/" in body["relPath"]


def test_store_raw_with_page_id_is_sanitized(client):
    r = client.post(
        "/api/store-raw",
        json={"pageId": "book:medinsky11klass/page.007", "text": "ok"},
    )
    assert r.status_code == 200
    body = r.json()
    # ':' and '.' become '-' for pageId-derived buckets
    assert body["bucket"] == "book-medinsky11klass/page-007"


def test_store_raw_bucket_takes_priority_over_page_id(client):
    r = client.post(
        "/api/store-raw",
        json={"bucket": "drafts", "pageId": "should-be-ignored", "x": 1},
    )
    assert r.status_code == 200
    assert r.json()["bucket"] == "drafts"


# ---------------------------------------------------------------------------
# /api/editor GET + validation
# ---------------------------------------------------------------------------

def test_editor_get_returns_default_page(client):
    r = client.get("/api/editor/medinsky11klass/12")
    assert r.status_code == 200
    page = r.json()
    assert page["pageId"] == "medinsky11klass_page_012"
    assert page["annotations"] == []
    assert page["serverPageSha"]  # computed & persisted on GET


@pytest.mark.parametrize("doc_id", ["Bad_Doc", "has space", "UPPER"])
def test_editor_get_invalid_doc_id(client, doc_id):
    r = client.get(f"/api/editor/{doc_id}/7")
    assert r.status_code == 400


@pytest.mark.parametrize("page_num", ["0", "1000", "abc", "-1"])
def test_editor_get_invalid_page_num(client, page_num):
    r = client.get(f"/api/editor/medinsky11klass/{page_num}")
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /api/editor POST/PUT round-trip
# ---------------------------------------------------------------------------

def test_editor_post_creates_annotation_and_persists(client):
    doc, page = "medinsky11klass", "21"
    payload = {"annType": "comment", "text": "hello", "coords": [100, 200]}
    r = client.post(f"/api/editor/{doc}/{page}", json=payload)
    assert r.status_code == 200
    created = r.json()
    assert created["id"].startswith("srv-")
    assert created["serverPageSha"]

    # GET should now return the persisted annotation.
    page_data = client.get(f"/api/editor/{doc}/{page}").json()
    ids = [a["id"] for a in page_data["annotations"]]
    assert created["id"] in ids
    ann = next(a for a in page_data["annotations"] if a["id"] == created["id"])
    assert ann["annType"] == "comment"
    assert ann["text"] == "hello"
    assert ann["coords"] == [100, 200]


def test_editor_post_general_annotation_without_coords(client):
    r = client.post(
        "/api/editor/medinsky11klass/22",
        json={"annType": "general", "text": "overview"},
    )
    assert r.status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        {"text": "no type"},                                  # missing annType
        {"annType": "comment"},                               # missing text
        {"annType": "", "text": "x"},                         # empty annType
        {"annType": "comment", "text": "x", "coords": [1]},   # bad coords
        {"annType": "comment", "text": "x", "coords": ["a", "b"]},
    ],
)
def test_editor_post_invalid_body(client, payload):
    r = client.post("/api/editor/medinsky11klass/23", json=payload)
    assert r.status_code == 400


def test_editor_put_updates_existing_annotation(client):
    doc, page = "medinsky11klass", "24"
    created = client.post(
        f"/api/editor/{doc}/{page}",
        json={"annType": "comment", "text": "first", "coords": [1, 2]},
    ).json()
    ann_id = created["id"]

    r = client.put(
        f"/api/editor/{doc}/{page}/{ann_id}",
        json={"annType": "main", "text": "updated", "coords": [3, 4]},
    )
    assert r.status_code == 200
    assert r.json()["id"] == ann_id

    page_data = client.get(f"/api/editor/{doc}/{page}").json()
    anns = [a for a in page_data["annotations"] if a["id"] == ann_id]
    assert len(anns) == 1  # updated in place, not duplicated
    assert anns[0]["text"] == "updated"
    assert anns[0]["annType"] == "main"


def test_editor_put_upserts_when_id_missing(client):
    doc, page = "medinsky11klass", "25"
    r = client.put(
        f"/api/editor/{doc}/{page}/does-not-exist",
        json={"annType": "comment", "text": "new via put", "coords": [5, 6]},
    )
    assert r.status_code == 200
    page_data = client.get(f"/api/editor/{doc}/{page}").json()
    ids = [a["id"] for a in page_data["annotations"]]
    assert "does-not-exist" in ids


# ---------------------------------------------------------------------------
# /api/rebuild validation (no-write paths only)
# ---------------------------------------------------------------------------

def test_rebuild_invalid_book_slug(client):
    r = client.post("/api/rebuild/Bad Slug/annotations/page_007")
    assert r.status_code == 400


def test_rebuild_invalid_page_id(client):
    r = client.post("/api/rebuild/medinsky11klass/annotations/page_7")
    assert r.status_code == 400


def test_rebuild_missing_markdown(client):
    r = client.post("/api/rebuild/nonexistentbook/annotations/page_999")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_login_with_valid_token_sets_session(client):
    r = client.post("/api/auth/login", json={"token": "dev-token-123"})
    assert r.status_code == 200
    assert r.json()["username"] == "john_doe"
    assert "redpen_session" in r.cookies


def test_login_with_invalid_token(client):
    r = client.post("/api/auth/login", json={"token": "nope"})
    assert r.status_code == 401


def test_login_with_empty_token(client):
    r = client.post("/api/auth/login", json={"token": ""})
    assert r.status_code == 401


def test_auth_me_requires_session(client):
    fresh = TestClient(main.app)  # no cookies
    r = fresh.get("/api/auth/me")
    assert r.status_code == 401
