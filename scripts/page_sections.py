"""
Свёртка страниц в параграфы по манифесту книги.

Единица работы проекта — параграф, а не страница: агент-аннотатор запускается
на параграф, приёмка идёт параграфами, и отчёт о посещаемости осмысленен тем
же образом. Манифест (`<doc>/metadata.json`) — единственный источник границ:
диапазоны заданы файловыми номерами страниц, и у обычных страниц метка адреса
с ними совпадает (`page_017` ↔ `?p=17` ↔ `/pages/17/`).

Модулем пользуются и экспорт в Matomo, и наши счётчики контента.
"""

import json
import os
from typing import Any, Dict, List, Optional


def load_manifest(site_dir: str, doc_id: str) -> Dict[str, Any]:
    """Границы параграфов и таблица «метка адреса → файловый номер».

    Манифеста может не быть (книга не выложена, каталог не примонтирован) —
    тогда отчёт просто беднее, а не падает.
    """
    path = os.path.join(site_dir, doc_id, "metadata.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return {"label_to_key": {}, "sections": []}

    label_to_key = {}
    for page in meta.get("pages") or []:
        file_name = page.get("file") or ""
        label = str(page.get("label") or "")
        if file_name.startswith("page_") and label:
            label_to_key[label] = file_name[len("page_"):]

    sections: List[Dict[str, Any]] = []
    for chapter in meta.get("chapters") or []:
        for section in chapter.get("sections") or []:
            if section.get("startPage") is None:
                continue
            sections.append({
                "id": str(section.get("id") or ""),
                "name": section.get("name") or "",
                "start": int(section["startPage"]),
                "end": int(section.get("endPage") or section["startPage"]),
            })
    return {"label_to_key": label_to_key, "sections": sections}


def section_of(manifest: Dict[str, Any], label: str) -> Optional[Dict[str, Any]]:
    """Параграф, которому принадлежит страница; None — аппарат книги."""
    try:
        number = int(label)
    except (TypeError, ValueError):
        return None
    for section in manifest.get("sections") or []:
        if section["start"] <= number <= section["end"]:
            return section
    return None


class ManifestCache:
    """Ленивая загрузка манифестов: книг две, а строк лога тысячи."""

    def __init__(self, site_dir: str):
        self.site_dir = site_dir
        self._cache: Dict[str, Dict[str, Any]] = {}

    def get(self, doc_id: str) -> Dict[str, Any]:
        if doc_id not in self._cache:
            self._cache[doc_id] = load_manifest(self.site_dir, doc_id)
        return self._cache[doc_id]

    def section_of(self, doc_id: str, label: str) -> Optional[Dict[str, Any]]:
        return section_of(self.get(doc_id), label)
