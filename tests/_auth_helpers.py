"""Общая обвязка логина для тестов API.

Раньше каждый файл заводил собственные `_login_google`/`_with_csrf`/`_editor`,
и они разъезжались. Здесь одна реализация под новую модель опознания: система
знает про участника только хеш его Google `sub`, а доступ выдаётся одноразовым
приглашением (docs/anonymity-model.md).

Приём: в тестах `credential` — это и есть `sub`. Проверка токена подменяется на
разбор строки, поэтому разные клиенты различаются просто разными credential.
"""

from fastapi.testclient import TestClient

import db
import main


def mock_google(monkeypatch):
    """Подменить проверку токена: credential трактуется как Google `sub`."""
    monkeypatch.setattr(main, "verify_google_token",
                        lambda credential: {"sub": credential})


def invite(role="editor", created_by=None):
    code, _ = db.create_invite(role=role, created_by=created_by)
    return code


def login(monkeypatch, sub, role="editor", csrf=True, display_name=None):
    """Клиент с сессией: при первом входе гасится свежее приглашение на `role`."""
    mock_google(monkeypatch)
    client = TestClient(main.app)
    response = client.post("/api/auth/google",
                           json={"credential": sub, "invite": invite(role)})
    assert response.status_code == 200, response.text
    if csrf:
        with_csrf(client)
    if display_name is not None:
        assert client.post("/api/auth/display-name",
                           json={"displayName": display_name}).status_code == 200
    return client


def with_csrf(client):
    response = client.get("/api/auth/csrf")
    assert response.status_code == 200, response.text
    client.headers.update({"X-CSRF-Token": response.json()["csrfToken"]})
    return client


def anon():
    return TestClient(main.app)
