#!/usr/bin/env python3
"""
Automated tests for annotation positioning at different screen widths.

This script tests the positioning of annotation circles on page 7 at different
screen widths (1280px and 800px) and verifies that the positioning remains
correct when the screen size changes.

Usage:
    python tests/annotation_position_tests.py

The tests will:
1. Measure circle positions at desktop width (1280px)
2. Measure circle positions at mobile width (800px)
3. Verify that circles reposition correctly when resizing from desktop to mobile
4. Verify that circles reposition correctly when resizing from mobile to desktop
5. Compare positions against baseline values

The baseline values are stored in tests/baseline_positions.json and represent
the expected positions of circles at different screen widths.
"""

import json
import os
import time
import tempfile
import shutil
import importlib.util
import sys
from playwright.sync_api import sync_playwright
import socket
import threading
import http.server
import socketserver

# Constants
DESKTOP_WIDTH = 1280
DESKTOP_HEIGHT = 800
MOBILE_WIDTH = 800
MOBILE_HEIGHT = 600
BASELINE_FILE = os.path.join(os.path.dirname(__file__), 'baseline_positions.json')
RESULTS_DIR = os.path.join(os.path.dirname(__file__), 'results')
TOLERANCE = 5  # Tolerance in pixels for position comparisons

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(project_root)

# Default test content
DEFAULT_ANNOTATION_MD = """---
type: main
id: ann-page7-1
target: page_007_line003
---

This is a test annotation for positioning tests.

---
type: general
---

This is a general comment for testing.

---
type: comment
target: [400,600]
---

This is a comment annotation for testing.
"""

DEFAULT_ANNOTATION_JSON = """[
  {
    "id": "ann-page7-1",
    "text": "This is a test annotation for positioning tests.",
    "annType": "main",
    "targetBlock": "page_007_line003"
  },
  {
    "id": "",
    "text": "This is a general comment for testing.",
    "annType": "general"
  },
  {
    "id": "",
    "text": "This is a comment annotation for testing.",
    "annType": "comment",
    "coords": [
      400,
      600
    ]
  }
]"""

def find_free_port():
    """Find a free port on localhost"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]

def start_http_server(directory, port):
    """Start a simple HTTP server in a separate thread"""
    handler = http.server.SimpleHTTPRequestHandler
    httpd = socketserver.TCPServer(("", port), handler)

    # Change to the specified directory
    os.chdir(directory)

    # Start the server in a separate thread
    server_thread = threading.Thread(target=httpd.serve_forever)
    server_thread.daemon = True
    server_thread.start()

    return httpd

def get_circle_positions(page):
    """Get positions of all circles on the page"""
    return page.evaluate('''() => {
        const circles = Array.from(document.querySelectorAll('.circle'));
        return circles.map((circle, index) => {
            const rect = circle.getBoundingClientRect();
            return {
                id: circle.id || `circle-${index}`,
                centerX: rect.left + rect.width / 2,
                centerY: rect.top + rect.height / 2,
                left: rect.left,
                top: rect.top,
                width: rect.width,
                height: rect.height
            };
        });
    }''')

def load_baseline_positions():
    """Load baseline positions from file"""
    try:
        with open(BASELINE_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # Return empty baseline if file doesn't exist or is invalid
        return {
            'desktop': [],
            'mobile': [],
            'desktop_to_mobile': [],
            'mobile_to_desktop': []
        }

def save_baseline_positions(baseline):
    """Save baseline positions to file"""
    os.makedirs(os.path.dirname(BASELINE_FILE), exist_ok=True)
    with open(BASELINE_FILE, 'w') as f:
        json.dump(baseline, f, indent=2)

def compare_positions(actual, expected, tolerance=TOLERANCE):
    """Compare actual positions with expected positions"""
    if not expected:
        print("No baseline positions to compare against")
        return True

    if len(actual) != len(expected):
        print(f"Number of circles doesn't match: actual={len(actual)}, expected={len(expected)}")
        return False

    all_match = True
    for i, (act, exp) in enumerate(zip(actual, expected)):
        # Match by ID if available, otherwise by index
        if act.get('id') and exp.get('id') and act['id'] != exp['id']:
            print(f"Circle {i} ID mismatch: actual={act['id']}, expected={exp['id']}")
            all_match = False
            continue

        # Check centerX
        if abs(act['centerX'] - exp['centerX']) > tolerance:
            print(f"Circle {act.get('id', i)} centerX mismatch: actual={act['centerX']}, expected={exp['centerX']}")
            all_match = False

        # Check centerY
        if abs(act['centerY'] - exp['centerY']) > tolerance:
            print(f"Circle {act.get('id', i)} centerY mismatch: actual={act['centerY']}, expected={exp['centerY']}")
            all_match = False

    return all_match

def setup_test_content():
    """Set up a temporary directory with test content"""
    # Create a temporary directory for test content
    temp_content_dir = tempfile.mkdtemp(prefix="redpen_test_content_")

    # Create the necessary directory structure
    os.makedirs(os.path.join(temp_content_dir, 'annotations'), exist_ok=True)
    os.makedirs(os.path.join(temp_content_dir, 'images'), exist_ok=True)
    os.makedirs(os.path.join(temp_content_dir, 'text'), exist_ok=True)

    # Create a test annotation file
    with open(os.path.join(temp_content_dir, 'annotations', 'page_007.md'), 'w') as f:
        f.write(DEFAULT_ANNOTATION_MD)

    # Copy a test image from the project
    source_image = os.path.join(project_root, 'redpen-content', 'images', 'page_007.png')
    if os.path.exists(source_image):
        shutil.copy(source_image, os.path.join(temp_content_dir, 'images', 'page_007.png'))
    else:
        # Create a blank image if the source doesn't exist
        print("Warning: Source image not found, creating a blank image for testing")
        with open(os.path.join(temp_content_dir, 'images', 'page_007.png'), 'wb') as f:
            # Create a minimal PNG file (1x1 pixel)
            f.write(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')

    # Create a test text file
    with open(os.path.join(temp_content_dir, 'text', 'page_007.json'), 'w') as f:
        f.write('[]')  # Empty text blocks for simplicity

    return temp_content_dir

def build_test_website(content_dir):
    """Build a test website using the build_website.py script"""
    # Create a temporary directory for the build output
    temp_output_dir = tempfile.mkdtemp(prefix="redpen_test_output_")

    # Import the build_website module
    build_website_path = os.path.join(project_root, 'scripts', 'build_website.py')
    spec = importlib.util.spec_from_file_location("build_website", build_website_path)
    build_website = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_website)

    # Save current directory
    original_dir = os.getcwd()

    try:
        # Set up environment for build_website
        os.environ['REDPEN_CONTENT_DIR'] = content_dir

        # Run the build steps with our temporary directories
        print(f"Converting annotations from {content_dir} to {temp_output_dir}...")
        build_website.convert_annotations(temp_output_dir)

        print(f"Publishing website data to {temp_output_dir}...")
        build_website.publish_website_data(temp_output_dir)

        # Create index.html in the output directory if it doesn't exist
        index_html_path = os.path.join(temp_output_dir, 'index.html')
        if not os.path.exists(index_html_path):
            # Copy from templates
            template_index = os.path.join(project_root, 'templates', 'index.html')
            if os.path.exists(template_index):
                shutil.copy(template_index, index_html_path)
            else:
                # Create a minimal index.html
                with open(index_html_path, 'w') as f:
                    f.write('''<!DOCTYPE html>
<html>
<head>
    <title>RedPen Test</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="css/main.css">
    <link rel="stylesheet" href="css/components.css">
    <link rel="stylesheet" href="css/responsive.css">
</head>
<body>
    <div id="app">
        <div id="sidebar">
            <div id="page-nav">
                <h3>Страницы</h3>
                <ul>
                    <li><a href="#" onclick="loadPage(7)">Стр. 7</a></li>
                </ul>
            </div>
            <div id="comment-list-container">
                <h3>Комментарии</h3>
                <ul id="comment-list"></ul>
            </div>
        </div>
        <div id="content">
            <div id="image-container">
                <img id="page-image" src="" alt="Page Image">
            </div>
            <div id="global-comment-container">
                <h3>Общий комментарий</h3>
                <div id="global-comment"></div>
            </div>
        </div>
        <div id="mobile-overlay">
            <div id="mobile-comment-content"></div>
            <div class="mobile-overlay-close">&times;</div>
        </div>
    </div>
    <script src="js/main.js"></script>
    <script src="js/annotations.js"></script>
    <script src="js/layout.js"></script>
    <script src="js/mobile.js"></script>
</body>
</html>''')

        # Copy JS and CSS files
        for dir_name in ['js', 'css']:
            src_dir = os.path.join(project_root, 'templates', dir_name)
            dst_dir = os.path.join(temp_output_dir, dir_name)
            if os.path.exists(src_dir):
                os.makedirs(dst_dir, exist_ok=True)
                for file_name in os.listdir(src_dir):
                    if file_name.endswith('.js') or file_name.endswith('.css'):
                        shutil.copy(os.path.join(src_dir, file_name), os.path.join(dst_dir, file_name))

        return temp_output_dir
    finally:
        # Restore original directory
        os.chdir(original_dir)

def run_tests(update_baseline=False):
    """Run all annotation positioning tests"""
    # Create results directory
    os.makedirs(RESULTS_DIR, exist_ok=True)

    # Load baseline positions
    baseline = load_baseline_positions()

    # Set up test content and build the website
    print("\n=== Setting up test content ===")
    content_dir = setup_test_content()

    print("\n=== Building test website ===")
    publish_dir = build_test_website(content_dir)

    # Save current directory to restore it later
    original_dir = os.getcwd()

    # Start a local HTTP server
    port = find_free_port()
    print(f"Starting HTTP server on port {port}...")
    server = start_http_server(publish_dir, port)

    try:
        with sync_playwright() as p:
            # Test 1: Desktop width (1280px)
            print("\n=== Test 1: Desktop Width (1280px) ===")
            desktop_results = test_desktop_width(p, port)

            # Test 2: Mobile width (800px)
            print("\n=== Test 2: Mobile Width (800px) ===")
            mobile_results = test_mobile_width(p, port)

            # Test 3: Resize from desktop to mobile
            print("\n=== Test 3: Resize from Desktop to Mobile ===")
            desktop_to_mobile_results = test_resize_desktop_to_mobile(p, port)

            # Test 4: Resize from mobile to desktop
            print("\n=== Test 4: Resize from Mobile to Desktop ===")
            mobile_to_desktop_results = test_resize_mobile_to_desktop(p, port)

            # Update baseline if requested
            if update_baseline:
                baseline = {
                    'desktop': desktop_results,
                    'mobile': mobile_results,
                    'desktop_to_mobile': desktop_to_mobile_results,
                    'mobile_to_desktop': mobile_to_desktop_results
                }
                save_baseline_positions(baseline)
                print("\nBaseline positions updated")
            else:
                # Compare with baseline
                print("\n=== Comparing with Baseline ===")
                desktop_match = compare_positions(desktop_results, baseline.get('desktop', []))
                mobile_match = compare_positions(mobile_results, baseline.get('mobile', []))
                desktop_to_mobile_match = compare_positions(desktop_to_mobile_results, baseline.get('desktop_to_mobile', []))
                mobile_to_desktop_match = compare_positions(mobile_to_desktop_results, baseline.get('mobile_to_desktop', []))

                # Print overall results
                print("\n=== Overall Results ===")
                print(f"Desktop width test: {'PASS' if desktop_match else 'FAIL'}")
                print(f"Mobile width test: {'PASS' if mobile_match else 'FAIL'}")
                print(f"Desktop to mobile resize test: {'PASS' if desktop_to_mobile_match else 'FAIL'}")
                print(f"Mobile to desktop resize test: {'PASS' if mobile_to_desktop_match else 'FAIL'}")

                all_pass = desktop_match and mobile_match and desktop_to_mobile_match and mobile_to_desktop_match
                print(f"\nOverall: {'PASS' if all_pass else 'FAIL'}")
    finally:
        # Restore original directory
        os.chdir(original_dir)

        # Shutdown the server
        server.shutdown()

        # Clean up temporary directories
        try:
            if 'content_dir' in locals() and os.path.exists(content_dir):
                print(f"Cleaning up temporary content directory: {content_dir}")
                shutil.rmtree(content_dir)
        except Exception as e:
            print(f"Warning: Failed to clean up content directory: {e}")

        try:
            if 'publish_dir' in locals() and os.path.exists(publish_dir):
                print(f"Cleaning up temporary publish directory: {publish_dir}")
                shutil.rmtree(publish_dir)
        except Exception as e:
            print(f"Warning: Failed to clean up publish directory: {e}")

def test_desktop_width(p, port):
    """Test annotation positioning at desktop width (1280px)"""
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(
        viewport={'width': DESKTOP_WIDTH, 'height': DESKTOP_HEIGHT}
    )
    page = context.new_page()

    # Navigate to the application
    url = f"http://localhost:{port}/index.html"
    print(f"Opening: {url}")
    page.goto(url)

    # Wait for the page to load
    page.wait_for_selector('#page-image', state='visible')

    # Load page 7
    page.click('text=Стр. 7')

    # Wait for annotations to load and position
    time.sleep(2)

    # Get circle positions
    circles = get_circle_positions(page)

    # Take a screenshot
    screenshot_path = os.path.join(RESULTS_DIR, 'desktop_width.png')
    page.screenshot(path=screenshot_path)

    # Print results
    print(f"Found {len(circles)} circles")
    for circle in circles:
        print(f"Circle {circle['id']}: centerX={circle['centerX']}, centerY={circle['centerY']}")

    # Close browser
    browser.close()

    return circles

def test_mobile_width(p, port):
    """Test annotation positioning at mobile width (800px)"""
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(
        viewport={'width': MOBILE_WIDTH, 'height': MOBILE_HEIGHT}
    )
    page = context.new_page()

    # Navigate to the application
    url = f"http://localhost:{port}/index.html"
    print(f"Opening: {url}")
    page.goto(url)

    # Wait for the page to load
    page.wait_for_selector('#page-image', state='visible')

    # Load page 7
    page.click('text=Стр. 7')

    # Wait for annotations to load and position
    time.sleep(2)

    # Get circle positions
    circles = get_circle_positions(page)

    # Take a screenshot
    screenshot_path = os.path.join(RESULTS_DIR, 'mobile_width.png')
    page.screenshot(path=screenshot_path)

    # Print results
    print(f"Found {len(circles)} circles")
    for circle in circles:
        print(f"Circle {circle['id']}: centerX={circle['centerX']}, centerY={circle['centerY']}")

    # Close browser
    browser.close()

    return circles

def test_resize_desktop_to_mobile(p, port):
    """Test annotation positioning when resizing from desktop to mobile"""
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(
        viewport={'width': DESKTOP_WIDTH, 'height': DESKTOP_HEIGHT}
    )
    page = context.new_page()

    # Navigate to the application
    url = f"http://localhost:{port}/index.html"
    print(f"Opening: {url}")
    page.goto(url)

    # Wait for the page to load
    page.wait_for_selector('#page-image', state='visible')

    # Load page 7
    page.click('text=Стр. 7')

    # Wait for annotations to load and position
    time.sleep(2)

    # Take a screenshot before resize
    screenshot_path = os.path.join(RESULTS_DIR, 'desktop_before_resize.png')
    page.screenshot(path=screenshot_path)

    # Resize to mobile width
    page.set_viewport_size({'width': MOBILE_WIDTH, 'height': MOBILE_HEIGHT})

    # Wait for resize to take effect
    time.sleep(2)

    # Get circle positions after resize
    circles = get_circle_positions(page)

    # Take a screenshot after resize
    screenshot_path = os.path.join(RESULTS_DIR, 'desktop_to_mobile_after_resize.png')
    page.screenshot(path=screenshot_path)

    # Print results
    print(f"Found {len(circles)} circles after resize")
    for circle in circles:
        print(f"Circle {circle['id']}: centerX={circle['centerX']}, centerY={circle['centerY']}")

    # Close browser
    browser.close()

    return circles

def test_resize_mobile_to_desktop(p, port):
    """Test annotation positioning when resizing from mobile to desktop"""
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(
        viewport={'width': MOBILE_WIDTH, 'height': MOBILE_HEIGHT}
    )
    page = context.new_page()

    # Navigate to the application
    url = f"http://localhost:{port}/index.html"
    print(f"Opening: {url}")
    page.goto(url)

    # Wait for the page to load
    page.wait_for_selector('#page-image', state='visible')

    # Load page 7
    page.click('text=Стр. 7')

    # Wait for annotations to load and position
    time.sleep(2)

    # Take a screenshot before resize
    screenshot_path = os.path.join(RESULTS_DIR, 'mobile_before_resize.png')
    page.screenshot(path=screenshot_path)

    # Resize to desktop width
    page.set_viewport_size({'width': DESKTOP_WIDTH, 'height': DESKTOP_HEIGHT})

    # Wait for resize to take effect
    time.sleep(2)

    # Get circle positions after resize
    circles = get_circle_positions(page)

    # Take a screenshot after resize
    screenshot_path = os.path.join(RESULTS_DIR, 'mobile_to_desktop_after_resize.png')
    page.screenshot(path=screenshot_path)

    # Print results
    print(f"Found {len(circles)} circles after resize")
    for circle in circles:
        print(f"Circle {circle['id']}: centerX={circle['centerX']}, centerY={circle['centerY']}")

    # Close browser
    browser.close()

    return circles

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run annotation positioning tests")
    parser.add_argument("--update-baseline", action="store_true", help="Update baseline positions")

    args = parser.parse_args()

    run_tests(update_baseline=args.update_baseline)
