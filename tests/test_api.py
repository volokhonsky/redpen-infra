"""
Tests for the FastAPI service in ``scripts/api`` driven through Starlette's
``TestClient`` (no running server or Docker required).

Storage, log, and publish directories are redirected to a temp dir by
``conftest.py``, so these tests never touch real data. Stage 2: annotations
live in SQLite (``db.py``); PUBLISH_DIR is a throwaway directory, so tests can
assert that the rendered ``page_NNN.json`` files agree with the DB after each
mutation.
"""

import json
import os

import pytest

# Skip the whole module gracefully if the optional web stack is missing.
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("jinja2")

from fastapi.testclient import TestClient

import config  # noqa: E402  (imported after conftest sets env vars)
import db  # noqa: E402  (imported after conftest sets env vars)
import main  # noqa: E402  (imported after conftest sets env vars)


def _published_annotations(doc_id: str, page_num_str: str):
    path = os.path.join(config.PUBLISH_DIR, doc_id, "annotations", f"page_{page_num_str}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(autouse=True, scope="module")
def _editor_tokens():
    original = dict(config.EDITOR_TOKENS)
    config.EDITOR_TOKENS.clear()
    config.EDITOR_TOKENS.update({"dev-token-123": "john_doe"})
    yield
    config.EDITOR_TOKENS.clear()
    config.EDITOR_TOKENS.update(original)


def _login_with_csrf(c: TestClient, token: str = "dev-token-123") -> None:
    login = c.post("/api/auth/login", json={"token": token})
    assert login.status_code == 200
    csrf = c.get("/api/auth/csrf")
    assert csrf.status_code == 200
    c.headers.update({"X-CSRF-Token": csrf.json()["csrfToken"]})


@pytest.fixture(scope="module")
def client(_editor_tokens):
    # Write endpoints require an authenticated session + CSRF header (stage
    # 0.3/0.4); log in once so the shared client can exercise them without
    # repeating this in every test. Tests that specifically check
    # anonymous/401/403 behavior use their own throwaway TestClient instead.
    with TestClient(main.app) as c:
        _login_with_csrf(c)
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


@pytest.mark.parametrize("page_num", ["abc", "1000", "--1", ""])
def test_editor_get_invalid_page_num(client, page_num):
    r = client.get(f"/api/editor/medinsky11klass/{page_num}")
    assert r.status_code in (400, 404)  # "" hits a different route (404), not the {pageNum} param


# ---------------------------------------------------------------------------
# Page key normalization (stage 2, B.4): "6"/"006" are the same page; "000"
# and "-01" (front-matter pages) validate and go through the full CRUD cycle.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [("6", "006"), ("006", "006"), ("000", "000"), ("-1", "-01"), ("-01", "-01")],
)
def test_validate_page_key_normalizes(raw, expected):
    assert main._validate_page_key(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "1000", "--1", "", "1.5", "1 "])
def test_validate_page_key_rejects_invalid(raw):
    assert main._validate_page_key(raw) is None


def test_page_key_short_and_zfilled_form_are_the_same_page(client):
    doc = "medinsky11klass"
    created = client.post(f"/api/editor/{doc}/60", json={"annType": "comment", "text": "via short key", "coords": [1, 1]}).json()

    short_form = client.get(f"/api/editor/{doc}/60").json()
    zfilled_form = client.get(f"/api/editor/{doc}/060").json()
    assert short_form["pageId"] == zfilled_form["pageId"] == f"{doc}_page_060"
    assert [a["id"] for a in short_form["annotations"]] == [a["id"] for a in zfilled_form["annotations"]]
    assert created["id"] in [a["id"] for a in zfilled_form["annotations"]]


@pytest.mark.parametrize("page_key", ["000", "-01"])
def test_nonstandard_page_keys_support_full_crud_and_publish(client, page_key):
    doc = "medinsky11klass"

    created = client.post(f"/api/editor/{doc}/{page_key}", json={"annType": "comment", "text": "front matter", "coords": [1, 1]})
    assert created.status_code == 200
    ann_id = created.json()["id"]

    updated = client.put(f"/api/editor/{doc}/{page_key}/{ann_id}", json={"annType": "main", "text": "updated", "coords": [2, 2]})
    assert updated.status_code == 200

    published = _published_annotations(doc, page_key)
    assert published == [{"id": ann_id, "text": "updated", "annType": "main", "coords": [2, 2]}]

    deleted = client.delete(f"/api/editor/{doc}/{page_key}/{ann_id}")
    assert deleted.status_code == 200
    assert _published_annotations(doc, page_key) == []


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
    assert created["published"] is True

    # GET should now return the persisted annotation.
    page_data = client.get(f"/api/editor/{doc}/{page}").json()
    ids = [a["id"] for a in page_data["annotations"]]
    assert created["id"] in ids
    ann = next(a for a in page_data["annotations"] if a["id"] == created["id"])
    assert ann["annType"] == "comment"
    assert ann["text"] == "hello"
    assert ann["coords"] == [100, 200]

    # The published static snapshot matches the DB-backed GET response.
    published = _published_annotations(doc, "021")
    assert published == page_data["annotations"]


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
    body = r.json()
    assert body["id"] == ann_id
    assert body["published"] is True

    page_data = client.get(f"/api/editor/{doc}/{page}").json()
    anns = [a for a in page_data["annotations"] if a["id"] == ann_id]
    assert len(anns) == 1  # updated in place, not duplicated
    assert anns[0]["text"] == "updated"
    assert anns[0]["annType"] == "main"

    published = _published_annotations(doc, "024")
    assert published == page_data["annotations"]


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
# DELETE /api/editor/{docId}/{pageNum}/{annId} (stage 2, soft-delete)
# ---------------------------------------------------------------------------

def test_editor_delete_removes_annotation_from_get_and_published_file(client):
    doc, page = "medinsky11klass", "50"
    created = client.post(
        f"/api/editor/{doc}/{page}",
        json={"annType": "comment", "text": "to be deleted", "coords": [1, 1]},
    ).json()
    ann_id = created["id"]

    r = client.delete(f"/api/editor/{doc}/{page}/{ann_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == ann_id
    assert body["published"] is True

    page_data = client.get(f"/api/editor/{doc}/{page}").json()
    assert ann_id not in [a["id"] for a in page_data["annotations"]]

    published = _published_annotations(doc, "050")
    assert ann_id not in [a["id"] for a in published]


def test_editor_delete_missing_annotation_is_404(client):
    r = client.delete("/api/editor/medinsky11klass/51/does-not-exist")
    assert r.status_code == 404


def test_editor_delete_already_deleted_is_404(client):
    doc, page = "medinsky11klass", "52"
    created = client.post(
        f"/api/editor/{doc}/{page}",
        json={"annType": "comment", "text": "one shot", "coords": [1, 1]},
    ).json()
    ann_id = created["id"]

    assert client.delete(f"/api/editor/{doc}/{page}/{ann_id}").status_code == 200
    assert client.delete(f"/api/editor/{doc}/{page}/{ann_id}").status_code == 404


def test_anonymous_delete_editor_annotation_is_rejected():
    anon = TestClient(main.app)
    r = anon.delete("/api/editor/medinsky11klass/53/some-id")
    assert r.status_code == 401


def test_startup_self_heals_publish_dir():
    # An annotation exists in the DB but PUBLISH_DIR was wiped (simulating a
    # fresh/recreated container) -- the startup hook's publish_all() should
    # restore it without any explicit publish-all call.
    db.upsert_annotation_db("startupdoc", "006", "ann-1", "comment", "restored on boot")
    path = os.path.join(config.PUBLISH_DIR, "startupdoc", "annotations", "page_006.json")
    if os.path.exists(path):
        os.remove(path)

    with TestClient(main.app):
        pass  # entering the context fires the startup event

    assert os.path.exists(path)


def test_viewer_delete_editor_annotation_is_forbidden(client):
    # A fresh Google-login user defaults to the viewer role (no allowlist entry).
    # Email must be unique across the whole test session (users.email UNIQUE).
    c = TestClient(main.app)
    viewer_user = db.get_or_create_user_google("sub-viewer-test-api", "viewer-test-api@example.com", "Viewer", None)
    session_id = db.create_session(viewer_user["id"])
    c.cookies.set("redpen_session", session_id)

    r = c.delete("/api/editor/medinsky11klass/54/some-id")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Write endpoints require an authenticated session (stage 0.3) + CSRF (0.4)
# ---------------------------------------------------------------------------

def test_anonymous_post_editor_annotation_is_rejected():
    anon = TestClient(main.app)
    r = anon.post(
        "/api/editor/medinsky11klass/30",
        json={"annType": "comment", "text": "x", "coords": [1, 2]},
    )
    assert r.status_code == 401


def test_anonymous_put_editor_annotation_is_rejected():
    anon = TestClient(main.app)
    r = anon.put(
        "/api/editor/medinsky11klass/30/some-id",
        json={"annType": "comment", "text": "x", "coords": [1, 2]},
    )
    assert r.status_code == 401


def test_authenticated_post_editor_annotation_succeeds():
    # Full cycle: login -> csrf -> POST -> 200.
    c = TestClient(main.app)
    _login_with_csrf(c)
    r = c.post(
        "/api/editor/medinsky11klass/31",
        json={"annType": "comment", "text": "x", "coords": [1, 2]},
    )
    assert r.status_code == 200


def test_session_without_csrf_header_is_rejected():
    c = TestClient(main.app)
    c.post("/api/auth/login", json={"token": "dev-token-123"})
    r = c.post(
        "/api/editor/medinsky11klass/33",
        json={"annType": "comment", "text": "x", "coords": [1, 2]},
    )
    assert r.status_code == 403


def test_session_with_wrong_csrf_header_is_rejected():
    c = TestClient(main.app)
    _login_with_csrf(c)
    c.headers.update({"X-CSRF-Token": "csrf-wrong-value"})
    r = c.post(
        "/api/editor/medinsky11klass/34",
        json={"annType": "comment", "text": "x", "coords": [1, 2]},
    )
    assert r.status_code == 403


def test_csrf_endpoint_requires_session():
    anon = TestClient(main.app)
    r = anon.get("/api/auth/csrf")
    assert r.status_code == 401


def test_anonymous_store_is_rejected():
    anon = TestClient(main.app)
    r = anon.post("/api/store", json={"foo": 1})
    assert r.status_code == 401


def test_anonymous_store_raw_is_rejected():
    anon = TestClient(main.app)
    r = anon.post("/api/store-raw", json={"foo": 1})
    assert r.status_code == 401


def test_anonymous_logs_page_is_rejected():
    anon = TestClient(main.app)
    assert anon.get("/logs").status_code == 401
    assert anon.get("/api/logs").status_code == 401


def test_get_editor_page_remains_public():
    anon = TestClient(main.app)
    r = anon.get("/api/editor/medinsky11klass/32")
    assert r.status_code == 200


def test_get_legacy_pages_remains_public():
    anon = TestClient(main.app)
    r = anon.get("/api/pages/medinsky11klass_page_032")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_login_with_valid_token_sets_session():
    # Fresh client: logging in again on the shared `client` fixture would
    # replace its session and strand its already-issued CSRF token.
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"token": "dev-token-123"})
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


def test_logout_clears_session():
    c = TestClient(main.app)
    c.post("/api/auth/login", json={"token": "dev-token-123"})
    assert c.get("/api/auth/me").status_code == 200

    r = c.post("/api/auth/logout")
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    assert c.get("/api/auth/me").status_code == 401


def test_expired_session_is_rejected():
    c = TestClient(main.app)
    c.post("/api/auth/login", json={"token": "dev-token-123"})
    session_id = c.cookies.get("redpen_session")
    assert session_id

    # Force expiry directly in the DB-backed session store.
    from datetime import datetime, timedelta
    conn = db.get_connection()
    past = (datetime.utcnow() - timedelta(seconds=1)).isoformat()
    conn.execute("UPDATE sessions SET expires_at = ? WHERE id = ?", (past, session_id))
    conn.commit()

    r = c.get("/api/auth/me")
    assert r.status_code == 401
    assert db.get_session(session_id) is None


# ---------------------------------------------------------------------------
# CORS (stage 0.5)
# ---------------------------------------------------------------------------

def test_cors_settings_disables_credentials_for_wildcard():
    origins, allow_credentials = config.cors_settings(["*"])
    assert origins == ["*"]
    assert allow_credentials is False


def test_cors_settings_enables_credentials_for_explicit_origins():
    origins, allow_credentials = config.cors_settings(["https://medinsky.net"])
    assert origins == ["https://medinsky.net"]
    assert allow_credentials is True


def test_cors_preflight_reflects_explicit_origin():
    # main.app is built once with CORS_ALLOW_ORIGINS="*" (set by conftest), so
    # exercise the same middleware setup with an explicit origin directly
    # instead of mutating the shared app.
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    origins, allow_credentials = config.cors_settings(["https://medinsky.net"])
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_credentials,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )

    @app.get("/probe")
    async def probe():
        return {"ok": True}

    with TestClient(app) as c:
        r = c.options(
            "/probe",
            headers={
                "Origin": "https://medinsky.net",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r.headers.get("access-control-allow-origin") == "https://medinsky.net"
        assert r.headers.get("access-control-allow-credentials") == "true"


# ---------------------------------------------------------------------------
# Optimistic locking (stage 0.6)
# ---------------------------------------------------------------------------

def test_missing_client_page_sha_is_accepted_transitionally(client):
    doc, page = "medinsky11klass", "40"
    r = client.post(f"/api/editor/{doc}/{page}", json={"annType": "comment", "text": "no sha", "coords": [1, 1]})
    assert r.status_code == 200


def test_stale_client_page_sha_returns_409(client):
    doc, page = "medinsky11klass", "41"

    initial = client.get(f"/api/editor/{doc}/{page}").json()
    stale_sha = initial["serverPageSha"]

    # First writer succeeds and advances serverPageSha.
    first = client.post(
        f"/api/editor/{doc}/{page}",
        json={"annType": "comment", "text": "first", "coords": [1, 2], "clientPageSha": stale_sha},
    )
    assert first.status_code == 200
    assert first.json()["serverPageSha"] != stale_sha

    # Second writer still holds the old sha -> conflict.
    second = client.post(
        f"/api/editor/{doc}/{page}",
        json={"annType": "comment", "text": "second", "coords": [3, 4], "clientPageSha": stale_sha},
    )
    assert second.status_code == 409
    body = second.json()
    assert body["detail"] == "conflict"
    assert body["serverPageSha"] == first.json()["serverPageSha"]

    # Re-reading the page and retrying with the fresh sha succeeds.
    refreshed = client.get(f"/api/editor/{doc}/{page}").json()
    third = client.post(
        f"/api/editor/{doc}/{page}",
        json={"annType": "comment", "text": "second retry", "coords": [3, 4], "clientPageSha": refreshed["serverPageSha"]},
    )
    assert third.status_code == 200


# ---------------------------------------------------------------------------
# Drafts (stage 3, C.2): status field, editor-only visibility
# ---------------------------------------------------------------------------


def test_draft_annotation_not_in_static_file_or_anonymous_get(client):
    doc, page = "medinsky11klass", "70"
    created = client.post(
        f"/api/editor/{doc}/{page}",
        json={"annType": "comment", "text": "wip", "coords": [1, 1], "status": "draft"},
    )
    assert created.status_code == 200
    assert created.json()["published"] is False
    ann_id = created.json()["id"]

    assert _published_annotations(doc, "070") == []

    anon = TestClient(main.app)
    anon_page = anon.get(f"/api/editor/{doc}/{page}").json()
    assert ann_id not in [a["id"] for a in anon_page["annotations"]]

    editor_page = client.get(f"/api/editor/{doc}/{page}").json()
    draft_anns = [a for a in editor_page["annotations"] if a["id"] == ann_id]
    assert len(draft_anns) == 1
    assert draft_anns[0]["draft"] is True


def test_put_without_status_preserves_existing_draft_status(client):
    doc, page = "medinsky11klass", "71"
    created = client.post(
        f"/api/editor/{doc}/{page}",
        json={"annType": "comment", "text": "wip", "coords": [1, 1], "status": "draft"},
    ).json()
    ann_id = created["id"]

    updated = client.put(
        f"/api/editor/{doc}/{page}/{ann_id}",
        json={"annType": "comment", "text": "still wip", "coords": [2, 2]},
    )
    assert updated.status_code == 200
    assert updated.json()["published"] is False
    assert _published_annotations(doc, "071") == []


def test_put_with_status_published_publishes_draft(client):
    doc, page = "medinsky11klass", "72"
    created = client.post(
        f"/api/editor/{doc}/{page}",
        json={"annType": "comment", "text": "wip", "coords": [1, 1], "status": "draft"},
    ).json()
    ann_id = created["id"]

    published = client.put(
        f"/api/editor/{doc}/{page}/{ann_id}",
        json={"annType": "comment", "text": "ready", "coords": [2, 2], "status": "published"},
    )
    assert published.status_code == 200
    assert published.json()["published"] is True
    files = _published_annotations(doc, "072")
    assert [a["id"] for a in files] == [ann_id]
    assert files[0]["text"] == "ready"


def test_invalid_status_value_is_400(client):
    r = client.post(
        "/api/editor/medinsky11klass/73",
        json={"annType": "comment", "text": "x", "coords": [1, 1], "status": "bogus"},
    )
    assert r.status_code == 400


@pytest.mark.parametrize("role", ["anon", "viewer", "editor"])
def test_editor_get_draft_visibility_matrix(role):
    doc, page = "medinsky11klass", "74"
    editor_client = TestClient(main.app)
    _login_with_csrf(editor_client)
    created = editor_client.post(
        f"/api/editor/{doc}/{page}",
        json={"annType": "comment", "text": "wip", "coords": [1, 1], "status": "draft"},
    ).json()
    ann_id = created["id"]

    if role == "anon":
        c = TestClient(main.app)
    elif role == "viewer":
        c = TestClient(main.app)
        viewer_user = db.get_or_create_user_google(
            f"sub-viewer-draft-{page}", f"viewer-draft-{page}@example.com", "Viewer", None
        )
        c.cookies.set("redpen_session", db.create_session(viewer_user["id"]))
    else:
        c = editor_client

    page_data = c.get(f"/api/editor/{doc}/{page}").json()
    ids = [a["id"] for a in page_data["annotations"]]
    if role == "editor":
        assert ann_id in ids
    else:
        assert ann_id not in ids


def test_stale_client_page_sha_returns_409_on_put(client):
    doc, page = "medinsky11klass", "42"
    created = client.post(
        f"/api/editor/{doc}/{page}",
        json={"annType": "comment", "text": "first", "coords": [1, 2]},
    ).json()
    ann_id = created["id"]
    stale_sha = created["serverPageSha"]

    # Someone else updates the page, advancing serverPageSha.
    client.post(f"/api/editor/{doc}/{page}", json={"annType": "comment", "text": "other", "coords": [9, 9]})

    r = client.put(
        f"/api/editor/{doc}/{page}/{ann_id}",
        json={"annType": "main", "text": "updated", "coords": [3, 4], "clientPageSha": stale_sha},
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "conflict"
