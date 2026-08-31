"""
Опознание участников, роли и выдача доступа приглашениями.

Система знает про человека ровно одно: HMAC его Google `sub`. Email, имя и
аватар не читаются из токена и никуда не сохраняются, а доступ выдаётся
одноразовым кодом, переданным вне системы. Обоснование и модель угрозы —
docs/anonymity-model.md.

``main.verify_google_token`` подменяется в тестах: `credential` трактуется как
`sub`, поэтому разные клиенты различаются просто разными credential.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import config  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402

from _auth_helpers import invite, login, mock_google, with_csrf  # noqa: E402


@pytest.fixture(autouse=True)
def _google_configured(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setattr(config, "BOOTSTRAP_INVITE_CODE", "")


def _login_raw(monkeypatch, sub, code=None):
    mock_google(monkeypatch)
    client = TestClient(main.app)
    body = {"credential": sub}
    if code is not None:
        body["invite"] = code
    return client, client.post("/api/auth/google", json=body)


# ---------------------------------------------------------------------------
# Вход: закрытый круг, приглашение, повторный вход
# ---------------------------------------------------------------------------

def test_login_without_invite_is_refused(monkeypatch):
    # Не «неверный пароль», а «доступ не выдан»: круг участников закрыт.
    _, response = _login_raw(monkeypatch, "stranger")
    assert response.status_code == 403
    assert response.json()["detail"] == "invite required"


def test_invite_admits_and_grants_its_role(monkeypatch):
    _, response = _login_raw(monkeypatch, "newcomer", code=invite("editor"))
    assert response.status_code == 200
    assert response.json()["role"] == "editor"


def test_login_response_carries_no_google_identity(monkeypatch):
    _, response = _login_raw(monkeypatch, "quiet", code=invite("editor"))
    body = response.json()
    assert set(body) == {"userId", "role", "kind", "displayName"}


def test_second_login_needs_no_invite(monkeypatch):
    _, first = _login_raw(monkeypatch, "regular", code=invite("editor"))
    _, again = _login_raw(monkeypatch, "regular")
    assert again.status_code == 200
    assert again.json()["userId"] == first.json()["userId"]


def test_used_invite_does_not_admit_a_second_person(monkeypatch):
    code = invite("editor")
    _login_raw(monkeypatch, "first", code=code)
    _, second = _login_raw(monkeypatch, "second", code=code)
    assert second.status_code == 403


def test_invalid_credential_is_401(monkeypatch):
    def boom(credential):
        raise ValueError("bad token")
    monkeypatch.setattr(main, "verify_google_token", boom)
    client = TestClient(main.app)
    assert client.post("/api/auth/google", json={"credential": "not-a-jwt"}).status_code == 401


def test_missing_credential_is_400(monkeypatch):
    assert TestClient(main.app).post("/api/auth/google", json={}).status_code == 400


def test_google_client_id_unset_is_503(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "")
    assert TestClient(main.app).post(
        "/api/auth/google", json={"credential": "fake"}).status_code == 503


def test_missing_pepper_is_503_not_a_silent_downgrade(monkeypatch):
    monkeypatch.setattr(config, "IDENTITY_PEPPER", "")
    mock_google(monkeypatch)
    response = TestClient(main.app).post(
        "/api/auth/google", json={"credential": "x", "invite": "whatever"})
    assert response.status_code == 503


# ---------------------------------------------------------------------------
# Псевдоним и /api/auth/me
# ---------------------------------------------------------------------------

def test_me_reports_pseudonym_not_google_name(monkeypatch):
    client = login(monkeypatch, "named", display_name="Корректор")
    body = client.get("/api/auth/me").json()
    assert body["displayName"] == "Корректор"
    assert body["username"] == "Корректор"
    assert "email" not in body and "picture" not in body


def test_participant_without_a_pseudonym_is_shown_by_number(monkeypatch):
    client = login(monkeypatch, "anon-1")
    body = client.get("/api/auth/me").json()
    assert body["displayName"] is None
    assert body["username"].startswith("Участник №")


def test_display_name_requires_csrf(monkeypatch):
    client = login(monkeypatch, "nocsrf", csrf=False)
    assert client.post("/api/auth/display-name",
                       json={"displayName": "x"}).status_code == 403


def test_leaving_the_project_unlinks_the_account(monkeypatch):
    client = login(monkeypatch, "departing")
    assert client.post("/api/auth/leave").status_code == 200
    # Сессия убита вместе с привязкой.
    assert client.get("/api/auth/me").status_code == 401
    # И прежний Google-аккаунт больше не узнаётся.
    _, again = _login_raw(monkeypatch, "departing")
    assert again.status_code == 403


# ---------------------------------------------------------------------------
# Роли: лестница viewer < editor < admin
# ---------------------------------------------------------------------------

def test_viewer_cannot_write_remarks(monkeypatch):
    client = login(monkeypatch, "viewer-1", role="viewer")
    response = client.post("/api/editor/medinsky11klass/50",
                           json={"kind": "minor", "text": "x", "coords": [1, 1]})
    assert response.status_code == 403


def test_editor_can_write_remarks(monkeypatch):
    client = login(monkeypatch, "editor-1", role="editor")
    response = client.post("/api/editor/medinsky11klass/51",
                           json={"kind": "minor", "text": "x", "coords": [1, 1]})
    assert response.status_code == 200


def test_reviewer_role_is_gone(monkeypatch):
    """`reviewer` упразднён: он никогда не давал ничего сверх editor, а кабинет
    его и вовсе не пускал — значение, которое ничего не значит, опасно тем, что
    однажды его кому-нибудь выдадут."""
    admin = login(monkeypatch, "boss-reviewer", role="admin")
    csrf = admin.get("/api/auth/csrf").json()["csrfToken"]
    response = admin.post("/api/admin/invites", json={"role": "reviewer"},
                          headers={"X-CSRF-Token": csrf})
    assert response.status_code == 400

    member = login(monkeypatch, "member-reviewer", role="editor")
    user_id = member.get("/api/auth/me").json()["userId"]
    csrf = admin.get("/api/auth/csrf").json()["csrfToken"]
    assert admin.post(f"/api/admin/users/{user_id}/role", json={"role": "reviewer"},
                      headers={"X-CSRF-Token": csrf}).status_code == 400


def test_existing_reviewers_become_editors(monkeypatch):
    """Строка в базе старше упразднения роли должна пережить его редактором, а
    не потерять права молча."""
    import db as dbmod
    login(monkeypatch, "old-reviewer", role="editor")
    conn = dbmod.get_connection()
    conn.execute("UPDATE users SET role = 'reviewer' WHERE sub_hash IS NOT NULL")
    conn.commit()
    dbmod._retire_reviewer_role(conn)
    assert not conn.execute(
        "SELECT 1 FROM users WHERE role = 'reviewer'").fetchone()


def test_viewer_cannot_view_logs(monkeypatch):
    client = login(monkeypatch, "viewer-2", role="viewer")
    assert client.get("/logs").status_code == 403
    assert client.get("/api/logs").status_code == 403


def test_admin_can_view_logs(monkeypatch):
    client = login(monkeypatch, "root-1", role="admin")
    assert client.get("/logs").status_code == 200
    assert client.get("/api/logs").status_code == 200


def test_admin_can_change_a_role(monkeypatch):
    admin = login(monkeypatch, "boss-role", role="admin")
    member = login(monkeypatch, "member-role", role="editor")
    user_id = member.get("/api/auth/me").json()["userId"]

    response = admin.post(f"/api/admin/users/{user_id}/role", json={"role": "admin"})
    assert response.status_code == 200
    # Роль читается из БД на каждом запросе, поэтому действует сразу.
    assert member.get("/api/auth/me").json()["role"] == "admin"


def test_admin_can_retire_a_participant(monkeypatch):
    admin = login(monkeypatch, "boss-retire", role="admin")
    member = login(monkeypatch, "member-retire", role="editor")
    user_id = member.get("/api/auth/me").json()["userId"]

    assert admin.post(f"/api/admin/users/{user_id}/retire").status_code == 200
    assert member.get("/api/auth/me").status_code == 401


# ---------------------------------------------------------------------------
# Logout / истечение сессии
# ---------------------------------------------------------------------------

def test_logout_then_me_is_401(monkeypatch):
    client = login(monkeypatch, "dana")
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_expired_session_is_401(monkeypatch):
    from datetime import datetime, timedelta

    client = login(monkeypatch, "erin")
    session_id = client.cookies.get("redpen_session")
    conn = db.get_connection()
    past = (datetime.utcnow() - timedelta(seconds=1)).isoformat()
    conn.execute("UPDATE sessions SET expires_at = ? WHERE id = ?", (past, session_id))
    conn.commit()

    assert client.get("/api/auth/me").status_code == 401


# ---------------------------------------------------------------------------
# Приглашения через API
# ---------------------------------------------------------------------------

def test_invites_require_admin(monkeypatch):
    editor = login(monkeypatch, "plain-editor", role="editor")
    assert editor.get("/api/admin/invites").status_code == 403
    assert editor.post("/api/admin/invites", json={"role": "editor"}).status_code == 403

    admin = login(monkeypatch, "boss-invites", role="admin")
    assert admin.get("/api/admin/invites").status_code == 200


def test_creating_an_invite_requires_csrf(monkeypatch):
    admin = login(monkeypatch, "boss-csrf", role="admin", csrf=False)
    assert admin.post("/api/admin/invites", json={"role": "editor"}).status_code == 403


def test_invite_code_is_returned_once_and_never_stored(monkeypatch):
    admin = login(monkeypatch, "boss-once", role="admin")
    response = admin.post("/api/admin/invites", json={"role": "editor", "note": "для К."})
    assert response.status_code == 200
    code = response.json()["code"]

    # В листинге кода нет — только хеш: живых ключей БД не хранит.
    listed = admin.get("/api/admin/invites").json()["invites"]
    assert all("code" not in entry for entry in listed)
    assert any(entry["codeHash"] == db.hash_invite_code(code) for entry in listed)


def test_invite_role_is_validated(monkeypatch):
    admin = login(monkeypatch, "boss-badrole", role="admin")
    assert admin.post("/api/admin/invites", json={"role": "root"}).status_code == 400


def test_unused_invite_can_be_revoked(monkeypatch):
    admin = login(monkeypatch, "boss-revoke", role="admin")
    code = admin.post("/api/admin/invites", json={"role": "editor"}).json()["code"]
    code_hash = db.hash_invite_code(code)

    assert admin.delete(f"/api/admin/invites/{code_hash}").status_code == 200
    assert admin.delete(f"/api/admin/invites/{code_hash}").status_code == 404
    # Отозванный код больше не пускает.
    _, response = _login_raw(monkeypatch, "too-late", code=code)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/admin/publish-all
# ---------------------------------------------------------------------------

def test_publish_all_requires_admin(monkeypatch):
    editor = login(monkeypatch, "publish-editor", role="editor")
    assert editor.post("/api/admin/publish-all").status_code == 403


def test_publish_all_writes_files_for_admin(monkeypatch):
    import os

    db.upsert_remark_db("publishalldoc", "006", "ann-1", "minor", "hi")
    admin = login(monkeypatch, "publish-admin", role="admin")

    response = admin.post("/api/admin/publish-all")
    assert response.status_code == 200
    assert response.json()["pages"] >= 1
    assert response.json()["failed"] == 0

    path = os.path.join(config.PUBLISH_DIR, "publishalldoc", "remarks", "page_006.json")
    assert os.path.exists(path)
