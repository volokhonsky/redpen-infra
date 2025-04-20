#!/usr/bin/env python3
"""
annotation_converter.py

A utility script for converting between JSON and Markdown annotation formats.

Usage:
    python annotation_converter.py json_to_md [path/to/json_dir] [path/to/md_dir]
    python annotation_converter.py md_to_json [path/to/md_dir] [path/to/json_dir]

Example:
    python annotation_converter.py json_to_md redpen-publish/annotations/ redpen-content/annotations/
    python annotation_converter.py md_to_json redpen-content/annotations/ redpen-publish/annotations/
"""

import sys
import os
import json
import glob
import re

def convert_json_to_md(json_file_path):
    """
    Convert a JSON annotation file to Markdown format according to the specification.

    Args:
        json_file_path: Path to the JSON file

    Returns:
        Markdown content as a string
    """
    with open(json_file_path, 'r', encoding='utf-8') as f:
        annotations = json.load(f)

    md_content = ""

    for i, annotation in enumerate(annotations):
        # Extract data from JSON
        annotation_id = annotation.get('id', '')
        target_block = annotation.get('targetBlock', '')
        text = annotation.get('text', '')
        ann_type = annotation.get('annType', '')
        coords = annotation.get('coords', [])

        # Start annotation with separator
        md_content += "---\n"

        # Add type field (mapping annType to type)
        md_content += f"type: {ann_type}\n"

        # Add id field if it exists
        if annotation_id:
            md_content += f"id: {annotation_id}\n"

        # Add target field based on the type of annotation and available data
        if ann_type != 'general':
            if coords:
                # If coords exist, use them as target
                md_content += f"target: [{coords[0]}, {coords[1]}]\n"
            elif target_block:
                # Otherwise use targetBlock as target
                md_content += f"target: {target_block}\n"

        # End metadata section with separator
        md_content += "---\n\n"

        # Add the annotation text
        md_content += f"{text}\n\n"

    return md_content

def parse_markdown_annotation(md_content):
    """
    Parse a markdown annotation file and extract annotations.

    Args:
        md_content (str): Content of the markdown file

    Returns:
        list: List of annotation dictionaries
    """
    # Split the content by the frontmatter delimiter
    sections = re.split(r'^---$', md_content, flags=re.MULTILINE)

    # Remove empty sections
    sections = [s.strip() for s in sections if s.strip()]

    annotations = []

    # Process sections in pairs (metadata + content)
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

        # Get annotation type
        ann_type = metadata_dict.get('type', '')

        # Create annotation object
        annotation = {
            "id": metadata_dict.get('id', ''),
            "text": content.strip(),
            "annType": ann_type
        }

        # Process target field if present and annotation type is not general
        if 'target' in metadata_dict and ann_type != 'general':
            target_value = metadata_dict['target'].strip()
            
            # Check if target is in the format [X, Y]
            coords_match = re.match(r'^\[(\d+),\s*(\d+)\]$', target_value)
            if coords_match:
                # If target contains coordinates, extract them
                x, y = map(int, coords_match.groups())
                annotation["coords"] = [x, y]
            else:
                # Otherwise, use it as targetBlock
                annotation["targetBlock"] = target_value

        annotations.append(annotation)

    return annotations

def json_to_md(json_dir, md_dir):
    """
    Convert JSON annotation files to Markdown format.

    Args:
        json_dir (str): Directory containing JSON annotation files
        md_dir (str): Directory to save the Markdown annotation files
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
    Convert Markdown annotation files to JSON format.

    Args:
        md_dir (str): Directory containing Markdown annotation files
        json_dir (str): Directory to save the JSON annotation files
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

        # Parse annotations
        annotations = parse_markdown_annotation(md_content)

        # Save as JSON
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(annotations, f, ensure_ascii=False, indent=2)

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
        source_dir = "redpen-publish/annotations"
        target_dir = "redpen-content/annotations"
    elif direction == "md_to_json":
        source_dir = "redpen-content/annotations"
        target_dir = "redpen-publish/annotations"
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