#!/usr/bin/env python3
"""
remark_converter.py

A utility script for converting between JSON and Markdown remark formats.

Usage:
    python remark_converter.py json_to_md [path/to/json_dir] [path/to/md_dir]
    python remark_converter.py md_to_json [path/to/md_dir] [path/to/json_dir]

Example:
    python remark_converter.py json_to_md redpen-publish/remarks/ redpen-content/remarks/
    python remark_converter.py md_to_json redpen-content/remarks/ redpen-publish/remarks/
"""

import sys
import os
import json
import glob
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import remark_kinds  # noqa: E402

#: Прежние значения вида замечания (до 2026-08-29).
LEGACY_KINDS = remark_kinds.LEGACY_KINDS

def convert_json_to_md(json_file_path):
    """
    Convert a JSON remark file to Markdown format according to the specification.

    Args:
        json_file_path: Path to the JSON file

    Returns:
        Markdown content as a string
    """
    with open(json_file_path, 'r', encoding='utf-8') as f:
        remarks = json.load(f)

    md_content = ""

    for i, remark in enumerate(remarks):
        # Extract data from JSON
        remark_id = remark.get('id', '')
        target_block = remark.get('targetBlock', '')
        text = remark.get('text', '')
        remark_kind = remark.get('kind', '')
        coords = remark.get('coords', [])

        # Start remark with meta block
        md_content += "~~~meta\n"

        # Вид замечания. Ключ `kind`, значения major/minor — с переименования
        # сущности 2026-08-29; прежние `type: main|comment` читаются, но больше
        # не пишутся.
        md_content += f"kind: {LEGACY_KINDS.get(remark_kind, remark_kind)}\n"

        # Add id field if it exists
        if remark_id:
            md_content += f"id: {remark_id}\n"

        # Add target field based on the available data. Every remark has an
        # anchor now -- the type without one ("general") is retired.
        if coords:
            # If coords exist, use them as target
            md_content += f"target: [{coords[0]}, {coords[1]}]\n"
        elif target_block:
            # Otherwise use targetBlock as target
            md_content += f"target: {target_block}\n"

        # Tags round-trip too, so json -> md -> json doesn't lose them
        category = remark.get('category')
        if category and category != 'other':
            md_content += f"category: {category}\n"
        tags = remark.get('tags') or []
        if tags:
            md_content += f"tags: [{', '.join(tags)}]\n"

        # End metadata section with separator
        md_content += "~~~\n\n"

        # Add the remark text
        md_content += f"{text}\n\n"

    return md_content

def parse_tags_field(raw):
    """Parse the meta `tags:` line into a list. The annotator agents write it
    as a bracketed list -- `tags: [omission, framing]` -- but a bare
    comma-separated line is accepted too. Order is kept, duplicates dropped."""
    if not raw:
        return []
    value = raw.strip()
    if value.startswith('[') and value.endswith(']'):
        value = value[1:-1]
    tags = []
    for part in value.split(','):
        tag = part.strip().strip('"\'').lower()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def parse_markdown_remark(md_content):
    """
    Parse a markdown remark file and extract remarks.

    Args:
        md_content (str): Content of the markdown file

    Returns:
        list: List of remark dictionaries
    """
    # Split the content by the meta block delimiter (supporting both old and new formats)
    # Хвостовые пробелы на строке-разделителе встречаются в черновиках; без
    # допуска на них замечание склеивается со следующим (стр. 006, 2026-08-16).
    sections = re.split(r'^[ \t]*(?:~~~meta|~~~|---)[ \t]*$', md_content,
                        flags=re.MULTILINE)

    # Remove empty sections
    sections = [s.strip() for s in sections if s.strip()]

    remarks = []

    # Process sections in pairs (metadata + content)
    # With the new format, we expect sections to alternate between metadata and content
    for i in range(0, len(sections), 2):
        if i + 1 >= len(sections):
            continue

        metadata = sections[i]
        content = sections[i + 1]

        # Parse metadata
        metadata_dict = {}
        for line in metadata.split('\n'):
            line = line.strip()
            if not line or ':' not in line:
                continue

            key, value = line.split(':', 1)
            metadata_dict[key.strip()] = value.strip()

        # Вид замечания: новый ключ `kind`, прежний `type` — для черновиков,
        # написанных до переименования. Значения нормализуются туда же.
        remark_kind = metadata_dict.get('kind') or metadata_dict.get('type', '')
        remark_kind = LEGACY_KINDS.get(remark_kind, remark_kind)
        if remark_kind == 'general':
            # Retired: an remark without an anchor on the scan has nowhere
            # to live now that every page is its own address. Converting it
            # silently would produce a marker-less remark that the viewer
            # cannot show at all, so fail loudly instead.
            raise ValueError(
                f"type: general больше не поддерживается (id={metadata_dict.get('id', '?')!r}). "
                "Проставьте target: [x, y] и kind: major|minor — см. docs/general-migration-map.json"
            )

        # Create remark object
        remark = {
            "id": metadata_dict.get('id', ''),
            "text": content.strip(),
            "kind": remark_kind
        }

        # Категория — своё поле (ровно одно, по умолчанию «Прочее»), а не тег.
        # В md она пишется отдельной строкой `category: <slug>`; пустая или
        # отсутствующая означает «приём не назначен».
        category = (metadata_dict.get('category') or '').strip().lower()
        if category:
            remark["category"] = category

        tags = parse_tags_field(metadata_dict.get('tags'))
        confidence = (metadata_dict.get('confidence') or '').strip().lower()
        if confidence:
            # `prefix:value` convention -- confidence is just another tag, so it
            # needs no column of its own (scripts/api/db.py, normalize_tag).
            tags.append('confidence:' + confidence)
        if tags:
            remark["tags"] = tags

        # Process target field if present
        if 'target' in metadata_dict:
            target_value = metadata_dict['target'].strip()

            # Check if target is in the format [X, Y]
            coords_match = re.match(r'^\[(\d+),\s*(\d+)\]$', target_value)
            if coords_match:
                # If target contains coordinates, extract them
                x, y = map(int, coords_match.groups())
                remark["coords"] = [x, y]
            else:
                # Otherwise, use it as targetBlock
                remark["targetBlock"] = target_value

        remarks.append(remark)

    return remarks

def json_to_md(json_dir, md_dir):
    """
    Convert JSON remark files to Markdown format.

    Args:
        json_dir (str): Directory containing JSON remark files
        md_dir (str): Directory to save the Markdown remark files
    """
    os.makedirs(md_dir, exist_ok=True)

    # Find all JSON files
    json_files = glob.glob(os.path.join(json_dir, "*.json"))

    for json_file in json_files:
        # Create the corresponding MD filename
        md_filename = os.path.basename(json_file).replace('.json', '.md')
        md_path = os.path.join(md_dir, md_filename)

        # Convert JSON to MD
        md_content = convert_json_to_md(json_file)

        # Write the MD file
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(md_content)

        print(f"[+] Converted {json_file} to {md_path}")

def md_to_json(md_dir, json_dir):
    """
    Convert Markdown remark files to JSON format.

    Args:
        md_dir (str): Directory containing Markdown remark files
        json_dir (str): Directory to save the JSON remark files
    """
    os.makedirs(json_dir, exist_ok=True)

    # Find all markdown files
    md_files = glob.glob(os.path.join(md_dir, "*.md"))

    for md_file in md_files:
        # Create the corresponding JSON filename
        json_filename = os.path.basename(md_file).replace('.md', '.json')
        json_path = os.path.join(json_dir, json_filename)

        # Load markdown content
        with open(md_file, "r", encoding="utf-8") as f:
            md_content = f.read()

        # Parse remarks
        remarks = parse_markdown_remark(md_content)

        # Save as JSON
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(remarks, f, ensure_ascii=False, indent=2)

        print(f"[+] Converted {md_file} to {json_path}")

def main():
    """
    Main function to process command line arguments and call the appropriate conversion function.
    """
    if len(sys.argv) < 2:
        print("Error: Missing conversion direction.")
        print(__doc__)
        sys.exit(1)

    direction = sys.argv[1]

    # Default directories
    if direction == "json_to_md":
        source_dir = "redpen-publish/remarks"
        target_dir = "redpen-content/remarks"
    elif direction == "md_to_json":
        source_dir = "redpen-content/remarks"
        target_dir = "redpen-publish/remarks"
    else:
        print(f"Error: Unknown conversion direction '{direction}'.")
        print(__doc__)
        sys.exit(1)

    # Override with command line arguments if provided
    if len(sys.argv) >= 3:
        source_dir = sys.argv[2]
    if len(sys.argv) >= 4:
        target_dir = sys.argv[3]

    if direction == "json_to_md":
        json_to_md(source_dir, target_dir)
    else:  # md_to_json
        md_to_json(source_dir, target_dir)

if __name__ == "__main__":
    main()
