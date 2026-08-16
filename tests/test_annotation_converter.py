#!/usr/bin/env python3
"""
test_annotation_converter.py

Automated tests for the annotation_converter.py script.

This script tests the conversion between JSON and Markdown annotation formats
in both directions, ensuring that the conversion is correct and consistent.

Usage:
    python -m unittest tests/test_annotation_converter.py
"""

import unittest
import os
import json
import sys
import tempfile
import shutil

# Add the scripts directory to the Python path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'scripts'))

# Import the functions from the annotation_converter.py script
from annotation_converter import (
    convert_json_to_md,
    parse_markdown_annotation,
    parse_tags_field,
    json_to_md,
    md_to_json
)


class TestTags(unittest.TestCase):
    """tags:/confidence: used to be parsed and then dropped; they now survive
    into the JSON so the import can put them in the DB."""

    def test_parse_tags_field_accepts_bracketed_and_bare_lists(self):
        self.assertEqual(parse_tags_field('[omission, framing]'), ['omission', 'framing'])
        self.assertEqual(parse_tags_field('omission, framing'), ['omission', 'framing'])
        self.assertEqual(parse_tags_field('[Omission, omission]'), ['omission'])
        self.assertEqual(parse_tags_field(''), [])
        self.assertEqual(parse_tags_field(None), [])

    def test_meta_tags_and_confidence_reach_the_annotation(self):
        md = (
            "~~~meta\n"
            "type: main\n"
            "id: ann-p107-1\n"
            "target: [350, 255]\n"
            "tags: [omission, tc-usa-origin]\n"
            "confidence: high\n"
            "~~~\n\n"
            "Текст аннотации\n"
        )
        ann = parse_markdown_annotation(md)[0]
        self.assertEqual(ann['tags'], ['omission', 'tc-usa-origin', 'confidence:high'])
        self.assertEqual(ann['coords'], [350, 255])

    def test_annotation_without_tags_has_no_tags_key(self):
        md = "~~~meta\ntype: comment\nid: ann-1\n~~~\n\nТекст\n"
        self.assertNotIn('tags', parse_markdown_annotation(md)[0])

    def test_tags_round_trip_through_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, 'page_007.json')
            with open(src, 'w', encoding='utf-8') as f:
                json.dump([{"id": "a1", "text": "t", "annType": "main",
                            "coords": [1, 2], "tags": ["omission", "framing"]}], f)
            back = parse_markdown_annotation(convert_json_to_md(src))[0]
            self.assertEqual(back['tags'], ['omission', 'framing'])

class TestAnnotationConverter(unittest.TestCase):
    """Test cases for the annotation_converter.py script."""

    def setUp(self):
        """Set up the test environment."""
        self.test_data_dir = os.path.join(os.path.dirname(__file__), 'test_data')
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        """Clean up the test environment."""
        shutil.rmtree(self.temp_dir)

    def test_convert_json_to_md(self):
        """Test the conversion from JSON to Markdown."""
        # Test with a file containing annotations
        json_file = os.path.join(self.test_data_dir, 'test_annotations.json')
        expected_md_file = os.path.join(self.test_data_dir, 'test_annotations.md')

        # Convert JSON to Markdown
        md_content = convert_json_to_md(json_file)

        # Read the expected Markdown content
        with open(expected_md_file, 'r', encoding='utf-8') as f:
            expected_md_content = f.read()

        # Compare the converted content with the expected content
        self.assertEqual(md_content.strip(), expected_md_content.strip())

        # Test with an empty file
        json_file = os.path.join(self.test_data_dir, 'empty_annotations.json')
        expected_md_file = os.path.join(self.test_data_dir, 'empty_annotations.md')

        # Convert JSON to Markdown
        md_content = convert_json_to_md(json_file)

        # Read the expected Markdown content
        with open(expected_md_file, 'r', encoding='utf-8') as f:
            expected_md_content = f.read()

        # Compare the converted content with the expected content
        self.assertEqual(md_content.strip(), expected_md_content.strip())

    def test_parse_markdown_annotation(self):
        """Test parsing Markdown annotations."""
        # Test with a file containing annotations
        md_file = os.path.join(self.test_data_dir, 'test_annotations.md')
        expected_json_file = os.path.join(self.test_data_dir, 'test_annotations.json')

        # Read the Markdown content
        with open(md_file, 'r', encoding='utf-8') as f:
            md_content = f.read()

        # Parse the Markdown content
        annotations = parse_markdown_annotation(md_content)

        # Read the expected JSON content
        with open(expected_json_file, 'r', encoding='utf-8') as f:
            expected_annotations = json.load(f)

        # Compare the parsed annotations with the expected annotations
        self.assertEqual(annotations, expected_annotations)

        # Test with an empty file
        md_file = os.path.join(self.test_data_dir, 'empty_annotations.md')
        expected_json_file = os.path.join(self.test_data_dir, 'empty_annotations.json')

        # Read the Markdown content
        with open(md_file, 'r', encoding='utf-8') as f:
            md_content = f.read()

        # Parse the Markdown content
        annotations = parse_markdown_annotation(md_content)

        # Read the expected JSON content
        with open(expected_json_file, 'r', encoding='utf-8') as f:
            expected_annotations = json.load(f)

        # Compare the parsed annotations with the expected annotations
        self.assertEqual(annotations, expected_annotations)

    def test_fence_with_trailing_whitespace_still_separates(self):
        """Пробел после закрывающего ~~~ не должен склеивать аннотации."""
        md_content = (
            "~~~meta\n"
            "type: main\n"
            "id: ann-1\n"
            "target: [300,650]\n"
            "~~~ \n"
            "\nПервая.\n\n"
            "~~~meta\n"
            "type: main\n"
            "id: ann-2\n"
            "target: [650,950]\n"
            "~~~\n"
            "Вторая.\n"
        )

        annotations = parse_markdown_annotation(md_content)

        self.assertEqual([a['id'] for a in annotations], ['ann-1', 'ann-2'])
        self.assertEqual(annotations[0]['text'], 'Первая.')
        self.assertEqual(annotations[1]['text'], 'Вторая.')

    def test_json_to_md_directory(self):
        """Test the conversion from JSON to Markdown for a directory."""
        # Create temporary directories for input and output
        json_dir = os.path.join(self.temp_dir, 'json')
        md_dir = os.path.join(self.temp_dir, 'md')
        os.makedirs(json_dir, exist_ok=True)
        os.makedirs(md_dir, exist_ok=True)

        # Copy test JSON files to the input directory
        for filename in ['test_annotations.json', 'empty_annotations.json']:
            src = os.path.join(self.test_data_dir, filename)
            dst = os.path.join(json_dir, filename)
            shutil.copy2(src, dst)

        # Convert JSON to Markdown
        json_to_md(json_dir, md_dir)

        # Check that the output files exist and have the correct content
        for filename in ['test_annotations.md', 'empty_annotations.md']:
            expected_file = os.path.join(self.test_data_dir, filename)
            output_file = os.path.join(md_dir, filename)

            # Check that the file exists
            self.assertTrue(os.path.exists(output_file))

            # Read the expected and actual content
            with open(expected_file, 'r', encoding='utf-8') as f:
                expected_content = f.read().strip()
            with open(output_file, 'r', encoding='utf-8') as f:
                actual_content = f.read().strip()

            # Compare the content
            self.assertEqual(actual_content, expected_content)

    def test_md_to_json_directory(self):
        """Test the conversion from Markdown to JSON for a directory."""
        # Create temporary directories for input and output
        md_dir = os.path.join(self.temp_dir, 'md')
        json_dir = os.path.join(self.temp_dir, 'json')
        os.makedirs(md_dir, exist_ok=True)
        os.makedirs(json_dir, exist_ok=True)

        # Copy test Markdown files to the input directory
        for filename in ['test_annotations.md', 'empty_annotations.md']:
            src = os.path.join(self.test_data_dir, filename)
            dst = os.path.join(md_dir, filename)
            shutil.copy2(src, dst)

        # Convert Markdown to JSON
        md_to_json(md_dir, json_dir)

        # Check that the output files exist and have the correct content
        for filename in ['test_annotations.json', 'empty_annotations.json']:
            expected_file = os.path.join(self.test_data_dir, filename)
            output_file = os.path.join(json_dir, filename)

            # Check that the file exists
            self.assertTrue(os.path.exists(output_file))

            # Read the expected and actual content
            with open(expected_file, 'r', encoding='utf-8') as f:
                expected_content = json.load(f)
            with open(output_file, 'r', encoding='utf-8') as f:
                actual_content = json.load(f)

            # Compare the content
            self.assertEqual(actual_content, expected_content)

    def test_round_trip_conversion(self):
        """Test round-trip conversion (JSON -> MD -> JSON)."""
        # Create temporary directories for intermediate and final output
        json_dir = os.path.join(self.temp_dir, 'json')
        md_dir = os.path.join(self.temp_dir, 'md')
        json_final_dir = os.path.join(self.temp_dir, 'json_final')
        os.makedirs(json_dir, exist_ok=True)
        os.makedirs(md_dir, exist_ok=True)
        os.makedirs(json_final_dir, exist_ok=True)

        # Copy test JSON files to the input directory
        for filename in ['test_annotations.json', 'empty_annotations.json']:
            src = os.path.join(self.test_data_dir, filename)
            dst = os.path.join(json_dir, filename)
            shutil.copy2(src, dst)

        # Convert JSON to Markdown
        json_to_md(json_dir, md_dir)

        # Convert Markdown back to JSON
        md_to_json(md_dir, json_final_dir)

        # Check that the final JSON files match the original JSON files
        for filename in ['test_annotations.json', 'empty_annotations.json']:
            original_file = os.path.join(json_dir, filename)
            final_file = os.path.join(json_final_dir, filename)

            # Read the original and final content
            with open(original_file, 'r', encoding='utf-8') as f:
                original_content = json.load(f)
            with open(final_file, 'r', encoding='utf-8') as f:
                final_content = json.load(f)

            # Compare the content
            self.assertEqual(final_content, original_content)

if __name__ == '__main__':
    unittest.main()