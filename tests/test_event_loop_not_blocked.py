"""Тяжёлая работа не должна останавливать весь API.

API работает одним воркером (`--workers 1`), а обработчики маршрутов были
объявлены `async def`, хотя внутри у них синхронная работа: запросы к SQLite,
запись файлов, сетевая проверка токена Google. Такой обработчик выполняется
прямо на цикле событий, то есть на время своей работы останавливает сервис
целиком — включая проверку живости `/api/health`.

Лечение: обработчик без единого `await` объявляется обычным `def`, и FastAPI
сам уводит его в пул потоков. Там, где тело запроса всё же читается через
`await`, тяжёлая часть уходит в пул явно (`run_in_threadpool`).
"""
import ast
import inspect
import io
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import main  # noqa: E402
import publisher  # noqa: E402

_SRC = Path(main.__file__)


def _route_handlers():
    tree = ast.parse(io.open(_SRC, encoding="utf-8").read())
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = [ast.unparse(d) for d in node.decorator_list]
        if any(d.startswith(("app.get", "app.post", "app.put",
                             "app.patch", "app.delete"))
               for d in decorators):
            yield node


def test_no_handler_blocks_the_loop_for_nothing():
    """`async def` оправдан только там, где действительно есть `await`."""
    offenders = [node.name for node in _route_handlers()
                 if isinstance(node, ast.AsyncFunctionDef)
                 and not any(isinstance(n, ast.Await) for n in ast.walk(node))]
    assert offenders == [], (
        "эти обработчики объявлены async, но ничего не ждут — значит держат "
        f"цикл событий: {offenders}")


def test_google_verification_runs_off_the_loop():
    source = inspect.getsource(main.auth_google)
    assert "run_in_threadpool(verify_google_token" in source


def test_publishing_runs_off_the_loop_in_async_handlers():
    """Запись страницы в статику — четыре чтения и две записи с fsync."""
    for handler in (main.post_editor_remark, main.put_editor_remark,
                    main._patch_result):
        source = inspect.getsource(handler)
        assert "run_in_threadpool(\n        _publish_and_sha" in source \
            or "run_in_threadpool(" in source and "_publish_and_sha" in source


def test_publish_all_refuses_a_second_run(monkeypatch):
    """Второй обход поверх первого ничего не ускорит, а работы удвоит."""
    assert publisher._publish_all_lock.acquire(blocking=False)
    try:
        with pytest.raises(publisher.PublishAllBusy):
            publisher.publish_all()
    finally:
        publisher._publish_all_lock.release()
    # Замок отпущен — обычный вызов снова работает.
    assert "pages" in publisher.publish_all()


def test_health_answers_while_publish_all_is_running(monkeypatch, tmp_path):
    """Проверка живости не должна ждать конца перепубликации.

    Проверяется ПОРЯДОК завершения, а не время: оба запроса идут через один
    цикл событий, и если обработчик перепубликации держит этот цикл, то запрос
    живости физически не может завершиться раньше — даже таймаут не сработает,
    потому что выполнить его тоже некому. Порядок «сначала живость, потом
    перепубликация» и означает, что тяжёлая работа ушла в пул потоков.

    До 2026-09-05 обход девятисот страниц шёл прямо на цикле событий, и на всё
    это время API не отвечал ничем."""
    import asyncio
    import os
    import time

    import httpx

    import config
    import db
    from _auth_helpers import login

    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(config, "DB_PATH", os.path.join(tmp_path, "redpen.db"))
    if db._conn is not None:
        db._conn.close()
    db._conn = None
    db.init_db()

    def slow_publish_all():
        time.sleep(1.0)
        return {"pages": 0, "failed": 0}

    monkeypatch.setattr(publisher, "publish_all", slow_publish_all)
    # Сессия и CSRF берутся обычным клиентом, дальше нужны только заголовки.
    admin = login(monkeypatch, "loop-admin", role="admin")
    cookies = dict(admin.cookies)
    headers = {"X-CSRF-Token": admin.headers["X-CSRF-Token"]}

    finished = []

    async def scenario():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test",
                                     cookies=cookies) as client:

            async def publish():
                response = await client.post("/api/admin/publish-all",
                                             headers=headers)
                finished.append("перепубликация")
                return response

            async def health():
                # Небольшая фора, чтобы перепубликация точно началась первой.
                await asyncio.sleep(0.05)
                response = await client.get("/api/health")
                finished.append("живость")
                return response

            return await asyncio.gather(publish(), health())

    try:
        published, health = asyncio.run(scenario())
        assert published.status_code == 200
        assert health.json() == {"status": "ok"}
        assert finished[0] == "живость", (
            "перепубликация завершилась первой — значит она держала цикл "
            "событий, и всё остальное ждало её")
    finally:
        if db._conn is not None:
            db._conn.close()
        db._conn = None
