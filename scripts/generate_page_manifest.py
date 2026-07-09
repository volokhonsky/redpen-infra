"""
Generate the `pages` manifest section of redpen-publish/<docId>/metadata.json
from the on-disk file layout (images/page_*.png) and the numbering rule in
redpen-content/<docId>/meta.json (stage 2 / B.2). See
docs/page-addressing-proposal.md and docs/agent-instructions-stage-2.md.

meta.json opts a document in via:

    "pageNumbering": {
      "frontMatter": ["-01", "000"],
      "printedStartFile": "001",
      "printedStartNumber": 1
    }

frontMatter entries are page file keys (no "page_" prefix/extension), in the
order they should be labeled A1, A2, .... Everything from printedStartFile
onward is labeled printedStartNumber, +1, +2, ... in file-key order.

If meta.json has no printedStartFile/printedStartNumber, the document is left
in legacy mode: nothing is written (this is expected for documents not yet
migrated, not an error). If the section IS present but inconsistent with the
files on disk (missing frontMatter file, uncovered page_*.png, duplicate
labels, gap in the printed sequence), generation fails loudly -- callers
(build_website.py, the CLI) should treat that as a build error.

Usage:
    python generate_page_manifest.py <redpen-publish-doc-dir> <meta-json-path> [--ignore key1,key2]
"""

import argparse
import json
import os
import re
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

PAGE_IMAGE_RE = re.compile(r"^page_(-?\d+)\.png$")


class ManifestError(ValueError):
    pass


def _front_matter_entry(entry: Any) -> Tuple[str, Optional[str]]:
    if isinstance(entry, str):
        return entry, None
    if isinstance(entry, dict) and isinstance(entry.get("file"), str):
        return entry["file"], entry.get("name")
    raise ManifestError(f"invalid frontMatter entry: {entry!r}")


def _scan_page_keys(doc_dir: str) -> List[str]:
    images_dir = os.path.join(doc_dir, "images")
    if not os.path.isdir(images_dir):
        raise ManifestError(f"images directory not found: {images_dir}")
    keys = []
    for name in os.listdir(images_dir):
        m = PAGE_IMAGE_RE.match(name)
        if m:
            keys.append(m.group(1))
    keys.sort(key=int)
    return keys


def build_manifest(doc_dir: str, numbering: Dict[str, Any], ignore: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Compute the pages manifest. Raises ManifestError on any validation failure."""
    ignore_set = set(ignore or [])
    front_matter_raw = numbering.get("frontMatter") or []
    printed_start_file = numbering.get("printedStartFile")
    printed_start_number = numbering.get("printedStartNumber")

    if printed_start_file is None or printed_start_number is None:
        raise ManifestError("pageNumbering.printedStartFile/printedStartNumber are required")

    front_entries = [_front_matter_entry(e) for e in front_matter_raw]
    front_keys = [key for key, _ in front_entries]

    if len(front_keys) != len(set(front_keys)):
        dupes = sorted({key for key in front_keys if front_keys.count(key) > 1})
        raise ManifestError(f"duplicate frontMatter entry: page_{dupes[0]}")

    available = set(_scan_page_keys(doc_dir))

    for key in front_keys:
        if key not in available:
            raise ManifestError(f"frontMatter file page_{key}.png not found in {doc_dir}/images")

    pages: List[Dict[str, Any]] = []
    seen_labels = set()

    for i, (key, name) in enumerate(front_entries, start=1):
        label = f"A{i}"
        if label in seen_labels:
            raise ManifestError(f"duplicate label: {label}")
        seen_labels.add(label)
        entry: Dict[str, Any] = {"file": f"page_{key}", "label": label}
        if name:
            entry["name"] = name
        pages.append(entry)

    remaining_keys = sorted(
        (key for key in available if key not in front_keys and key not in ignore_set),
        key=int,
    )

    if remaining_keys:
        printed_start_num = int(printed_start_file)
        first_num = int(remaining_keys[0])
        if first_num != printed_start_num:
            raise ManifestError(
                f"printed part must start at page_{printed_start_file}, "
                f"but the first uncovered page is page_{remaining_keys[0]} "
                f"(files before it are neither in frontMatter nor --ignore)"
            )
        for offset, key in enumerate(remaining_keys):
            expected = printed_start_num + offset
            if int(key) != expected:
                raise ManifestError(f"gap in the printed page sequence: expected file key {expected}, found {key}")
            label = str(printed_start_number + offset)
            if label in seen_labels:
                raise ManifestError(f"duplicate label: {label}")
            seen_labels.add(label)
            pages.append({"file": f"page_{key}", "label": label})

    return pages


def write_manifest(doc_dir: str, pages: List[Dict[str, Any]]) -> None:
    metadata_path = os.path.join(doc_dir, "metadata.json")
    if os.path.exists(metadata_path):
        with open(metadata_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)
    else:
        metadata = {}

    metadata["pages"] = pages
    metadata["totalPages"] = len(pages)

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


def generate(doc_dir: str, meta_path: str, ignore: Optional[List[str]] = None) -> Optional[List[Dict[str, Any]]]:
    """Returns the written manifest, or None if the document has no
    pageNumbering.printedStartFile/printedStartNumber section (legacy mode,
    nothing written -- this is expected, not an error)."""
    with open(meta_path, "r", encoding="utf-8") as f:
        content_meta = json.load(f)

    numbering = content_meta.get("pageNumbering")
    if not isinstance(numbering, dict) or "printedStartFile" not in numbering or "printedStartNumber" not in numbering:
        print(
            f"[generate_page_manifest] {meta_path} has no pageNumbering.printedStartFile/printedStartNumber; "
            f"leaving {doc_dir} in legacy mode (no pages manifest written)"
        )
        return None

    pages = build_manifest(doc_dir, numbering, ignore=ignore)
    write_manifest(doc_dir, pages)
    return pages


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("doc_dir", help="redpen-publish/<docId> directory (contains images/, metadata.json)")
    parser.add_argument("meta_path", help="redpen-content/<docId>/meta.json path")
    parser.add_argument(
        "--ignore",
        default="",
        help="Comma-separated file keys to exclude from the manifest (e.g. illustration files matching page_*)",
    )
    args = parser.parse_args(argv)

    ignore = [k.strip() for k in args.ignore.split(",") if k.strip()]
    try:
        pages = generate(args.doc_dir, args.meta_path, ignore=ignore)
    except ManifestError as e:
        print(f"[generate_page_manifest] validation failed: {e}", file=sys.stderr)
        return 1

    if pages is not None:
        print(f"[generate_page_manifest] wrote {len(pages)} pages to {os.path.join(args.doc_dir, 'metadata.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
