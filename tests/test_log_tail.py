"""Лог API читается с конца, а не целиком.

`/logs` и `/api/logs` делали `readlines()` ради последней сотни строк, то есть
держали в памяти весь файл. Файл при этом писался без ротации и рос вечно,
а под заливом рос ровно так же быстро, как шёл залив.
"""
import pytest

pytest.importorskip("fastapi")

import main  # noqa: E402


def test_tail_returns_the_last_lines(tmp_path):
    path = tmp_path / "лог"
    path.write_text("".join(f"строка {i}\n" for i in range(1000)), encoding="utf-8")
    assert main.tail_lines(str(path), 3) == ["строка 997", "строка 998", "строка 999"]


def test_tail_survives_a_file_shorter_than_the_request(tmp_path):
    path = tmp_path / "лог"
    path.write_text("одна\nдве\n", encoding="utf-8")
    assert main.tail_lines(str(path), 100) == ["одна", "две"]


def test_tail_on_a_missing_file_is_empty(tmp_path):
    assert main.tail_lines(str(tmp_path / "нет такого"), 10) == []


def test_tail_does_not_read_the_whole_file(tmp_path):
    """Проверка по существу: большой файл не вычитывается целиком."""
    path = tmp_path / "лог"
    with open(path, "w", encoding="utf-8") as f:
        for i in range(200_000):
            f.write(f"строка {i} с некоторым хвостом для веса\n")
    assert path.stat().st_size > 5 * 1024 * 1024

    read_calls = []
    real_open = open

    def counting_open(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        if "b" in mode:
            real_read = handle.read

            def read(size=-1):
                chunk = real_read(size)
                read_calls.append(len(chunk))
                return chunk

            handle.read = read
        return handle

    import builtins
    builtins.open = counting_open
    try:
        tail = main.tail_lines(str(path), 5)
    finally:
        builtins.open = real_open

    assert tail[-1].startswith("строка 199999")
    assert sum(read_calls) < 1024 * 1024


def test_rotation_is_configured():
    """Ротация должна быть настроена, иначе лог занимает диск хоста навсегда."""
    import logging.handlers
    handlers = [h for h in main.logger.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert handlers, "лог пишется без ротации"
    assert handlers[0].maxBytes == main.LOG_MAX_BYTES
    assert handlers[0].backupCount == main.LOG_BACKUP_COUNT
