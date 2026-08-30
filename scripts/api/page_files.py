"""Разбор имён постраничных файлов замечаний.

Ключ страницы — файловый и берётся из имени дословно, поэтому «000» и «-01»
доживают до базы неизменными (в БД `page_num` — TEXT). Служебные файлы
каталога черновиков (`_report_*`, `_check_*`, `_typical_remarks.md`) отсеиваются
самим шаблоном имени.

Импортёр JSON и бэкфилл тегов из markdown обходили каталоги одинаково, но
двумя копиями кода — здесь один экземпляр на оба расширения.
"""

import os
import re
from typing import List, Optional, Tuple

PAGE_JSON_RE = re.compile(r"^page_(-?\d+)\.json$")
PAGE_MD_RE = re.compile(r"^page_(-?\d+)\.md$")


def iter_page_files(directory: str, pattern: re.Pattern) -> List[Tuple[str, str]]:
    """[(page_num, path), ...] в порядке имён файлов."""
    if not os.path.isdir(directory):
        return []
    out = []
    for name in sorted(os.listdir(directory)):
        m = pattern.match(name)
        if m:
            out.append((m.group(1), os.path.join(directory, name)))
    return out


def iter_doc_dirs(source_dir: str, doc_id: Optional[str], subdir: str = "remarks") -> List[str]:
    """Каталоги документов внутри source_dir, у которых есть подкаталог subdir."""
    if doc_id:
        return [doc_id] if os.path.isdir(os.path.join(source_dir, doc_id, subdir)) else []
    if not os.path.isdir(source_dir):
        return []
    return [name for name in sorted(os.listdir(source_dir))
            if os.path.isdir(os.path.join(source_dir, name, subdir))]
