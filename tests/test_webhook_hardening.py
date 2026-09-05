"""Вебхук публикации: тело читается только после проверки размера.

Маршрут `/.hooks/redpen-publish` открыт наружу (Caddy проксирует его на хосте
сайта), а предел размера тела до 2026-09-05 стоял только на хосте API. Тело
читалось в память целиком и лишь потом проверялось по подписи, так что один
большой POST без всякой подписи стоил дороже тысячи обычных запросов.
"""
import hashlib
import hmac
import importlib.util
import io
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "content-sync" / "content_sync.py"


@pytest.fixture(scope="module")
def content_sync():
    spec = importlib.util.spec_from_file_location("content_sync", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def fresh_sync_flag(content_sync):
    """Флаг «синхронизация уже идёт» — модульное состояние.

    В бою его отпускает сам поток синхронизации; в тестах поток подменён, и
    без сброса первый же успешный вызов оставлял бы флаг взведённым для всех
    следующих проверок."""
    import threading
    content_sync._sync_pending = threading.Lock()
    yield


class _Rfile(io.BytesIO):
    """Тело запроса, которое считает, сколько байт у него прочитали."""

    def __init__(self, data: bytes):
        super().__init__(data)
        self.read_bytes = 0

    def read(self, size=-1):
        chunk = super().read(size)
        self.read_bytes += len(chunk)
        return chunk


def _handler(content_sync, path="/.hooks/redpen-publish", headers=None, body=b""):
    """Обработчик без сети: HTTP-разбор нам не нужен, нужен do_POST."""
    handler = content_sync.Handler.__new__(content_sync.Handler)
    handler.path = path
    handler.headers = headers or {}
    handler.rfile = _Rfile(body)
    handler.replies = []
    handler.wfile = io.BytesIO()

    def send_response(code, message=None):
        handler.replies.append(code)

    handler.send_response = send_response
    handler.send_header = lambda *a, **kw: None
    handler.end_headers = lambda: None
    handler.log_message = lambda *a, **kw: None
    return handler


def test_oversized_body_is_refused_without_being_read(content_sync):
    size = content_sync.MAX_WEBHOOK_BODY + 1
    handler = _handler(content_sync,
                       headers={"Content-Length": str(size)},
                       body=b"x" * 16)
    handler.do_POST()
    assert handler.replies == [413]
    # Главное: тело не прочитано ни на байт.
    assert handler.rfile.read_bytes == 0


def test_garbage_content_length_answers_400(content_sync):
    # Прежняя версия падала здесь с ValueError.
    handler = _handler(content_sync, headers={"Content-Length": "не число"})
    handler.do_POST()
    assert handler.replies == [400]


def test_negative_content_length_is_refused(content_sync):
    handler = _handler(content_sync, headers={"Content-Length": "-1"})
    handler.do_POST()
    assert handler.replies == [413]


def test_unknown_path_answers_404(content_sync):
    handler = _handler(content_sync, path="/что-то ещё",
                       headers={"Content-Length": "0"})
    handler.do_POST()
    assert handler.replies == [404]


def test_bad_signature_answers_401(content_sync, monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "секрет")
    body = b'{"ref": "refs/heads/main"}'
    handler = _handler(content_sync,
                       headers={"Content-Length": str(len(body)),
                                "X-Hub-Signature-256": "sha256=00"},
                       body=body)
    handler.do_POST()
    assert handler.replies == [401]


def test_valid_push_answers_before_doing_the_work(content_sync, monkeypatch):
    """Ответ 202 отдаётся сразу, работа уходит в отдельный поток.

    Раньше отправитель вебхука ждал весь git fetch и rsync, занимая
    единственный обработчик однопоточного сервера."""
    monkeypatch.setenv("WEBHOOK_SECRET", "секрет")
    body = b'{"ref": "refs/heads/main"}'
    digest = hmac.new(b"\xd1\x81\xd0\xb5\xd0\xba\xd1\x80\xd0\xb5\xd1\x82",
                      body, hashlib.sha256).hexdigest()
    started = []
    monkeypatch.setattr(content_sync.threading, "Thread",
                        lambda **kw: type("T", (), {
                            "start": lambda self: started.append(kw["target"])})())
    handler = _handler(content_sync,
                       headers={"Content-Length": str(len(body)),
                                "X-Hub-Signature-256": f"sha256={digest}"},
                       body=body)
    handler.do_POST()
    assert handler.replies == [202]
    assert started == [content_sync.run_sync_under_lock]


def test_a_second_call_is_folded_into_the_running_one(content_sync, monkeypatch):
    """Пока синхронизация идёт, второй вызов не разводит ещё один поток."""
    monkeypatch.setenv("WEBHOOK_SECRET", "секрет")
    body = b'{"ref": "refs/heads/main"}'
    digest = hmac.new("секрет".encode(), body, hashlib.sha256).hexdigest()
    started = []
    monkeypatch.setattr(content_sync.threading, "Thread",
                        lambda **kw: type("T", (), {
                            "start": lambda self: started.append(kw["target"])})())
    headers = {"Content-Length": str(len(body)),
               "X-Hub-Signature-256": f"sha256={digest}"}
    assert content_sync._sync_pending.acquire(blocking=False)
    try:
        handler = _handler(content_sync, headers=headers, body=body)
        handler.do_POST()
        assert handler.replies == [202]
        assert started == []
    finally:
        content_sync._sync_pending.release()
