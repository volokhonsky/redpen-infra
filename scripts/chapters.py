"""
Build the `chapters` section of redpen-publish/<docId>/metadata.json from
redpen-content/<docId>/paragraphs_list.txt.

Why this exists: the hand-maintained `chapters` in meta.json had lost §10-§23
(~170 pages had no section at all), and its sections carried only `startPage`,
so "which paragraph is this page in?" had to be guessed as "the last section
whose startPage <= page". paragraphs_list.txt -- which the annotation pipeline
already relies on (see docs/annotation-agent-prompt.md) -- has every entry with
both bounds, so the mapping becomes exact.

Source format (comments and blank lines ignored):

    # Глава I
    chapter_I, Глава I. СССР в 1945-1991 гг., 5, 5
    1, Восстановление и развитие экономики и социальной сферы, 6, 20
    32-33, Культура, наука, спорт и общественная жизнь..., 348, 371
    summary_I, Итоги главы, 269, 269

An id of `chapter_<...>` opens a chapter; every following entry becomes one of
its sections until the next chapter opens. Entries appearing before any chapter
(the introduction) form a leading chapter with no sections. A purely numeric id
(or a `N-M` range) is a numbered paragraph and is titled "§ N. <name>";
anything else keeps its name as-is.

Chapters present in the existing metadata but not covered by
paragraphs_list.txt (e.g. Приложения, Указатель) are preserved -- the list
stops at the last numbered paragraph, and those tail sections are maintained by
hand in meta.json.

Usage:
    python chapters.py <redpen-publish-doc-dir> <paragraphs-list-path>
"""

import argparse
import json
import os
import re
import sys
import tempfile
from typing import Any, Dict, List, Optional

CHAPTER_ID_RE = re.compile(r"^chapter_", re.IGNORECASE)
PARAGRAPH_ID_RE = re.compile(r"^(\d+)(?:-(\d+))?$")


class ChaptersError(ValueError):
    pass


def _section_title(entry_id: str, name: str) -> str:
    """"14" + name -> "§ 14. name"; "32-33" -> "§ 32—33. name"; other ids keep
    the bare name (Введение, Итоги главы, Ресурсы к главе...)."""
    m = PARAGRAPH_ID_RE.match(entry_id)
    if not m:
        return name
    first, second = m.group(1), m.group(2)
    number = f"{first}—{second}" if second else first
    return f"§ {number}. {name}"


def parse_paragraphs_list(path: str) -> List[Dict[str, Any]]:
    """Parse paragraphs_list.txt into raw entries, preserving file order."""
    entries: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 4:
                raise ChaptersError(f"{path}:{lineno}: expected 'id, name, start, end', got {line!r}")
            # The name itself may contain commas -- the last two fields are the bounds.
            entry_id = parts[0]
            start_raw, end_raw = parts[-2], parts[-1]
            name = ", ".join(parts[1:-2])
            try:
                start_page, end_page = int(start_raw), int(end_raw)
            except ValueError:
                raise ChaptersError(f"{path}:{lineno}: page bounds must be integers, got {start_raw!r}, {end_raw!r}")
            if end_page < start_page:
                raise ChaptersError(f"{path}:{lineno}: endPage {end_page} precedes startPage {start_page}")
            if not name:
                raise ChaptersError(f"{path}:{lineno}: empty name")
            entries.append({"id": entry_id, "name": name, "startPage": start_page, "endPage": end_page})
    if not entries:
        raise ChaptersError(f"{path}: no entries found")
    return entries


def build_chapters(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group flat entries into chapters with sections."""
    chapters: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for entry in entries:
        section = {
            "id": entry["id"],
            "name": _section_title(entry["id"], entry["name"]),
            "startPage": entry["startPage"],
            "endPage": entry["endPage"],
        }
        if CHAPTER_ID_RE.match(entry["id"]):
            current = {
                "id": entry["id"],
                "name": entry["name"],
                "startPage": entry["startPage"],
                "endPage": entry["endPage"],
                "sections": [],
            }
            chapters.append(current)
            continue
        if current is None:
            # Front matter before the first chapter (Введение): a chapter of its own.
            chapters.append({
                "id": entry["id"],
                "name": entry["name"],
                "startPage": entry["startPage"],
                "endPage": entry["endPage"],
                "sections": [],
            })
            continue
        current["sections"].append(section)
        current["endPage"] = max(current["endPage"], entry["endPage"])

    return chapters


def merge_tail_chapters(chapters: List[Dict[str, Any]], existing: Any) -> List[Dict[str, Any]]:
    """Keep hand-maintained chapters that start after everything the
    paragraphs list covers (Приложения, Указатель).

    Those entries carry only startPage, so each one's endPage is derived from
    the next one; the last stays open-ended (it runs to the end of the book).
    """
    if not isinstance(existing, list) or not chapters:
        return chapters
    last_page = max(c["endPage"] for c in chapters)
    tail = [
        dict(c) for c in existing
        if isinstance(c, dict) and isinstance(c.get("startPage"), int) and c["startPage"] > last_page
    ]
    tail.sort(key=lambda c: c["startPage"])
    for i, chapter in enumerate(tail):
        following = tail[i + 1]["startPage"] if i + 1 < len(tail) else None
        if chapter.get("endPage") is None:
            if following is not None:
                chapter["endPage"] = following - 1
            else:
                chapter.pop("endPage", None)
        chapter["sections"] = _fill_section_bounds(chapter.get("sections"), chapter.get("endPage"))
    return chapters + tail


def _fill_section_bounds(sections: Any, chapter_end: Optional[int]) -> List[Dict[str, Any]]:
    """Hand-maintained sections carry only startPage; derive each one's
    endPage from the next sibling, and the last one's from the chapter."""
    if not isinstance(sections, list):
        return []
    filled = [dict(s) for s in sections if isinstance(s, dict) and isinstance(s.get("startPage"), int)]
    filled.sort(key=lambda s: s["startPage"])
    for i, section in enumerate(filled):
        if section.get("endPage") is not None:
            continue
        if i + 1 < len(filled):
            section["endPage"] = filled[i + 1]["startPage"] - 1
        elif chapter_end is not None:
            section["endPage"] = chapter_end
        else:
            section.pop("endPage", None)
    return filled


def locate_page(chapters: List[Dict[str, Any]], page: int) -> Dict[str, Any]:
    """Which chapter/section does a printed page belong to?

    Returns {"chapter": <chapter or None>, "section": <section or None>}. Used
    for breadcrumbs and <title>s in the per-page build.
    """
    result: Dict[str, Any] = {"chapter": None, "section": None}
    for chapter in chapters:
        start, end = chapter.get("startPage"), chapter.get("endPage")
        if not isinstance(start, int) or page < start:
            continue
        # A missing endPage means open-ended: the last chapter runs to the end.
        if isinstance(end, int) and page > end:
            continue
        result["chapter"] = chapter
        for section in chapter.get("sections") or []:
            section_end = section.get("endPage")
            if section["startPage"] <= page and (section_end is None or page <= section_end):
                result["section"] = section
                break
        break
    return result


def write_chapters(doc_dir: str, chapters: List[Dict[str, Any]]) -> None:
    metadata_path = os.path.join(doc_dir, "metadata.json")
    if os.path.exists(metadata_path):
        with open(metadata_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)
    else:
        metadata = {}

    metadata["chapters"] = chapters

    data_bytes = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
    fd, tmp_path = tempfile.mkstemp(dir=doc_dir, prefix="._tmp_metadata_", suffix=".json")
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(data_bytes)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, metadata_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def generate(doc_dir: str, paragraphs_path: str) -> Optional[List[Dict[str, Any]]]:
    """Returns the written chapters, or None if the document has no
    paragraphs_list.txt (expected for documents outside the annotation
    pipeline, not an error)."""
    if not os.path.exists(paragraphs_path):
        print(f"[chapters] {paragraphs_path} not found; leaving chapters in metadata.json untouched")
        return None

    metadata_path = os.path.join(doc_dir, "metadata.json")
    existing = None
    if os.path.exists(metadata_path):
        with open(metadata_path, "r", encoding="utf-8") as f:
            existing = json.load(f).get("chapters")

    chapters = merge_tail_chapters(build_chapters(parse_paragraphs_list(paragraphs_path)), existing)
    write_chapters(doc_dir, chapters)
    return chapters


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("doc_dir", help="redpen-publish/<docId> directory (contains metadata.json)")
    parser.add_argument("paragraphs_path", help="redpen-content/<docId>/paragraphs_list.txt path")
    args = parser.parse_args(argv)

    try:
        chapters = generate(args.doc_dir, args.paragraphs_path)
    except ChaptersError as e:
        print(f"[chapters] validation failed: {e}", file=sys.stderr)
        return 1

    if chapters is not None:
        sections = sum(len(c.get("sections") or []) for c in chapters)
        print(f"[chapters] wrote {len(chapters)} chapters / {sections} sections to {os.path.join(args.doc_dir, 'metadata.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
