"""Ограничение частоты запросов к API.

Просмотрщик к API не обращается вовсе (инвариант офлайна), поэтому даже
полностью выведенный из строя API читателя не касается — ограничивать нужно
ровно API, и именно его дорогие открытые ручки.
"""

import pytest

pytest.importorskip("fastapi")

import config  # noqa: E402
import main  # noqa: E402
import ratelimit  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402


# --- само ведро ---------------------------------------------------------


def test_burst_is_allowed_then_refused():
    bucket = ratelimit.TokenBucket(rate_per_minute=60, burst=3)
    assert [bucket.allow("ip", now=100.0) for _ in range(3)] == [True, True, True]
    assert bucket.allow("ip", now=100.0) is False


def test_tokens_refill_over_time():
    bucket = ratelimit.TokenBucket(rate_per_minute=60, burst=2)  # один токен в секунду
    assert bucket.allow("ip", now=0.0) is True
    assert bucket.allow("ip", now=0.0) is True
    assert bucket.allow("ip", now=0.0) is False
    assert bucket.allow("ip", now=1.0) is True


def test_keys_are_independent():
    bucket = ratelimit.TokenBucket(rate_per_minute=60, burst=1)
    assert bucket.allow("первый", now=0.0) is True
    assert bucket.allow("первый", now=0.0) is False
    # Чужой адрес не должен страдать от соседа.
    assert bucket.allow("второй", now=0.0) is True


def test_zero_rate_disables_the_check():
    bucket = ratelimit.TokenBucket(rate_per_minute=0, burst=0)
    assert all(bucket.allow("ip", now=0.0) for _ in range(100))


def test_memory_does_not_grow_without_bound():
    bucket = ratelimit.TokenBucket(rate_per_minute=600, burst=1)
    bucket.MAX_KEYS = 50
    for i in range(500):
        bucket.allow(f"ip-{i}", now=float(i))
    assert len(bucket._buckets) <= bucket.MAX_KEYS + 1


def test_client_key_prefers_the_proxy_header():
    class Req:
        headers = {"x-forwarded-for": "203.0.113.7, 10.0.0.1"}
        client = type("C", (), {"host": "10.0.0.1"})()
    assert ratelimit.client_key(Req()) == "203.0.113.7"

    class Direct:
        headers = {}
        client = type("C", (), {"host": "198.51.100.4"})()
    assert ratelimit.client_key(Direct()) == "198.51.100.4"


# --- поведение API ------------------------------------------------------


@pytest.fixture
def strict(monkeypatch):
    """Заменяем вёдра на заведомо тесные, чтобы не слать сотни запросов."""
    monkeypatch.setattr(main, "_rate_general", ratelimit.TokenBucket(600, 3))
    monkeypatch.setattr(main, "_rate_auth", ratelimit.TokenBucket(60, 2))
    return TestClient(main.app)


def test_api_answers_429_after_the_burst(strict):
    codes = [strict.get("/api/hello").status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200]
    assert 429 in codes
    response = strict.get("/api/hello")
    assert response.status_code == 429
    assert response.headers.get("Retry-After") == "60"


def test_health_is_never_limited(strict):
    # Проверка живости не должна глохнуть ровно тогда, когда что-то происходит.
    assert all(strict.get("/api/health").status_code == 200 for _ in range(20))


def test_login_has_its_own_stricter_limit(strict, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "test-client")
    monkeypatch.setattr(main, "verify_google_token", lambda credential: {"sub": credential})
    # Своё ведро: два запроса проходят, третий — нет, хотя общий предел щедрее.
    first = strict.post("/api/auth/google", json={"credential": "x"})
    second = strict.post("/api/auth/google", json={"credential": "x"})
    third = strict.post("/api/auth/google", json={"credential": "x"})
    assert first.status_code != 429 and second.status_code != 429
    assert third.status_code == 429


def test_static_paths_are_not_limited(strict):
    # Ограничитель трогает только /api/*: сайт раздаёт nginx, и он тут ни при чём.
    assert all(strict.get("/logs").status_code in (401, 403) for _ in range(10))
