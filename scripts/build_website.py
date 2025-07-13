#!/usr/bin/env python3
"""
build_website.py

A comprehensive script for building and publishing the website.

This script:
1. Converts markdown annotations to JSON
2. Runs annotation position tests to verify correct positioning
3. Publishes data (images, text, annotations) to the target directory (default: redpen-publish)
4. Commits and pushes changes to the redpen-publish submodule (if target is redpen-publish)

Usage:
    python scripts/build_website.py [--skip-tests] [--skip-push] [--target-dir TARGET_DIR] [--document DOCUMENT] [--folders FOLDERS]

Options:
    --skip-tests    Skip running annotation position tests
    --skip-push     Skip pushing changes to the redpen-publish submodule
    --target-dir    Specify a target directory for the build output (default: redpen-publish)
    --document      Specify a document to build (default: build all documents)
    --folders       Comma-separated list of specific folders to deploy (default: all folders)
"""

import os
import sys
import argparse
import subprocess
import importlib.util
import shutil
import glob
import datetime

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(project_root)

def get_document_folders(specific_folders=None):
    """
    Get a list of document folders from redpen-content directory.

    Args:
        specific_folders (list): Optional list of specific folders to include.
                                If provided, only these folders will be returned if they exist.

    Returns:
        list: List of document folder names
    """
    content_dir = os.path.join(project_root, 'redpen-content')

    # If specific folders are provided, filter by them
    if specific_folders:
        folders = []
        for folder in specific_folders:
            folder_path = os.path.join(content_dir, folder)
            if os.path.isdir(folder_path):
                folders.append(folder)
            else:
                print(f"Warning: Specified folder '{folder}' not found in redpen-content")
        return folders

    # Otherwise, get all folders in the content directory
    folders = []
    for item in glob.glob(os.path.join(content_dir, '*')):
        if os.path.isdir(item):
            folder_name = os.path.basename(item)
            # Check if this is a valid document folder (has images, text, or annotations subdirectory)
            if any(os.path.isdir(os.path.join(item, subdir)) for subdir in ['images', 'text', 'annotations']):
                folders.append(folder_name)

    if not folders:
        print("Warning: No document folders found in redpen-content directory")

    return folders

# Import required modules
def import_module_from_file(module_name, file_path):
    """Import a module from a file path"""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# Import the annotation converter and publish_data modules
annotation_converter = import_module_from_file(
    'annotation_converter', 
    os.path.join(project_root, 'scripts', 'annotation_converter.py')
)
publish_data = import_module_from_file(
    'publish_data',
    os.path.join(project_root, 'scripts', 'publish_data.py')
)

def run_command(command, cwd=None):
    """Run a shell command and return the result"""
    print(f"Running command: {command}")
    result = subprocess.run(
        command, 
        shell=True, 
        cwd=cwd or project_root,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print(f"Command failed with exit code {result.returncode}")
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
        return False, result.stdout, result.stderr

    return True, result.stdout, result.stderr

def convert_annotations(target_dir=None, document=None, specific_folders=None):
    """
    Convert markdown annotations to JSON

    Args:
        target_dir (str): Target directory for output
        document (str): Specific document to convert
        specific_folders (list): List of specific folders to convert
    """
    print(f"\n=== Converting Markdown Annotations to JSON for {document or (', '.join(specific_folders) if specific_folders else 'all documents')} ===")

    # If document is specified, only convert that document's annotations
    if document:
        md_dir = os.path.join(project_root, 'redpen-content', document, 'annotations')

        # Use target_dir if provided, otherwise use default redpen-publish
        if target_dir:
            # Place annotations directly in the document directory
            json_dir = os.path.join(target_dir, document, 'annotations')
        else:
            json_dir = os.path.join(project_root, 'redpen-publish', document, 'annotations')

        # Create the directory if it doesn't exist
        os.makedirs(json_dir, exist_ok=True)

        try:
            annotation_converter.md_to_json(md_dir, json_dir)
            return True
        except Exception as e:
            print(f"Error converting annotations for {document}: {e}")
            return False
    else:
        # Convert annotations for all documents or specific folders
        documents = get_document_folders(specific_folders)
        success = True

        for doc in documents:
            md_dir = os.path.join(project_root, 'redpen-content', doc, 'annotations')

            # Use target_dir if provided, otherwise use default redpen-publish
            if target_dir:
                # Place annotations directly in the document directory
                json_dir = os.path.join(target_dir, doc, 'annotations')
            else:
                json_dir = os.path.join(project_root, 'redpen-publish', doc, 'annotations')

            # Create the directory if it doesn't exist
            os.makedirs(json_dir, exist_ok=True)

            try:
                annotation_converter.md_to_json(md_dir, json_dir)
            except Exception as e:
                print(f"Error converting annotations for {doc}: {e}")
                success = False

        return success

def run_annotation_tests(target_dir=None):
    """Run annotation position tests"""
    print("\n=== Running Annotation Position Tests ===")

    # Import the annotation_position_tests module
    tests_module = import_module_from_file(
        'annotation_position_tests',
        os.path.join(project_root, 'tests', 'annotation_position_tests.py')
    )

    # Create test files in the target directory
    print("\n=== Creating Test Files for Annotation Tests ===")
    tests_module.create_test_files(target_dir or os.path.join(project_root, 'redpen-publish'))

    try:
        # Run the tests with a custom function that captures the result
        class TestResult:
            def __init__(self):
                self.all_pass = None

        result = TestResult()

        # Monkey patch the run_tests function to capture the result
        original_run_tests = tests_module.run_tests

        def patched_run_tests(update_baseline=False):
            # Load baseline positions
            baseline = tests_module.load_baseline_positions()

            # Get the base path and publish directory
            base_path = os.path.abspath(os.path.join(os.path.dirname(tests_module.__file__), '..'))

            # Use target_dir if provided, otherwise use default redpen-publish
            if target_dir:
                publish_dir = target_dir
            else:
                publish_dir = os.path.join(base_path, 'redpen-publish')

            # Save current directory to restore it later
            original_dir = os.getcwd()

            # Start a local HTTP server
            port = tests_module.find_free_port()
            print(f"Starting HTTP server on port {port}...")
            server = tests_module.start_http_server(publish_dir, port)

            try:
                with tests_module.sync_playwright() as p:
                    # Test 1: Desktop width (1280px)
                    print("\n=== Test 1: Desktop Width (1280px) ===")
                    desktop_results = tests_module.test_desktop_width(p, port)

                    # Test 2: Mobile width (800px)
                    print("\n=== Test 2: Mobile Width (800px) ===")
                    mobile_results = tests_module.test_mobile_width(p, port)

                    # Test 3: Resize from desktop to mobile
                    print("\n=== Test 3: Resize from Desktop to Mobile ===")
                    desktop_to_mobile_results = tests_module.test_resize_desktop_to_mobile(p, port)

                    # Test 4: Resize from mobile to desktop
                    print("\n=== Test 4: Resize from Mobile to Desktop ===")
                    mobile_to_desktop_results = tests_module.test_resize_mobile_to_desktop(p, port)

                    # Update baseline if requested
                    if update_baseline:
                        baseline = {
                            'desktop': desktop_results,
                            'mobile': mobile_results,
                            'desktop_to_mobile': desktop_to_mobile_results,
                            'mobile_to_desktop': mobile_to_desktop_results
                        }
                        tests_module.save_baseline_positions(baseline)
                        print("\nBaseline positions updated")
                    else:
                        # Compare with baseline
                        print("\n=== Comparing with Baseline ===")
                        desktop_match = tests_module.compare_positions(desktop_results, baseline.get('desktop', []))
                        mobile_match = tests_module.compare_positions(mobile_results, baseline.get('mobile', []))
                        desktop_to_mobile_match = tests_module.compare_positions(desktop_to_mobile_results, baseline.get('desktop_to_mobile', []))
                        mobile_to_desktop_match = tests_module.compare_positions(mobile_to_desktop_results, baseline.get('mobile_to_desktop', []))

                        # Print overall results
                        print("\n=== Overall Results ===")
                        print(f"Desktop width test: {'PASS' if desktop_match else 'FAIL'}")
                        print(f"Mobile width test: {'PASS' if mobile_match else 'FAIL'}")
                        print(f"Desktop to mobile resize test: {'PASS' if desktop_to_mobile_match else 'FAIL'}")
                        print(f"Mobile to desktop resize test: {'PASS' if mobile_to_desktop_match else 'FAIL'}")

                        result.all_pass = desktop_match and mobile_match and desktop_to_mobile_match and mobile_to_desktop_match
                        print(f"\nOverall: {'PASS' if result.all_pass else 'FAIL'}")
            finally:
                # Restore original directory
                os.chdir(original_dir)

                # Shutdown the server
                server.shutdown()

        # Replace the original function with our patched version
        tests_module.run_tests = patched_run_tests

        # Run the tests
        tests_module.run_tests(update_baseline=False)

        # Restore the original function
        tests_module.run_tests = original_run_tests

        return result.all_pass
    except Exception as e:
        print(f"Error running annotation tests: {e}")
        return False

def publish_website_data(target_dir=None, document=None, specific_folders=None):
    """
    Publish data to the target directory

    Args:
        target_dir (str): Target directory for output
        document (str): Specific document to publish
        specific_folders (list): List of specific folders to publish
    """
    print(f"\n=== Publishing Website Data for {document or (', '.join(specific_folders) if specific_folders else 'all documents')} ===")

    # Template directories
    templates_dir = os.path.join(project_root, 'templates')

    # Use target_dir if provided, otherwise use default redpen-publish
    if target_dir:
        output_dir = target_dir
    else:
        output_dir = os.path.join(project_root, 'redpen-publish')

    # Create the directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    try:
        # If document is specified, only publish that document's data
        if document:
            images_dir = os.path.join(project_root, 'redpen-content', document, 'images')
            text_dir = os.path.join(project_root, 'redpen-content', document, 'text')
            # Skip annotations as they're already converted and in the right place
            annotations_dir = None

            # Create document-specific output directory
            doc_output_dir = os.path.join(output_dir, document)
            os.makedirs(doc_output_dir, exist_ok=True)

            # Use the document directory directly for content
            doc_content_dir = doc_output_dir

            # Publish content data directly to the document directory
            publish_data.publish_data(images_dir, text_dir, annotations_dir, doc_content_dir)

            # Check if illustrations folder exists and publish its content to images folder
            illustrations_dir = os.path.join(project_root, 'redpen-content', document, 'illustrations')
            if os.path.exists(illustrations_dir) and os.path.isdir(illustrations_dir):
                images_output = os.path.join(doc_content_dir, "images")
                publish_data.copy_files(illustrations_dir, images_output, "*")
                print(f"[+] Published illustrations from {illustrations_dir} to {images_output}")

            # Copy meta.json to the document directory as metadata.json
            meta_json_path = os.path.join(project_root, 'redpen-content', document, 'meta.json')
            metadata_json_path = os.path.join(doc_content_dir, 'metadata.json')
            if os.path.exists(meta_json_path):
                shutil.copy2(meta_json_path, metadata_json_path)
                print(f"[+] Copied meta.json to {metadata_json_path}")

            # Copy document index template to the document directory with updated timestamp
            document_template = os.path.join(templates_dir, 'document_index.html')
            document_index = os.path.join(doc_content_dir, 'index.html')
            if os.path.exists(document_template):
                # Read the template content
                with open(document_template, 'r', encoding='utf-8') as f:
                    template_content = f.read()

                # Replace the timestamp with current date and time
                current_timestamp = datetime.datetime.now().strftime('%d.%m.%Y %H:%M')
                template_content = template_content.replace('Последнее обновление: 15.05.2023 14:30', f'Последнее обновление: {current_timestamp}')

                # Write the modified content to the output file
                with open(document_index, 'w', encoding='utf-8') as f:
                    f.write(template_content)

                print(f"[+] Copied document index template to {document_index} with updated timestamp")

            # No need for redirect HTML file anymore as we're using the document directory directly
            # create_redirect_html(doc_output_dir, f"i/{document}")

            # Clean up only the old nested structure
            old_dirs = ['i']
            for old_dir in old_dirs:
                old_path = os.path.join(doc_output_dir, old_dir)
                if os.path.exists(old_path) and os.path.isdir(old_path):
                    try:
                        shutil.rmtree(old_path)
                        print(f"[+] Removed old directory: {old_path}")
                    except Exception as e:
                        print(f"[!] Error removing directory {old_path}: {e}")
        else:
            # Publish data for all documents or specific folders
            documents = get_document_folders(specific_folders)

            for doc in documents:
                images_dir = os.path.join(project_root, 'redpen-content', doc, 'images')
                text_dir = os.path.join(project_root, 'redpen-content', doc, 'text')
                # Skip annotations as they're already converted and in the right place
                annotations_dir = None

                # Create document-specific output directory
                doc_output_dir = os.path.join(output_dir, doc)
                os.makedirs(doc_output_dir, exist_ok=True)

                # Use the document directory directly for content
                doc_content_dir = doc_output_dir

                # Publish content data directly to the document directory
                publish_data.publish_data(images_dir, text_dir, annotations_dir, doc_content_dir)

                # Check if illustrations folder exists and publish its content to images folder
                illustrations_dir = os.path.join(project_root, 'redpen-content', doc, 'illustrations')
                if os.path.exists(illustrations_dir) and os.path.isdir(illustrations_dir):
                    images_output = os.path.join(doc_content_dir, "images")
                    publish_data.copy_files(illustrations_dir, images_output, "*")
                    print(f"[+] Published illustrations from {illustrations_dir} to {images_output}")

                # Copy meta.json to the document directory as metadata.json
                meta_json_path = os.path.join(project_root, 'redpen-content', doc, 'meta.json')
                metadata_json_path = os.path.join(doc_content_dir, 'metadata.json')
                if os.path.exists(meta_json_path):
                    shutil.copy2(meta_json_path, metadata_json_path)
                    print(f"[+] Copied meta.json to {metadata_json_path}")

                # Copy document index template to the document directory with updated timestamp
                document_template = os.path.join(templates_dir, 'document_index.html')
                document_index = os.path.join(doc_content_dir, 'index.html')
                document_index_html = os.path.join(doc_content_dir, 'document_index.html')
                if os.path.exists(document_template):
                    # Read the template content
                    with open(document_template, 'r', encoding='utf-8') as f:
                        template_content = f.read()

                    # Replace the timestamp with current date and time
                    current_timestamp = datetime.datetime.now().strftime('%d.%m.%Y %H:%M')
                    template_content = template_content.replace('Последнее обновление: 15.05.2023 14:30', f'Последнее обновление: {current_timestamp}')

                    # Write the modified content to the output files
                    with open(document_index, 'w', encoding='utf-8') as f:
                        f.write(template_content)

                    # Also create document_index.html for compatibility with tests
                    with open(document_index_html, 'w', encoding='utf-8') as f:
                        f.write(template_content)

                    print(f"[+] Copied document index template to {document_index} with updated timestamp")
                    print(f"[+] Also created document_index.html at {document_index_html} for test compatibility")

                # No need for redirect HTML file anymore as we're using the document directory directly
                # create_redirect_html(doc_output_dir, f"i/{doc}")

                # Clean up only the old nested structure
                old_dirs = ['i']
                for old_dir in old_dirs:
                    old_path = os.path.join(doc_output_dir, old_dir)
                    if os.path.exists(old_path) and os.path.isdir(old_path):
                        try:
                            shutil.rmtree(old_path)
                            print(f"[+] Removed old directory: {old_path}")
                        except Exception as e:
                            print(f"[!] Error removing directory {old_path}: {e}")

        # Copy template files (CSS, JS, HTML, etc.)
        print("\n=== Copying Template Files ===")
        # Copy CSS files
        css_src = os.path.join(templates_dir, 'css')
        css_dest = os.path.join(output_dir, 'css')
        if os.path.exists(css_src):
            publish_data.copy_files(css_src, css_dest, "*.css")

        # Copy JS files
        js_src = os.path.join(templates_dir, 'js')
        js_dest = os.path.join(output_dir, 'js')
        if os.path.exists(js_src):
            publish_data.copy_files(js_src, js_dest, "*.js")

        # Copy favicon
        if os.path.exists(templates_dir):
            publish_data.copy_files(templates_dir, output_dir, "*.svg")

        return True
    except Exception as e:
        print(f"Error publishing data: {e}")
        return False

def create_index_page(target_dir=None, specific_folders=None):
    """
    Create the main index page with document selection menu

    Args:
        target_dir (str): Target directory for output
        specific_folders (list): List of specific folders to include in the index
    """
    print("\n=== Creating Index Page with Document Selection Menu ===")

    # Use target_dir if provided, otherwise use default redpen-publish
    if target_dir:
        output_dir = target_dir
    else:
        output_dir = os.path.join(project_root, 'redpen-publish')

    # Create the index.html file
    index_path = os.path.join(output_dir, 'index.html')

    # Also create document_index.html at the root level for test compatibility
    document_index_path = os.path.join(output_dir, 'document_index.html')

    # Get the list of document folders
    document_folders = get_document_folders(specific_folders)

    # Create document entries with titles from meta.json if available
    documents = []
    for doc_id in document_folders:
        # Default title if meta.json is not available
        title = doc_id
        icon_path = None

        # Try to get title from meta.json
        meta_json_path = os.path.join(project_root, 'redpen-content', doc_id, 'meta.json')
        if os.path.exists(meta_json_path):
            try:
                import json
                with open(meta_json_path, 'r', encoding='utf-8') as f:
                    meta_data = json.load(f)
                    if 'title' in meta_data:
                        title = meta_data['title']
            except Exception as e:
                print(f"Warning: Could not read title from meta.json for {doc_id}: {e}")

        # Find the first PNG image in the book's images directory
        images_dir = os.path.join(project_root, 'redpen-content', doc_id, 'images')
        if os.path.exists(images_dir):
            try:
                from PIL import Image
                png_files = sorted([f for f in os.listdir(images_dir) if f.lower().endswith('.png')])
                if png_files:
                    # Get the first PNG file
                    first_png = png_files[0]
                    source_image_path = os.path.join(images_dir, first_png)

                    # Create the target directory if it doesn't exist
                    doc_publish_dir = os.path.join(output_dir, doc_id)
                    os.makedirs(doc_publish_dir, exist_ok=True)

                    # Resize the image to 150px width and save as cover.png
                    target_image_path = os.path.join(doc_publish_dir, 'cover.png')
                    img = Image.open(source_image_path)

                    # Calculate new height to maintain aspect ratio
                    width_percent = (150 / float(img.size[0]))
                    new_height = int((float(img.size[1]) * float(width_percent)))

                    # Resize and save
                    img = img.resize((150, new_height), Image.LANCZOS)
                    img.save(target_image_path)

                    # Set the icon path relative to the document directory
                    icon_path = 'cover.png'
                    print(f"[+] Created cover image for {doc_id}: {target_image_path}")
            except Exception as e:
                print(f"Warning: Could not process image for {doc_id}: {e}")

        documents.append({'id': doc_id, 'title': title, 'icon': icon_path})

    # Get current timestamp
    current_timestamp = datetime.datetime.now().strftime('%d.%m.%Y %H:%M')

    # Create the HTML content - header part
    html_content = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <title>RedPen — Красной ручкой</title>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <link rel="stylesheet" href="css/main.css">
  <link rel="stylesheet" href="css/components.css">
  <link rel="stylesheet" href="css/responsive.css">
  <link rel="icon" href="favicon.svg">
  <style>
    .document-list {
      max-width: 800px;
      margin: 40px auto;
      padding: 20px;
    }
    .document-card {
      background-color: #fff;
      border-radius: 8px;
      box-shadow: 0 2px 10px rgba(0,0,0,0.1);
      margin-bottom: 20px;
      padding: 20px;
      transition: transform 0.2s, box-shadow 0.2s;
    }
    .document-card:hover {
      transform: translateY(-5px);
      box-shadow: 0 5px 15px rgba(0,0,0,0.15);
    }
    .document-content {
      display: flex;
      align-items: flex-start;
    }
    .document-icon {
      margin-right: 20px;
      flex-shrink: 0;
    }
    .document-icon img {
      border-radius: 4px;
      box-shadow: 0 2px 5px rgba(0,0,0,0.1);
    }
    .document-card h2 {
      margin-top: 0;
      color: #DC143C;
    }
    .document-card a {
      display: inline-block;
      background-color: #DC143C;
      color: white;
      padding: 10px 20px;
      border-radius: 4px;
      text-decoration: none;
      margin-top: 10px;
      transition: background-color 0.2s;
    }
    .document-card a:hover {
      background-color: #b01030;
    }
  </style>
</head>
<body>"""

    # Add header with dynamic timestamp
    html_content += f"""
  <header>RedPen — Красной ручкой <span id="timestamp" style="font-size: 0.7rem; font-weight: normal; opacity: 0.8;">Последнее обновление: {current_timestamp}</span></header>

  <div class="document-list">
    <h1>Выберите документ</h1>
"""

    # Add document cards
    for doc in documents:
        # Add icon if available
        icon_html = ""
        if doc.get('icon'):
            icon_html = f"""
      <div class="document-icon">
        <img src="{doc['id']}/{doc['icon']}" alt="{doc['title']}" width="150">
      </div>"""

        html_content += f"""
    <div class="document-card">
      <div class="document-content">{icon_html}
        <h2>{doc['title']}</h2>
        <a href="{doc['id']}/index.html">Открыть документ</a>
      </div>
    </div>
"""

    # Close HTML tags
    html_content += """
  </div>
</body>
</html>
"""

    # Write the HTML content to the files
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

    # Also create a copy as document_index.html for test compatibility
    with open(document_index_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"[+] Created index page at {index_path}")
    print(f"[+] Also created document_index.html at {document_index_path} for test compatibility")

    # Clean up old structure
    print("\n=== Cleaning Up Old Structure ===")
    old_dirs = ['annotations', 'images', 'text']
    for old_dir in old_dirs:
        old_path = os.path.join(output_dir, old_dir)
        if os.path.exists(old_path) and os.path.isdir(old_path):
            try:
                shutil.rmtree(old_path)
                print(f"[+] Removed old directory: {old_path}")
            except Exception as e:
                print(f"[!] Error removing directory {old_path}: {e}")

    return True

def create_redirect_html(directory, target_path):
    """Create an HTML file that redirects to the target path"""
    print(f"Creating redirect from {directory} to {target_path}")

    # Create the redirect HTML content
    redirect_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta http-equiv="refresh" content="0;url={target_path}/index.html">
    <title>Redirecting...</title>
</head>
<body>
    <p>Redirecting to <a href="{target_path}/index.html">{target_path}/index.html</a>...</p>
    <script>
        window.location.href = "{target_path}/index.html";
    </script>
</body>
</html>
"""

    # Write the redirect HTML to a file
    redirect_path = os.path.join(directory, "index.html")
    with open(redirect_path, "w") as f:
        f.write(redirect_html)

    print(f"[+] Created redirect file at {redirect_path}")

def push_to_submodule(target_dir=None):
    """Commit and push changes to the redpen-publish submodule"""
    # Only push if target_dir is None or is the default redpen-publish directory
    default_publish_dir = os.path.join(project_root, 'redpen-publish')

    if target_dir and os.path.abspath(target_dir) != os.path.abspath(default_publish_dir):
        print("\n=== Skipping Push to Submodule (custom target directory used) ===")
        return True

    print("\n=== Pushing Changes to redpen-publish Submodule ===")

    submodule_path = os.path.join(project_root, 'redpen-publish')

    # Check if there are changes to commit
    success, stdout, stderr = run_command("git status --porcelain", cwd=submodule_path)
    if not success:
        print("Failed to check git status")
        return False

    if not stdout.strip():
        print("No changes to commit in redpen-publish")
        return True

    # Add all changes
    success, stdout, stderr = run_command("git add .", cwd=submodule_path)
    if not success:
        print("Failed to add changes")
        return False

    # Commit changes
    success, stdout, stderr = run_command(
        'git commit -m "Update website content via build script"', 
        cwd=submodule_path
    )
    if not success:
        print("Failed to commit changes")
        return False

    # Push changes
    success, stdout, stderr = run_command("git push", cwd=submodule_path)
    if not success:
        print("Failed to push changes")
        return False

    print("Successfully pushed changes to redpen-publish")
    return True

def main():
    """Main function to build and publish the website"""
    parser = argparse.ArgumentParser(description="Build and publish the website")
    parser.add_argument("--skip-tests", action="store_true", help="Skip running annotation position tests")
    parser.add_argument("--skip-push", action="store_true", help="Skip pushing changes to the redpen-publish submodule")
    parser.add_argument("--target-dir", help="Specify a target directory for the build output (default: redpen-publish)")
    parser.add_argument("--document", help="Specify a document to build (default: build all documents)")
    parser.add_argument("--folders", help="Comma-separated list of specific folders to deploy (default: all folders)")

    args = parser.parse_args()

    # Use the specified target directory or default to redpen-publish
    target_dir = args.target_dir
    if target_dir:
        # Create absolute path if relative path is provided
        if not os.path.isabs(target_dir):
            target_dir = os.path.abspath(os.path.join(os.getcwd(), target_dir))
        # Create the directory if it doesn't exist
        os.makedirs(target_dir, exist_ok=True)
        print(f"Using target directory: {target_dir}")

    # Process specific folders if provided
    specific_folders = None
    if args.folders:
        specific_folders = [folder.strip() for folder in args.folders.split(',')]
        print(f"Building specific folders: {', '.join(specific_folders)}")

    # Use the specified document or build all documents
    document = args.document
    if document:
        print(f"Building document: {document}")
        # If both --document and --folders are specified, --document takes precedence
        specific_folders = None

    # Step 1: Convert markdown annotations to JSON
    if not convert_annotations(target_dir, document, specific_folders):
        print("Failed to convert annotations. Aborting.")
        sys.exit(1)

    # Step 2: Run annotation position tests (if not skipped)
    if not args.skip_tests:
        # For now, we'll only run tests if no specific document is specified
        if not document:
            tests_passed = run_annotation_tests(target_dir)
            if not tests_passed:
                print("Annotation position tests failed. Aborting.")
                sys.exit(1)
        else:
            print("Skipping annotation position tests for specific document")
    else:
        print("Skipping annotation position tests")

    # Step 3: Publish data
    if not publish_website_data(target_dir, document, specific_folders):
        print("Failed to publish website data. Aborting.")
        sys.exit(1)

    # Step 4: Create index page with document selection menu
    if not document:
        # Only create the index page when building all documents
        create_index_page(target_dir, specific_folders)

    # Step 5: Push changes to submodule (if not skipped)
    if not args.skip_push:
        if not push_to_submodule(target_dir):
            print("Failed to push changes to redpen-publish submodule. Aborting.")
            sys.exit(1)
    else:
        print("Skipping push to redpen-publish submodule")

    print("\n=== Website Build Completed Successfully ===")

if __name__ == "__main__":
    main()
