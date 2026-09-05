"""Почасовой разбор логов не должен ломаться от залива.

Разбор идёт внутри контейнера API, рядом с единственным воркером, и всё, что
он держит в памяти, он отнимает у сервиса. Под заливом файл лога — это сотни
тысяч строк за час.
"""
import io
import os
import sqlite3

import pytest

import matomo_export  # noqa: E402
from ops import redpen_stats  # noqa: E402


def _log_line(path="/medinsky11klass/pages/7/"):
    return ('{"ts":1756000000.0,"request":{"host":"medinsky.net",'
            f'"uri":"{path}","method":"GET","headers":{{}}}},'
            '"status":200,"size":100}')


def _write_log(tmp_path, lines):
    log_dir = tmp_path / "caddy"
    log_dir.mkdir()
    target = log_dir / "access.log"
    target.write_text("\n".join(_log_line() for _ in range(lines)) + "\n",
                      encoding="utf-8")
    return str(log_dir)


def test_ingest_writes_in_batches(tmp_path, monkeypatch):
    """Разобранные строки не копятся одним списком на весь файл."""
    monkeypatch.setattr(redpen_stats, "INGEST_BATCH", 10)
    log_dir = _write_log(tmp_path, 55)

    conn = redpen_stats.open_db(str(tmp_path / "stats.db"))
    sizes = []
    real_write = redpen_stats._write_hits

    def counting_write(conn_, batch):
        if batch:
            sizes.append(len(batch))
        return real_write(conn_, batch)

    monkeypatch.setattr(redpen_stats, "_write_hits", counting_write)
    stats = redpen_stats.ingest(conn, log_dir, own_hosts=("medinsky.net",))

    assert stats["hits"] == 55
    assert max(sizes) <= 10, f"пачки оказались по {max(sizes)} строк"
    assert conn.execute("SELECT COUNT(*) FROM hits").fetchone()[0] == 55


def test_matomo_export_stops_at_the_ceiling(tmp_path):
    """За один прогон выгружается не больше предела; остаток ждёт следующего."""
    log_dir = _write_log(tmp_path, 100)
    out = tmp_path / "matomo-import.log"
    state = tmp_path / "state.json"

    written = matomo_export.export(log_dir, str(out), str(state),
                                   site_dir=None, max_lines=30,
                                   own_hosts=("medinsky.net",))
    assert written == 30
    assert len(out.read_text(encoding="utf-8").splitlines()) == 30


def test_matomo_export_resumes_where_it_stopped(tmp_path):
    log_dir = _write_log(tmp_path, 100)
    out = tmp_path / "matomo-import.log"
    state = tmp_path / "state.json"

    assert matomo_export.export(log_dir, str(out), str(state), site_dir=None,
                                max_lines=30, own_hosts=("medinsky.net",)) == 30
    # Позиция запоминается только после успешного импорта — здесь имитируем его.
    os.replace(f"{state}.pending", state)
    assert matomo_export.export(log_dir, str(out), str(state), site_dir=None,
                                max_lines=30, own_hosts=("medinsky.net",)) == 30
    os.replace(f"{state}.pending", state)
    assert matomo_export.export(log_dir, str(out), str(state), site_dir=None,
                                max_lines=1000, own_hosts=("medinsky.net",)) == 40
