"""
Tests for the FastAPI service in ``scripts/api`` driven through Starlette's
``TestClient`` (no running server or Docker required).

Storage, log, and publish directories are redirected to a temp dir by
``conftest.py``, so these tests never touch real data. Stage 2: remarks
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


def _static_remarks(doc_id: str, page_num_str: str):
    """Everything in the published file -- drafts included, since they share it."""
    path = os.path.join(config.PUBLISH_DIR, doc_id, "remarks", f"page_{page_num_str}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _published_remarks(doc_id: str, page_num_str: str):
    """Only what a plain reader sees: the file minus the draft-tagged items."""
    return [a for a in _static_remarks(doc_id, page_num_str) if not a.get("draft")]


def _without_legacy_keys(items):
    """Ответ API дублирует поля прежними именами (`annType`) — это временный
    мостик для клиентов редактора, которые переезжают отдельной выкладкой.
    Сверять содержимое с опубликованным файлом надо без него."""
    return [{k: v for k, v in item.items() if k not in ("annType", "annId")}
            for item in items]


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
    assert page["remarks"] == []
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
    created = client.post(f"/api/editor/{doc}/60", json={"kind": "minor", "text": "via short key", "coords": [1, 1]}).json()

    short_form = client.get(f"/api/editor/{doc}/60").json()
    zfilled_form = client.get(f"/api/editor/{doc}/060").json()
    assert short_form["pageId"] == zfilled_form["pageId"] == f"{doc}_page_060"
    assert [a["id"] for a in short_form["remarks"]] == [a["id"] for a in zfilled_form["remarks"]]
    assert created["id"] in [a["id"] for a in zfilled_form["remarks"]]


@pytest.mark.parametrize("page_key", ["000", "-01"])
def test_nonstandard_page_keys_support_full_crud_and_publish(client, page_key):
    doc = "medinsky11klass"

    created = client.post(f"/api/editor/{doc}/{page_key}", json={"kind": "minor", "text": "front matter", "coords": [1, 1]})
    assert created.status_code == 200
    remark_id = created.json()["id"]

    updated = client.put(f"/api/editor/{doc}/{page_key}/{remark_id}", json={"kind": "major", "text": "updated", "coords": [2, 2]})
    assert updated.status_code == 200

    published = _published_remarks(doc, page_key)
    assert published == [{
        "id": remark_id, "text": "updated", "kind": "major", "coords": [2, 2],
        # Категория есть у каждой аннотации; по умолчанию — «Прочее».
        "category": "other",
    }]

    deleted = client.delete(f"/api/editor/{doc}/{page_key}/{remark_id}")
    assert deleted.status_code == 200
    assert _published_remarks(doc, page_key) == []


# ---------------------------------------------------------------------------
# /api/editor POST/PUT round-trip
# ---------------------------------------------------------------------------

def test_editor_post_creates_remark_and_persists(client):
    doc, page = "medinsky11klass", "21"
    payload = {"kind": "minor", "text": "hello", "coords": [100, 200]}
    r = client.post(f"/api/editor/{doc}/{page}", json=payload)
    assert r.status_code == 200
    created = r.json()
    assert created["id"].startswith("srv-")
    assert created["serverPageSha"]
    assert created["published"] is True

    # GET should now return the persisted remark.
    page_data = client.get(f"/api/editor/{doc}/{page}").json()
    ids = [a["id"] for a in page_data["remarks"]]
    assert created["id"] in ids
    ann = next(a for a in page_data["remarks"] if a["id"] == created["id"])
    assert ann["kind"] == "minor"
    assert ann["text"] == "hello"
    assert ann["coords"] == [100, 200]

    # The published static snapshot matches the DB-backed GET response.
    published = _published_remarks(doc, "021")
    assert published == _without_legacy_keys(page_data["remarks"])


def test_editor_post_rejects_retired_general_type(client):
    """`general` (общий комментарий к странице) is retired: it had no anchor on
    the scan. Old clients must fail loudly rather than create an remark the
    viewer cannot place."""
    r = client.post(
        "/api/editor/medinsky11klass/22",
        json={"kind": "general", "text": "overview"},
    )
    assert r.status_code == 400


def test_editor_post_rejects_unknown_type(client):
    r = client.post(
        "/api/editor/medinsky11klass/22",
        json={"kind": "footnote", "text": "что-то"},
    )
    assert r.status_code == 400


@pytest.mark.parametrize(
    "payload",
    [
        {"text": "no type"},                                  # missing kind
        {"kind": "minor"},                               # missing text
        {"kind": "", "text": "x"},                         # empty kind
        {"kind": "minor", "text": "x", "coords": [1]},   # bad coords
        {"kind": "minor", "text": "x", "coords": ["a", "b"]},
    ],
)
def test_editor_post_invalid_body(client, payload):
    r = client.post("/api/editor/medinsky11klass/23", json=payload)
    assert r.status_code == 400


def test_editor_put_updates_existing_remark(client):
    doc, page = "medinsky11klass", "24"
    created = client.post(
        f"/api/editor/{doc}/{page}",
        json={"kind": "minor", "text": "first", "coords": [1, 2]},
    ).json()
    remark_id = created["id"]

    r = client.put(
        f"/api/editor/{doc}/{page}/{remark_id}",
        json={"kind": "major", "text": "updated", "coords": [3, 4]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == remark_id
    assert body["published"] is True

    page_data = client.get(f"/api/editor/{doc}/{page}").json()
    anns = [a for a in page_data["remarks"] if a["id"] == remark_id]
    assert len(anns) == 1  # updated in place, not duplicated
    assert anns[0]["text"] == "updated"
    assert anns[0]["kind"] == "major"

    published = _published_remarks(doc, "024")
    assert published == _without_legacy_keys(page_data["remarks"])


def test_editor_put_upserts_when_id_missing(client):
    doc, page = "medinsky11klass", "25"
    r = client.put(
        f"/api/editor/{doc}/{page}/does-not-exist",
        json={"kind": "minor", "text": "new via put", "coords": [5, 6]},
    )
    assert r.status_code == 200
    page_data = client.get(f"/api/editor/{doc}/{page}").json()
    ids = [a["id"] for a in page_data["remarks"]]
    assert "does-not-exist" in ids


# ---------------------------------------------------------------------------
# DELETE /api/editor/{docId}/{pageNum}/{remarkId} -- в архив (status='archived')
# ---------------------------------------------------------------------------

def test_editor_delete_removes_remark_from_get_and_published_file(client):
    doc, page = "medinsky11klass", "50"
    created = client.post(
        f"/api/editor/{doc}/{page}",
        json={"kind": "minor", "text": "to be archived", "coords": [1, 1]},
    ).json()
    remark_id = created["id"]

    r = client.delete(f"/api/editor/{doc}/{page}/{remark_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == remark_id
    assert body["published"] is True

    page_data = client.get(f"/api/editor/{doc}/{page}").json()
    assert remark_id not in [a["id"] for a in page_data["remarks"]]

    published = _published_remarks(doc, "050")
    assert remark_id not in [a["id"] for a in published]


def test_editor_delete_missing_remark_is_404(client):
    r = client.delete("/api/editor/medinsky11klass/51/does-not-exist")
    assert r.status_code == 404


def test_editor_delete_already_archived_is_404(client):
    doc, page = "medinsky11klass", "52"
    created = client.post(
        f"/api/editor/{doc}/{page}",
        json={"kind": "minor", "text": "one shot", "coords": [1, 1]},
    ).json()
    remark_id = created["id"]

    assert client.delete(f"/api/editor/{doc}/{page}/{remark_id}").status_code == 200
    assert client.delete(f"/api/editor/{doc}/{page}/{remark_id}").status_code == 404


def test_anonymous_delete_editor_remark_is_rejected():
    anon = TestClient(main.app)
    r = anon.delete("/api/editor/medinsky11klass/53/some-id")
    assert r.status_code == 401


def test_startup_self_heals_publish_dir():
    # An remark exists in the DB but PUBLISH_DIR was wiped (simulating a
    # fresh/recreated container) -- the startup hook's publish_all() should
    # restore it without any explicit publish-all call.
    db.upsert_remark_db("startupdoc", "006", "ann-1", "minor", "restored on boot")
    path = os.path.join(config.PUBLISH_DIR, "startupdoc", "remarks", "page_006.json")
    if os.path.exists(path):
        os.remove(path)

    with TestClient(main.app):
        pass  # entering the context fires the startup event

    # Восстановление тома идёт в отдельном потоке — старт его больше не ждёт
    # (обход девятисот страниц до готовности API выглядел как недоступность
    # сервиса). Тесту дождаться нужно.
    assert main._startup_publish_thread is not None
    main._startup_publish_thread.join(timeout=30)

    assert os.path.exists(path)


def test_viewer_delete_editor_remark_is_forbidden(client):
    # Роль приезжает с приглашением; система знает про участника только хеш
    # его Google `sub` (docs/anonymity-model.md).
    c = TestClient(main.app)
    viewer_user = db.login_with_google_sub(
        "sub-viewer-test-api", invite_code=db.create_invite(role="viewer")[0])
    session_id = db.create_session(viewer_user["id"])
    c.cookies.set("redpen_session", session_id)

    r = c.delete("/api/editor/medinsky11klass/54/some-id")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Write endpoints require an authenticated session (stage 0.3) + CSRF (0.4)
# ---------------------------------------------------------------------------

def test_anonymous_post_editor_remark_is_rejected():
    anon = TestClient(main.app)
    r = anon.post(
        "/api/editor/medinsky11klass/30",
        json={"kind": "minor", "text": "x", "coords": [1, 2]},
    )
    assert r.status_code == 401


def test_anonymous_put_editor_remark_is_rejected():
    anon = TestClient(main.app)
    r = anon.put(
        "/api/editor/medinsky11klass/30/some-id",
        json={"kind": "minor", "text": "x", "coords": [1, 2]},
    )
    assert r.status_code == 401


def test_authenticated_post_editor_remark_succeeds():
    # Full cycle: login -> csrf -> POST -> 200.
    c = TestClient(main.app)
    _login_with_csrf(c)
    r = c.post(
        "/api/editor/medinsky11klass/31",
        json={"kind": "minor", "text": "x", "coords": [1, 2]},
    )
    assert r.status_code == 200


def test_session_without_csrf_header_is_rejected():
    c = TestClient(main.app)
    c.post("/api/auth/login", json={"token": "dev-token-123"})
    r = c.post(
        "/api/editor/medinsky11klass/33",
        json={"kind": "minor", "text": "x", "coords": [1, 2]},
    )
    assert r.status_code == 403


def test_session_with_wrong_csrf_header_is_rejected():
    c = TestClient(main.app)
    _login_with_csrf(c)
    c.headers.update({"X-CSRF-Token": "csrf-wrong-value"})
    r = c.post(
        "/api/editor/medinsky11klass/34",
        json={"kind": "minor", "text": "x", "coords": [1, 2]},
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


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def test_cors_preflight_allows_every_method_the_editor_uses(client, method):
    """Проверка на настоящем приложении, а не на собственноручно собранном.

    Узкие операции (`PATCH .../status|category|tags`) появились позже списка
    методов и в него не попали: браузер получал 400 на предварительный OPTIONS
    и до сервера не доходил вовсе. Стенд это показал, тест не давал показать —
    он строил свой app со своим списком.
    """
    r = client.options(
        "/api/remarks",
        headers={"Origin": "https://medinsky.net",
                 "Access-Control-Request-Method": method},
    )
    assert r.status_code == 200, r.text
    assert method in r.headers.get("access-control-allow-methods", "")


# ---------------------------------------------------------------------------
# Optimistic locking (stage 0.6)
# ---------------------------------------------------------------------------

def test_missing_client_page_sha_is_accepted_transitionally(client):
    doc, page = "medinsky11klass", "40"
    r = client.post(f"/api/editor/{doc}/{page}", json={"kind": "minor", "text": "no sha", "coords": [1, 1]})
    assert r.status_code == 200


def test_stale_client_page_sha_returns_409(client):
    doc, page = "medinsky11klass", "41"

    initial = client.get(f"/api/editor/{doc}/{page}").json()
    stale_sha = initial["serverPageSha"]

    # First writer succeeds and advances serverPageSha.
    first = client.post(
        f"/api/editor/{doc}/{page}",
        json={"kind": "minor", "text": "first", "coords": [1, 2], "clientPageSha": stale_sha},
    )
    assert first.status_code == 200
    assert first.json()["serverPageSha"] != stale_sha

    # Second writer still holds the old sha -> conflict.
    second = client.post(
        f"/api/editor/{doc}/{page}",
        json={"kind": "minor", "text": "second", "coords": [3, 4], "clientPageSha": stale_sha},
    )
    assert second.status_code == 409
    body = second.json()
    assert body["detail"] == "conflict"
    assert body["serverPageSha"] == first.json()["serverPageSha"]

    # Re-reading the page and retrying with the fresh sha succeeds.
    refreshed = client.get(f"/api/editor/{doc}/{page}").json()
    third = client.post(
        f"/api/editor/{doc}/{page}",
        json={"kind": "minor", "text": "second retry", "coords": [3, 4], "clientPageSha": refreshed["serverPageSha"]},
    )
    assert third.status_code == 200


# ---------------------------------------------------------------------------
# Drafts (stage 3, C.2): status field, editor-only visibility
# ---------------------------------------------------------------------------


def test_draft_remark_tagged_in_static_file_and_hidden_from_anonymous_get(client):
    doc, page = "medinsky11klass", "70"
    created = client.post(
        f"/api/editor/{doc}/{page}",
        json={"kind": "minor", "text": "wip", "coords": [1, 1], "status": "draft"},
    )
    assert created.status_code == 200
    assert created.json()["published"] is False
    remark_id = created.json()["id"]

    # The draft ships in the page file, carrying the marker the viewer filters on.
    static = _static_remarks(doc, "070")
    assert [a["id"] for a in static] == [remark_id]
    assert static[0]["draft"] is True
    assert static[0]["tags"] == ["draft"]
    assert _published_remarks(doc, "070") == []

    anon = TestClient(main.app)
    anon_page = anon.get(f"/api/editor/{doc}/{page}").json()
    assert remark_id not in [a["id"] for a in anon_page["remarks"]]

    editor_page = client.get(f"/api/editor/{doc}/{page}").json()
    draft_anns = [a for a in editor_page["remarks"] if a["id"] == remark_id]
    assert len(draft_anns) == 1
    assert draft_anns[0]["draft"] is True


def test_put_without_status_preserves_existing_draft_status(client):
    doc, page = "medinsky11klass", "71"
    created = client.post(
        f"/api/editor/{doc}/{page}",
        json={"kind": "minor", "text": "wip", "coords": [1, 1], "status": "draft"},
    ).json()
    remark_id = created["id"]

    updated = client.put(
        f"/api/editor/{doc}/{page}/{remark_id}",
        json={"kind": "minor", "text": "still wip", "coords": [2, 2]},
    )
    assert updated.status_code == 200
    assert updated.json()["published"] is False
    assert _published_remarks(doc, "071") == []


def test_put_with_status_published_publishes_draft(client):
    doc, page = "medinsky11klass", "72"
    created = client.post(
        f"/api/editor/{doc}/{page}",
        json={"kind": "minor", "text": "wip", "coords": [1, 1], "status": "draft"},
    ).json()
    remark_id = created["id"]

    published = client.put(
        f"/api/editor/{doc}/{page}/{remark_id}",
        json={"kind": "minor", "text": "ready", "coords": [2, 2], "status": "published"},
    )
    assert published.status_code == 200
    assert published.json()["published"] is True
    files = _published_remarks(doc, "072")
    assert [a["id"] for a in files] == [remark_id]
    assert files[0]["text"] == "ready"


def test_invalid_status_value_is_400(client):
    r = client.post(
        "/api/editor/medinsky11klass/73",
        json={"kind": "minor", "text": "x", "coords": [1, 1], "status": "bogus"},
    )
    assert r.status_code == 400


@pytest.mark.parametrize("role", ["anon", "viewer", "editor"])
def test_editor_get_draft_visibility_matrix(role):
    doc, page = "medinsky11klass", "74"
    editor_client = TestClient(main.app)
    _login_with_csrf(editor_client)
    created = editor_client.post(
        f"/api/editor/{doc}/{page}",
        json={"kind": "minor", "text": "wip", "coords": [1, 1], "status": "draft"},
    ).json()
    remark_id = created["id"]

    if role == "anon":
        c = TestClient(main.app)
    elif role == "viewer":
        c = TestClient(main.app)
        viewer_user = db.login_with_google_sub(
            f"sub-viewer-draft-{page}", invite_code=db.create_invite(role="viewer")[0]
        )
        c.cookies.set("redpen_session", db.create_session(viewer_user["id"]))
    else:
        c = editor_client

    page_data = c.get(f"/api/editor/{doc}/{page}").json()
    ids = [a["id"] for a in page_data["remarks"]]
    if role == "editor":
        assert remark_id in ids
    else:
        assert remark_id not in ids


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


def test_post_stores_tags_and_renders_them(client):
    doc, page = "medinsky11klass", "80"
    created = client.post(
        f"/api/editor/{doc}/{page}",
        json={"kind": "minor", "text": "x", "coords": [1, 1], "tags": ["Omission", "framing"]},
    )
    assert created.status_code == 200
    remark_id = created.json()["id"]

    published = _published_remarks(doc, "080")
    assert published[0]["tags"] == ["framing", "omission"]

    editor_page = client.get(f"/api/editor/{doc}/{page}").json()
    assert [a for a in editor_page["remarks"] if a["id"] == remark_id][0]["tags"] == ["framing", "omission"]


def test_put_without_tags_preserves_them(client):
    """The editor UI doesn't send tags yet; saving from it must not wipe them."""
    doc, page = "medinsky11klass", "81"
    remark_id = client.post(
        f"/api/editor/{doc}/{page}",
        json={"kind": "minor", "text": "x", "coords": [1, 1], "tags": ["omission"]},
    ).json()["id"]

    client.put(
        f"/api/editor/{doc}/{page}/{remark_id}",
        json={"kind": "minor", "text": "edited", "coords": [2, 2]},
    )
    assert _published_remarks(doc, "081")[0]["tags"] == ["omission"]


def test_put_with_empty_tags_clears_them(client):
    doc, page = "medinsky11klass", "82"
    remark_id = client.post(
        f"/api/editor/{doc}/{page}",
        json={"kind": "minor", "text": "x", "coords": [1, 1], "tags": ["omission"]},
    ).json()["id"]

    client.put(
        f"/api/editor/{doc}/{page}/{remark_id}",
        json={"kind": "minor", "text": "x", "coords": [1, 1], "tags": []},
    )
    assert "tags" not in _published_remarks(doc, "082")[0]


@pytest.mark.parametrize("tags", [["draft"], ["published"], ["deleted"], ["has space"], ["кириллица"], "omission"])
def test_reserved_or_malformed_tags_are_400(client, tags):
    r = client.post(
        "/api/editor/medinsky11klass/83",
        json={"kind": "minor", "text": "x", "coords": [1, 1], "tags": tags},
    )
    assert r.status_code == 400


def test_editor_page_does_not_expose_draft_as_a_tag(client):
    """A draft's flag must stay a boolean: the editor echoes fields back on
    save, and a "draft" entry in tags would come back as a 400."""
    doc, page = "medinsky11klass", "84"
    remark_id = client.post(
        f"/api/editor/{doc}/{page}",
        json={"kind": "minor", "text": "wip", "coords": [1, 1], "status": "draft", "tags": ["omission"]},
    ).json()["id"]

    item = [a for a in client.get(f"/api/editor/{doc}/{page}").json()["remarks"] if a["id"] == remark_id][0]
    assert item["draft"] is True
    assert item["tags"] == ["omission"]

    # Round-tripping exactly what the editor got back must still save.
    resave = client.put(
        f"/api/editor/{doc}/{page}/{remark_id}",
        json={"kind": item["kind"], "text": item["text"], "coords": item["coords"], "tags": item["tags"]},
    )
    assert resave.status_code == 200


def test_tags_endpoint_lists_vocabulary_with_counts(client):
    doc = "medinsky11klass"
    # Tag names unique to this test: the DB is shared across the module.
    client.post(f"/api/editor/{doc}/85", json={"kind": "minor", "text": "a", "coords": [1, 1], "tags": ["vocab-a", "vocab-b"]})
    client.post(f"/api/editor/{doc}/86", json={"kind": "minor", "text": "b", "coords": [1, 1], "tags": ["vocab-a"]})

    tags = {t["tag"]: t["count"] for t in client.get(f"/api/tags?docId={doc}").json()["tags"]}
    assert tags["vocab-a"] == 2
    assert tags["vocab-b"] == 1


def test_remarks_list_filters_by_tag(client):
    doc = "medinsky11klass"
    tagged = client.post(f"/api/editor/{doc}/87", json={"kind": "minor", "text": "a", "coords": [1, 1], "tags": ["euphemism"]}).json()["id"]
    client.post(f"/api/editor/{doc}/88", json={"kind": "minor", "text": "b", "coords": [1, 1]})

    r = client.get(f"/api/remarks?docId={doc}&tag=euphemism").json()
    assert [i["remarkId"] for i in r["items"]] == [tagged]
    assert r["total"] == 1


def test_revert_of_pre_tags_snapshot_leaves_tags_alone(client):
    """History snapshots written before tags existed have no "tags" key;
    reverting to one must not clear the remark's current tags."""
    doc, page = "medinsky11klass", "89"
    remark_id = client.post(
        f"/api/editor/{doc}/{page}", json={"kind": "minor", "text": "v1", "coords": [1, 1]}
    ).json()["id"]

    # Simulate a legacy record: a snapshot lacking the tags key.
    legacy = db.get_remark(doc, "089", remark_id)
    legacy.pop("tags")
    db.add_history(doc, "089", remark_id, "update", legacy, None)
    hist_id = db.list_history(doc_id=doc, page_num="089", remark_id=remark_id, limit=1)[0]["id"]

    client.put(
        f"/api/editor/{doc}/{page}/{remark_id}",
        json={"kind": "minor", "text": "v2", "coords": [1, 1], "tags": ["omission"]},
    )
    r = client.post(f"/api/history/{hist_id}/revert")
    assert r.status_code == 200
    assert _published_remarks(doc, "089")[0]["tags"] == ["omission"]


def test_stale_client_page_sha_returns_409_on_put(client):
    doc, page = "medinsky11klass", "42"
    created = client.post(
        f"/api/editor/{doc}/{page}",
        json={"kind": "minor", "text": "first", "coords": [1, 2]},
    ).json()
    remark_id = created["id"]
    stale_sha = created["serverPageSha"]

    # Someone else updates the page, advancing serverPageSha.
    client.post(f"/api/editor/{doc}/{page}", json={"kind": "minor", "text": "other", "coords": [9, 9]})

    r = client.put(
        f"/api/editor/{doc}/{page}/{remark_id}",
        json={"kind": "major", "text": "updated", "coords": [3, 4], "clientPageSha": stale_sha},
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "conflict"


# ===== КАТЕГОРИИ =====
# Категория — своё поле, ровно одно на аннотацию, по умолчанию «Прочее».
# Теги описывают, что не так с фрагментом; категория — каким одним приёмом.


def test_new_remark_defaults_to_other(client):
    doc, page = "medinsky11klass", "401"
    created = client.post(
        f"/api/editor/{doc}/{page}",
        json={"kind": "minor", "text": "x", "coords": [1, 1]},
    ).json()
    published = _published_remarks(doc, "401")[0]
    assert published["category"] == "other"
    # Дефолтную категорию тегом не зеркалим — она стояла бы на всех сразу.
    assert "tags" not in published
    assert created["published"] is True


def test_post_accepts_category_and_mirrors_it_as_tag(client):
    doc, page = "medinsky11klass", "402"
    client.post(
        f"/api/editor/{doc}/{page}",
        json={"kind": "minor", "text": "x", "coords": [1, 1],
              "category": "today", "tags": ["anachronism"]},
    )
    published = _published_remarks(doc, "402")[0]
    assert published["category"] == "today"
    assert published["tags"] == ["anachronism", "cat:today"]


def test_put_without_category_preserves_it(client):
    """У редактора нет UI категорий; обычное сохранение текста не должно
    сбрасывать категорию в «Прочее»."""
    doc, page = "medinsky11klass", "403"
    remark_id = client.post(
        f"/api/editor/{doc}/{page}",
        json={"kind": "minor", "text": "x", "coords": [1, 1], "category": "sides"},
    ).json()["id"]

    client.put(
        f"/api/editor/{doc}/{page}/{remark_id}",
        json={"kind": "minor", "text": "edited", "coords": [2, 2]},
    )
    assert _published_remarks(doc, "403")[0]["category"] == "sides"


def test_put_can_change_category(client):
    doc, page = "medinsky11klass", "404"
    remark_id = client.post(
        f"/api/editor/{doc}/{page}",
        json={"kind": "minor", "text": "x", "coords": [1, 1], "category": "sides"},
    ).json()["id"]

    client.put(
        f"/api/editor/{doc}/{page}/{remark_id}",
        json={"kind": "minor", "text": "x", "coords": [1, 1], "category": "omission"},
    )
    published = _published_remarks(doc, "404")[0]
    assert published["category"] == "omission"
    assert published["tags"] == ["cat:omission"]


def test_unknown_category_is_rejected(client):
    doc, page = "medinsky11klass", "405"
    r = client.post(
        f"/api/editor/{doc}/{page}",
        json={"kind": "minor", "text": "x", "coords": [1, 1], "category": "propaganda"},
    )
    assert r.status_code == 400
    assert "propaganda" in r.json()["detail"]


def test_cat_tag_cannot_be_authored(client):
    """Зеркало производное. Если бы `cat:*` принимался тегом, поле и тег
    разъехались бы и снова встал бы вопрос, какой из них главный."""
    doc, page = "medinsky11klass", "406"
    r = client.post(
        f"/api/editor/{doc}/{page}",
        json={"kind": "minor", "text": "x", "coords": [1, 1], "tags": ["cat:today"]},
    )
    assert r.status_code == 400
    assert "category" in r.json()["detail"]


def test_editor_get_exposes_category(client):
    doc, page = "medinsky11klass", "407"
    client.post(
        f"/api/editor/{doc}/{page}",
        json={"kind": "minor", "text": "x", "coords": [1, 1], "category": "evidence"},
    )
    ann = client.get(f"/api/editor/{doc}/{page}").json()["remarks"][0]
    assert ann["category"] == "evidence"


# ---------------------------------------------------------------------------
# Фаза 6 переименования (2026-08-30): переходная совместимость снята.
# Клиенты редактора переведены на remarkId/kind, и API больше не понимает
# прежних имён. Тесты ниже сторожат именно это: шим, вернувшийся по
# невнимательности, снова разведёт два словаря по системе.
# ---------------------------------------------------------------------------

def test_editor_rejects_the_legacy_kind_key(client):
    doc, page = "medinsky11klass", "22"
    r = client.post(f"/api/editor/{doc}/{page}",
                    json={"annType": "main", "text": "старый клиент", "coords": [1, 2]})
    assert r.status_code == 400


def test_editor_rejects_legacy_kind_values(client):
    doc, page = "medinsky11klass", "22"
    r = client.post(f"/api/editor/{doc}/{page}",
                    json={"kind": "main", "text": "старое значение", "coords": [1, 2]})
    assert r.status_code == 400


def test_editor_page_response_carries_only_current_names(client):
    doc, page = "medinsky11klass", "23"
    client.post(f"/api/editor/{doc}/{page}",
                json={"kind": "minor", "text": "x", "coords": [1, 2]})
    body = client.get(f"/api/editor/{doc}/{page}").json()
    assert "annotations" not in body
    item = body["remarks"][0]
    assert item["kind"] == "minor"
    assert "annType" not in item


def test_legacy_list_path_is_gone(client):
    doc, page = "medinsky11klass", "24"
    client.post(f"/api/editor/{doc}/{page}",
                json={"kind": "major", "text": "y", "coords": [1, 2]})
    assert client.get("/api/annotations",
                      params={"docId": doc, "pageKey": page}).status_code == 404
    item = client.get("/api/remarks",
                      params={"docId": doc, "pageKey": page}).json()["items"][0]
    assert item["kind"] == "major"
    assert "annId" not in item and "annType" not in item


def test_list_filter_rejects_old_kind_values(client):
    doc, page = "medinsky11klass", "25"
    client.post(f"/api/editor/{doc}/{page}",
                json={"kind": "major", "text": "z", "coords": [1, 2]})
    r = client.get("/api/remarks",
                   params={"docId": doc, "pageKey": page, "kind": "main"})
    assert r.status_code == 400


def test_history_filter_uses_remark_id_only(client):
    doc, page = "medinsky11klass", "26"
    created = client.post(f"/api/editor/{doc}/{page}",
                          json={"kind": "minor", "text": "h", "coords": [1, 2]}).json()
    # Прежнее имя параметра больше не читается: фильтра нет, значит выдача не
    # сузилась — но и 500 быть не должно, лишние параметры FastAPI игнорирует.
    old = client.get("/api/history", params={"docId": doc, "annId": created["id"]})
    assert old.status_code == 200
    new = client.get("/api/history", params={"docId": doc, "remarkId": created["id"]})
    assert [i["remarkId"] for i in new.json()["items"]] == [created["id"]]

