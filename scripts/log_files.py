"""Чтение access-логов Caddy: перечисление файлов и отпечаток ротации.

Обе программы, разбирающие лог, — экспорт в Matomo (`matomo_export.py`) и
счётчики контента (`ops/redpen_stats.py`) — запускаются одним шелл-скриптом в
одном контейнере и обходят один каталог. Раньше эти три функции существовали в
двух побайтово одинаковых копиях.
"""

import gzip
import hashlib
import os
from typing import List


def open_log(path: str):
    """Открыть лог на чтение, распаковывая .gz на лету."""
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def signature(path: str) -> str:
    """Отпечаток первой строки: ротация или обрезка меняют его."""
    # Именно первая строка, а не первые N байт: дописанная в конец строка
    # меняла бы «первые 512 байт» у короткого файла, и весь лог читался бы
    # заново — то есть уезжал бы в Matomo дважды.
    try:
        with open_log(path) as f:
            head = f.readline()
    except OSError:
        return ""
    return hashlib.sha256(head.encode("utf-8", "replace")).hexdigest()[:16]


def log_files(log_dir: str) -> List[str]:
    """Все файлы логов каталога, от старых к свежим."""
    if not os.path.isdir(log_dir):
        return []
    names = [n for n in os.listdir(log_dir)
             if n.startswith("access") and (n.endswith(".log") or n.endswith(".gz"))]
    paths = [os.path.join(log_dir, n) for n in names]
    return sorted(paths, key=lambda p: (os.path.getmtime(p), p))
