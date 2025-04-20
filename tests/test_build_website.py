#!/usr/bin/env python3
"""
Test script for the build_website.py script with a target directory.

This script tests the build_website.py script by running it with a temporary
target directory and verifying that the website is built correctly.
"""

import os
import sys
import tempfile
import shutil
import importlib.util
import argparse

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(project_root)

def import_module_from_file(module_name, file_path):
    """Import a module from a file path"""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def test_build_website():
    """Test the build_website.py script with a target directory"""
    print("\n=== Testing build_website.py with a target directory ===")

    # Create a temporary directory for the build output
    temp_dir = tempfile.mkdtemp(prefix="redpen_test_build_")
    print(f"Created temporary directory: {temp_dir}")

    try:
        # Import the build_website module
        build_website_path = os.path.join(project_root, 'scripts', 'build_website.py')
        build_website = import_module_from_file("build_website", build_website_path)

        # Run the build steps with the temporary directory
        print(f"Converting annotations to {temp_dir}...")
        if not build_website.convert_annotations(temp_dir):
            print("Failed to convert annotations")
            return False

        print(f"Publishing website data to {temp_dir}...")
        if not build_website.publish_website_data(temp_dir):
            print("Failed to publish website data")
            return False

        # Verify that the website was built correctly
        print("\n=== Verifying build output ===")

        # Check for annotations directory
        annotations_dir = os.path.join(temp_dir, 'annotations')
        if not os.path.exists(annotations_dir):
            print(f"Error: Annotations directory not found: {annotations_dir}")
            return False

        # Check for images directory
        images_dir = os.path.join(temp_dir, 'images')
        if not os.path.exists(images_dir):
            print(f"Error: Images directory not found: {images_dir}")
            return False

        # Check for text directory
        text_dir = os.path.join(temp_dir, 'text')
        if not os.path.exists(text_dir):
            print(f"Error: Text directory not found: {text_dir}")
            return False

        # Check for at least one annotation file
        annotation_files = os.listdir(annotations_dir)
        if not annotation_files:
            print(f"Error: No annotation files found in {annotations_dir}")
            return False

        print(f"Found {len(annotation_files)} annotation files")

        # Check for at least one image file
        image_files = os.listdir(images_dir)
        if not image_files:
            print(f"Error: No image files found in {images_dir}")
            return False

        print(f"Found {len(image_files)} image files")

        # Check for at least one text file
        text_files = os.listdir(text_dir)
        if not text_files:
            print(f"Error: No text files found in {text_dir}")
            return False

        print(f"Found {len(text_files)} text files")

        # Check for CSS directory
        css_dir = os.path.join(temp_dir, 'css')
        if not os.path.exists(css_dir):
            print(f"Error: CSS directory not found: {css_dir}")
            return False

        # Check for at least one CSS file
        css_files = os.listdir(css_dir)
        if not css_files:
            print(f"Error: No CSS files found in {css_dir}")
            return False

        print(f"Found {len(css_files)} CSS files")

        # Check for JS directory
        js_dir = os.path.join(temp_dir, 'js')
        if not os.path.exists(js_dir):
            print(f"Error: JS directory not found: {js_dir}")
            return False

        # Check for at least one JS file
        js_files = os.listdir(js_dir)
        if not js_files:
            print(f"Error: No JS files found in {js_dir}")
            return False

        print(f"Found {len(js_files)} JS files")

        # Check for index.html
        index_html = os.path.join(temp_dir, 'index.html')
        if not os.path.exists(index_html):
            print(f"Error: index.html not found: {index_html}")
            return False

        print("Found index.html")

        # Check for favicon.svg
        favicon_svg = os.path.join(temp_dir, 'favicon.svg')
        if not os.path.exists(favicon_svg):
            print(f"Error: favicon.svg not found: {favicon_svg}")
            return False

        print("Found favicon.svg")

        print("\n=== Build verification successful ===")
        return True

    finally:
        # Clean up the temporary directory
        print(f"Cleaning up temporary directory: {temp_dir}")
        shutil.rmtree(temp_dir)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test the build_website.py script")
    args = parser.parse_args()

    success = test_build_website()

    if success:
        print("\n=== Test completed successfully ===")
        sys.exit(0)
    else:
        print("\n=== Test failed ===")
        sys.exit(1)
