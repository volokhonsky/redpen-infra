"""
Tests for Google Sign-In, roles, and the admin allowlist API (stage 1).

``main.verify_google_token`` is monkeypatched per-test instead of hitting
Google, per docs/agent-instructions-stage-0-1.md section 1.7.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import config  # noqa: E402
import main  # noqa: E402


@pytest.fixture(autouse=True)
def _google_configured(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setattr(config, "ADMIN_EMAILS", [])


def _mock_verify(monkeypatch, claims_or_exc):
    def fake_verify(credential):
        if isinstance(claims_or_exc, Exception):
            raise claims_or_exc
        return claims_or_exc
    monkeypatch.setattr(main, "verify_google_token", fake_verify)


def _claims(email, sub=None, name=None, verified=True, picture="http://pic"):
    return {
        "sub": sub or ("sub-" + email),
        "email": email,
        "email_verified": verified,
        "name": name or email.split("@")[0],
        "picture": picture,
    }


def _google_login(monkeypatch, email, **kwargs) -> TestClient:
    _mock_verify(monkeypatch, _claims(email, **kwargs))
    c = TestClient(main.app)
    r = c.post("/api/auth/google", json={"credential": "fake"})
    assert r.status_code == 200, r.text
    return c, r.json()


def _with_csrf(c: TestClient) -> TestClient:
    r = c.get("/api/auth/csrf")
    assert r.status_code == 200
    c.headers.update({"X-CSRF-Token": r.json()["csrfToken"]})
    return c


# ---------------------------------------------------------------------------
# Google login: user creation, idempotency, email_verified, bad credential
# ---------------------------------------------------------------------------

def test_google_login_creates_new_viewer(monkeypatch):
    c, data = _google_login(monkeypatch, "alice@example.com")
    assert data["role"] == "viewer"
    assert data["email"] == "alice@example.com"
    assert isinstance(data["userId"], int)


def test_google_login_is_idempotent_by_sub(monkeypatch):
    c1, data1 = _google_login(monkeypatch, "bob@example.com", sub="sub-bob")
    c2, data2 = _google_login(monkeypatch, "bob@example.com", sub="sub-bob")
    assert data1["userId"] == data2["userId"]


def test_google_login_email_not_verified_is_401(monkeypatch):
    _mock_verify(monkeypatch, _claims("carol@example.com", verified=False))
    c = TestClient(main.app)
    r = c.post("/api/auth/google", json={"credential": "fake"})
    assert r.status_code == 401


def test_google_login_invalid_credential_is_401(monkeypatch):
    _mock_verify(monkeypatch, ValueError("bad token"))
    c = TestClient(main.app)
    r = c.post("/api/auth/google", json={"credential": "not-a-jwt"})
    assert r.status_code == 401


def test_google_login_missing_credential_is_400(monkeypatch):
    c = TestClient(main.app)
    r = c.post("/api/auth/google", json={})
    assert r.status_code == 400


def test_google_client_id_unset_is_503(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "")
    c = TestClient(main.app)
    r = c.post("/api/auth/google", json={"credential": "fake"})
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# Roles: ADMIN_EMAILS, allowlist, viewer/editor permission checks
# ---------------------------------------------------------------------------

def test_admin_emails_grants_admin_role(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_EMAILS", ["admin@example.com"])
    c, data = _google_login(monkeypatch, "admin@example.com")
    assert data["role"] == "admin"


def test_allowlist_grants_editor_role(monkeypatch):
    import db
    db.upsert_allowlist("editor@example.com", "editor", "admin@example.com")
    c, data = _google_login(monkeypatch, "editor@example.com")
    assert data["role"] == "editor"


def test_viewer_cannot_write_editor_annotation(monkeypatch):
    c, _ = _google_login(monkeypatch, "viewer@example.com")
    _with_csrf(c)
    r = c.post(
        "/api/editor/medinsky11klass/50",
        json={"annType": "comment", "text": "x", "coords": [1, 1]},
    )
    assert r.status_code == 403


def test_editor_can_write_annotation(monkeypatch):
    import db
    db.upsert_allowlist("editor2@example.com", "editor", "admin@example.com")
    c, data = _google_login(monkeypatch, "editor2@example.com")
    assert data["role"] == "editor"
    _with_csrf(c)
    r = c.post(
        "/api/editor/medinsky11klass/51",
        json={"annType": "comment", "text": "x", "coords": [1, 1]},
    )
    assert r.status_code == 200


def test_viewer_cannot_view_logs(monkeypatch):
    c, _ = _google_login(monkeypatch, "viewer2@example.com")
    assert c.get("/logs").status_code == 403
    assert c.get("/api/logs").status_code == 403


def test_admin_can_view_logs(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_EMAILS", ["root@example.com"])
    c, _ = _google_login(monkeypatch, "root@example.com")
    assert c.get("/logs").status_code == 200
    assert c.get("/api/logs").status_code == 200


# ---------------------------------------------------------------------------
# Logout / session expiry
# ---------------------------------------------------------------------------

def test_logout_then_me_is_401(monkeypatch):
    c, _ = _google_login(monkeypatch, "dana@example.com")
    assert c.get("/api/auth/me").status_code == 200
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/auth/me").status_code == 401


def test_expired_session_is_401(monkeypatch):
    import db
    from datetime import datetime, timedelta

    c, _ = _google_login(monkeypatch, "erin@example.com")
    session_id = c.cookies.get("redpen_session")
    conn = db.get_connection()
    past = (datetime.utcnow() - timedelta(seconds=1)).isoformat()
    conn.execute("UPDATE sessions SET expires_at = ? WHERE id = ?", (past, session_id))
    conn.commit()

    assert c.get("/api/auth/me").status_code == 401


# ---------------------------------------------------------------------------
# Admin allowlist CRUD
# ---------------------------------------------------------------------------

def test_allowlist_get_requires_admin(monkeypatch):
    viewer, _ = _google_login(monkeypatch, "plainviewer@example.com")
    assert viewer.get("/api/admin/allowlist").status_code == 403

    monkeypatch.setattr(config, "ADMIN_EMAILS", ["boss@example.com"])
    admin, _ = _google_login(monkeypatch, "boss@example.com")
    assert admin.get("/api/admin/allowlist").status_code == 200


def test_allowlist_post_requires_admin_and_csrf(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_EMAILS", ["boss2@example.com"])
    admin, _ = _google_login(monkeypatch, "boss2@example.com")

    # No CSRF header yet -> 403.
    r = admin.post("/api/admin/allowlist", json={"email": "new@example.com", "role": "editor"})
    assert r.status_code == 403

    _with_csrf(admin)
    r = admin.post("/api/admin/allowlist", json={"email": "new@example.com", "role": "editor"})
    assert r.status_code == 200
    emails = [e["email"] for e in r.json()["allowlist"]]
    assert "new@example.com" in emails


def test_allowlist_change_takes_effect_on_next_login(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_EMAILS", ["boss3@example.com"])
    admin, _ = _google_login(monkeypatch, "boss3@example.com")
    _with_csrf(admin)

    # First login before being added to the allowlist -> viewer.
    _, before = _google_login(monkeypatch, "promoted@example.com")
    assert before["role"] == "viewer"

    r = admin.post("/api/admin/allowlist", json={"email": "promoted@example.com", "role": "editor"})
    assert r.status_code == 200

    _, after = _google_login(monkeypatch, "promoted@example.com")
    assert after["role"] == "editor"


def test_allowlist_delete(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_EMAILS", ["boss4@example.com"])
    admin, _ = _google_login(monkeypatch, "boss4@example.com")
    _with_csrf(admin)

    admin.post("/api/admin/allowlist", json={"email": "temp@example.com", "role": "editor"})
    r = admin.delete("/api/admin/allowlist/temp@example.com")
    assert r.status_code == 200
    emails = [e["email"] for e in r.json()["allowlist"]]
    assert "temp@example.com" not in emails

    r = admin.delete("/api/admin/allowlist/temp@example.com")
    assert r.status_code == 404


def test_allowlist_post_viewer_forbidden(monkeypatch):
    viewer, _ = _google_login(monkeypatch, "notadmin@example.com")
    _with_csrf(viewer)
    r = viewer.post("/api/admin/allowlist", json={"email": "x@example.com", "role": "editor"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/admin/publish-all (stage 2, A2.5)
# ---------------------------------------------------------------------------

def test_publish_all_requires_admin(monkeypatch):
    import db

    viewer, _ = _google_login(monkeypatch, "publish-viewer@example.com")
    _with_csrf(viewer)
    assert viewer.post("/api/admin/publish-all").status_code == 403

    monkeypatch.setattr(config, "ADMIN_EMAILS", ["publish-boss@example.com"])
    db.upsert_allowlist("publish-editor@example.com", "editor", "publish-boss@example.com")
    editor, _ = _google_login(monkeypatch, "publish-editor@example.com")
    _with_csrf(editor)
    assert editor.post("/api/admin/publish-all").status_code == 403


def test_publish_all_writes_files_for_admin(monkeypatch):
    import os

    import db

    db.upsert_annotation_db("publishalldoc", "006", "ann-1", "comment", "hi")

    monkeypatch.setattr(config, "ADMIN_EMAILS", ["publish-admin@example.com"])
    admin, _ = _google_login(monkeypatch, "publish-admin@example.com")
    _with_csrf(admin)

    r = admin.post("/api/admin/publish-all")
    assert r.status_code == 200
    body = r.json()
    assert body["pages"] >= 1
    assert body["failed"] == 0

    path = os.path.join(config.PUBLISH_DIR, "publishalldoc", "annotations", "page_006.json")
    assert os.path.exists(path)
