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


def _req(headers, host="10.0.0.1"):
    return type("Req", (), {
        "headers": headers,
        "client": type("C", (), {"host": host})(),
    })()


def test_client_key_takes_the_address_the_proxy_appended():
    # Наш прокси дописывает адрес соединения В КОНЕЦ, поэтому ключ — последний
    # элемент. Всё, что стоит перед ним, прислал сам клиент.
    assert ratelimit.client_key(
        _req({"x-forwarded-for": "203.0.113.7, 198.51.100.4"})) == "198.51.100.4"
    assert ratelimit.client_key(
        _req({"x-forwarded-for": "198.51.100.4"})) == "198.51.100.4"


def test_client_key_falls_back_to_the_connection():
    assert ratelimit.client_key(_req({}, host="198.51.100.4")) == "198.51.100.4"
    # Пустой или мусорный заголовок не должен давать пустой ключ, общий для всех.
    assert ratelimit.client_key(_req({"x-forwarded-for": "  "},
                                     host="198.51.100.4")) == "198.51.100.4"
    assert ratelimit.client_key(_req({"x-forwarded-for": "1.2.3.4,  "},
                                     host="198.51.100.4")) == "198.51.100.4"


def test_a_forged_header_does_not_buy_a_fresh_bucket():
    """Главная проверка: подделанный заголовок не снимает предел.

    До 2026-09-05 ключом был первый элемент, и такой обстрел не встречал
    предела вовсе — каждый запрос получал новое полное ведро.
    """
    bucket = ratelimit.TokenBucket(rate_per_minute=60, burst=2)
    verdicts = [
        bucket.allow(ratelimit.client_key(
            _req({"x-forwarded-for": f"10.1.1.{i}, 198.51.100.4"})), now=0.0)
        for i in range(5)
    ]
    assert verdicts == [True, True, False, False, False]


def test_eviction_keeps_the_buckets_that_are_being_spent():
    """Переполнение не должно возвращать полный запас токенов заливающим.

    Прежняя версия при нехватке места очищала словарь целиком — то есть
    снимала ограничение ровно во время залива."""
    bucket = ratelimit.TokenBucket(rate_per_minute=6, burst=1)
    bucket.MAX_KEYS = 10
    assert bucket.allow("активный", now=1000.0) is True
    # Залив с меняющихся ключей вперемешку с запросами того, кто уже отбит.
    for i in range(200):
        now = 1000.0 + i * 0.001
        bucket.allow(f"залив-{i}", now=now)
        assert bucket.allow("активный", now=now) is False
    assert len(bucket._buckets) <= bucket.MAX_KEYS


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


def test_the_log_page_is_limited_too(strict):
    """`/logs` читает файл лога, но не начинается с /api/.

    До 2026-09-05 условие в middleware смотрело только на префикс /api/, и этот
    маршрут не ограничивался вовсе."""
    codes = [strict.get("/logs").status_code for _ in range(6)]
    assert 429 in codes


def test_refusal_carries_cors_headers(strict):
    """429 должен доезжать до браузера как «слишком часто», а не как «нет связи».

    Middleware ограничителя обёрнут вокруг CORSMiddleware, поэтому заголовки
    проставляются в самом ответе."""
    origin = "https://medinsky.net"
    for _ in range(6):
        response = strict.get("/api/tags", headers={"Origin": origin})
    assert response.status_code == 429
    assert response.headers.get("Access-Control-Allow-Origin") in (origin, "*")


def test_survey_is_counted_by_session_not_by_address(strict):
    """Два захода с одного адреса не должны отбивать друг друга.

    Класс за общим NAT — это один адрес на тридцать человек."""
    first = {"X-Survey-Token": "token-one"}
    second = {"X-Survey-Token": "token-two"}
    codes = [strict.get("/api/survey/batch", headers=first).status_code
             for _ in range(4)]
    assert 429 in codes
    # У другого захода своё ведро, хотя адрес тот же.
    assert strict.get("/api/survey/batch", headers=second).status_code != 429
