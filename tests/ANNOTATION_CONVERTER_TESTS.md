# Annotation Converter Tests

This directory contains automated tests for the `annotation_converter.py` script, which converts between JSON and Markdown annotation formats.

## Overview

The tests verify that the conversion between JSON and Markdown formats works correctly in both directions:
- JSON to Markdown
- Markdown to JSON
- Round-trip conversion (JSON -> MD -> JSON)

The tests use sample files stored in the `test_data` directory, which include:
- Examples of all annotation types (general, main, comment)
- Examples with and without coordinates
- Empty files

## Running the Tests

To run the tests, use the following command:

```bash
python -m unittest tests/test_annotation_converter.py
```

This will run all the tests and report the results. If all tests pass, you should see output similar to:

```
.....
----------------------------------------------------------------------
Ran 5 tests in 0.017s

OK
```

## Test Cases

The test suite includes the following test cases:

### Test 1: JSON to Markdown Conversion
- Tests the `convert_json_to_md` function
- Verifies that JSON files are correctly converted to Markdown format
- Tests both files with annotations and empty files

### Test 2: Markdown Parsing
- Tests the `parse_markdown_annotation` function
- Verifies that Markdown files are correctly parsed into annotation objects
- Tests both files with annotations and empty files

### Test 3: Directory-based JSON to Markdown Conversion
- Tests the `json_to_md` function
- Verifies that all JSON files in a directory are correctly converted to Markdown
- Tests both files with annotations and empty files

### Test 4: Directory-based Markdown to JSON Conversion
- Tests the `md_to_json` function
- Verifies that all Markdown files in a directory are correctly converted to JSON
- Tests both files with annotations and empty files

### Test 5: Round-trip Conversion
- Tests the full conversion cycle: JSON -> Markdown -> JSON
- Verifies that the final JSON matches the original JSON
- Tests both files with annotations and empty files

## Test Data

The test data is stored in the `test_data` directory and includes:

### test_annotations.md
- Contains examples of all annotation types:
  - A "general" annotation without a target
  - A "main" annotation with a target block
  - A "comment" annotation with coordinates

### test_annotations.json
- The JSON representation of the annotations in `test_annotations.md`

### empty_annotations.md
- An empty Markdown file

### empty_annotations.json
- An empty JSON file (contains an empty array)

## Adding More Tests

To add more test cases:
1. Add new test files to the `test_data` directory
2. Add new test methods to the `TestAnnotationConverter` class in `test_annotation_converter.py`
3. Run the tests to verify that they pass

## Troubleshooting

If a test fails, check:
1. The error message to understand what went wrong
2. The test data files to ensure they are correctly formatted
3. The `annotation_converter.py` script to ensure it's handling the conversion correctly