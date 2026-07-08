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

import config  # noqa: E402  (imported after conftest sets env vars)
import db  # noqa: E402  (imported after conftest sets env vars)
import main  # noqa: E402  (imported after conftest sets env vars)


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


def test_anonymous_rebuild_is_rejected():
    anon = TestClient(main.app)
    r = anon.post("/api/rebuild/medinsky11klass/annotations/page_007")
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
