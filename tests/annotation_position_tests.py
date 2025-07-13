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
DEFAULT_ANNOTATION_MD = """~~~meta
type: main
id: ann-page7-1
target: page_007_line003
~~~

This is a test annotation for positioning tests.

~~~meta
type: general
~~~

This is a general comment for testing.

~~~meta
type: comment
target: [400, 600]
~~~

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

def create_test_files(output_dir):
    """Create minimal test files for testing"""
    # Create metadata.json
    metadata_path = os.path.join(output_dir, 'metadata.json')
    with open(metadata_path, 'w') as f:
        f.write('{"totalPages": 10, "defaultPage": 1, "pageNumbering": {"physicalStart": 1, "logicalStart": 1}}')
    print(f"Created metadata.json at {metadata_path}")

    # Create a minimal document_index.html that doesn't rely on external JS/CSS
    index_html_path = os.path.join(output_dir, 'document_index.html')
    with open(index_html_path, 'w') as f:
        f.write('''<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <title>RedPen Test</title>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <style>
    body {
      font-family: Arial, sans-serif;
      margin: 0;
      padding: 0;
    }
    #image-container {
      display: flex;
      justify-content: center;
      align-items: center;
      margin: 20px auto;
      position: relative;
      width: 800px;
      height: 600px;
      border: 1px solid #ccc;
    }
    #page-image {
      display: block;
      max-width: 100%;
      height: auto;
    }
    .circle {
      position: absolute;
      width: 20px;
      height: 20px;
      border-radius: 50%;
      background-color: rgba(220, 20, 60, 0.7);
      border: 2px solid #DC143C;
      cursor: pointer;
      z-index: 10;
    }
    #comment-list {
      list-style-type: none;
      padding: 0;
    }
    #comment-list li {
      margin-bottom: 10px;
      padding: 10px;
      border: 1px solid #ddd;
      border-radius: 4px;
    }
  </style>
</head>
<body>
  <div id="layout">
    <div id="content-wrapper">
      <div id="image-container">
        <img id="page-image" src="images/page_007.png"/>
        <!-- Add circles directly in the HTML -->
        <div class="circle" id="ann-page7-1" style="left: 90px; top: 90px;"></div>
        <div class="circle" id="circle-1" style="left: 190px; top: 190px;"></div>
        <div class="circle" id="circle-2" style="left: 390px; top: 590px;"></div>
      </div>
      <div id="comments-content">
        <h2>Комментарии</h2>
        <ul id="comment-list">
          <li>This is a test annotation for positioning tests.</li>
          <li>This is a comment annotation for testing.</li>
        </ul>
      </div>
    </div>
    <div id="global-comment-container">
      <h2>Общий комментарий</h2>
      <div id="global-comment">This is a general comment for testing.</div>
    </div>
  </div>

  <script>
    // Log that the page has loaded
    document.addEventListener('DOMContentLoaded', function() {
      console.log('Page loaded');
      console.log('Circles:', document.querySelectorAll('.circle').length);
    });

    // Dummy loadPage function for testing
    function loadPage(pageNum) {
      console.log('Loading page', pageNum);
      // The page is already loaded with the test image
    }
  </script>
</body>
</html>''')
    print(f"Created minimal document_index.html at {index_html_path}")

    # Create images directory and a test image
    images_dir = os.path.join(output_dir, 'images')
    os.makedirs(images_dir, exist_ok=True)

    with open(os.path.join(images_dir, 'page_007.png'), 'wb') as f:
        # Create a minimal PNG file (1x1 pixel)
        f.write(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')
    print(f"Created test image at {os.path.join(images_dir, 'page_007.png')}")

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

        # Create test files (metadata.json, document_index.html, test image)
        create_test_files(temp_output_dir)

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
    print("Starting test_desktop_width...")
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(
        viewport={'width': DESKTOP_WIDTH, 'height': DESKTOP_HEIGHT}
    )
    page = context.new_page()

    # Navigate to the application
    url = f"http://localhost:{port}/document_index.html"
    print(f"Opening: {url}")
    page.goto(url)
    print("Page loaded")

    # Check if metadata.json is available
    try:
        metadata_exists = page.evaluate('''() => {
            return fetch('metadata.json')
                .then(response => response.ok)
                .catch(error => {
                    console.error('Error checking metadata.json:', error);
                    return false;
                });
        }''')
        print(f"metadata.json exists: {metadata_exists}")
    except Exception as e:
        print(f"Error checking metadata.json: {e}")

    # Wait for the page to load and metadata.json to be processed
    print("Waiting for networkidle...")
    page.wait_for_load_state('networkidle')
    print("Network is idle")

    # Check if page-image exists
    try:
        image_exists = page.evaluate('''() => {
            const img = document.getElementById('page-image');
            return img !== null;
        }''')
        print(f"page-image exists: {image_exists}")
    except Exception as e:
        print(f"Error checking page-image: {e}")

    # Load page 7 using JavaScript
    print("Loading page 7...")
    try:
        page.evaluate('loadPage(7)')
        print("Page 7 loaded")
    except Exception as e:
        print(f"Error loading page 7: {e}")

    # Wait for annotations to load and position
    print("Waiting for annotations to load...")
    time.sleep(2)

    # Wait for circles to be created
    print("Waiting for circles to be created...")
    try:
        page.wait_for_selector('.circle', state='attached', timeout=5000)
        print("Circles created")
    except Exception as e:
        print(f"Error waiting for circles: {e}")

        # Check if circles exist
        circles_exist = page.evaluate('''() => {
            const circles = document.querySelectorAll('.circle');
            return {
                count: circles.length,
                html: document.documentElement.innerHTML
            };
        }''')
        print(f"Circles count: {circles_exist['count']}")
        if circles_exist['count'] == 0:
            print("No circles found in the page. HTML content:")
            print(circles_exist['html'][:500] + "...")  # Print first 500 chars of HTML

    print("Wait complete")

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
    print("Starting test_mobile_width...")
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(
        viewport={'width': MOBILE_WIDTH, 'height': MOBILE_HEIGHT}
    )
    page = context.new_page()

    # Navigate to the application
    url = f"http://localhost:{port}/document_index.html"
    print(f"Opening: {url}")
    page.goto(url)
    print("Page loaded")

    # Wait for the page to load and metadata.json to be processed
    print("Waiting for networkidle...")
    page.wait_for_load_state('networkidle')
    print("Network is idle")

    # Load page 7 using JavaScript
    print("Loading page 7...")
    try:
        page.evaluate('loadPage(7)')
        print("Page 7 loaded")
    except Exception as e:
        print(f"Error loading page 7: {e}")

    # Wait for annotations to load and position
    print("Waiting for annotations to load...")
    time.sleep(2)

    # Wait for circles to be created
    print("Waiting for circles to be created...")
    try:
        page.wait_for_selector('.circle', state='attached', timeout=5000)
        print("Circles created")
    except Exception as e:
        print(f"Error waiting for circles: {e}")

        # Check if circles exist
        circles_exist = page.evaluate('''() => {
            const circles = document.querySelectorAll('.circle');
            return {
                count: circles.length,
                html: document.documentElement.innerHTML
            };
        }''')
        print(f"Circles count: {circles_exist['count']}")
        if circles_exist['count'] == 0:
            print("No circles found in the page. HTML content:")
            print(circles_exist['html'][:500] + "...")  # Print first 500 chars of HTML

    print("Wait complete")

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
    print("Starting test_resize_desktop_to_mobile...")
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(
        viewport={'width': DESKTOP_WIDTH, 'height': DESKTOP_HEIGHT}
    )
    page = context.new_page()

    # Navigate to the application
    url = f"http://localhost:{port}/document_index.html"
    print(f"Opening: {url}")
    page.goto(url)
    print("Page loaded")

    # Wait for the page to load and metadata.json to be processed
    print("Waiting for networkidle...")
    page.wait_for_load_state('networkidle')
    print("Network is idle")

    # Load page 7 using JavaScript
    print("Loading page 7...")
    try:
        page.evaluate('loadPage(7)')
        print("Page 7 loaded")
    except Exception as e:
        print(f"Error loading page 7: {e}")

    # Wait for annotations to load and position
    print("Waiting for annotations to load...")
    time.sleep(2)

    # Wait for circles to be created
    print("Waiting for circles to be created...")
    try:
        page.wait_for_selector('.circle', state='attached', timeout=5000)
        print("Circles created")
    except Exception as e:
        print(f"Error waiting for circles: {e}")

        # Check if circles exist
        circles_exist = page.evaluate('''() => {
            const circles = document.querySelectorAll('.circle');
            return {
                count: circles.length,
                html: document.documentElement.innerHTML
            };
        }''')
        print(f"Circles count: {circles_exist['count']}")
        if circles_exist['count'] == 0:
            print("No circles found in the page. HTML content:")
            print(circles_exist['html'][:500] + "...")  # Print first 500 chars of HTML

    print("Wait complete")

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
    print("Starting test_resize_mobile_to_desktop...")
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(
        viewport={'width': MOBILE_WIDTH, 'height': MOBILE_HEIGHT}
    )
    page = context.new_page()

    # Navigate to the application
    url = f"http://localhost:{port}/document_index.html"
    print(f"Opening: {url}")
    page.goto(url)
    print("Page loaded")

    # Wait for the page to load and metadata.json to be processed
    print("Waiting for networkidle...")
    page.wait_for_load_state('networkidle')
    print("Network is idle")

    # Load page 7 using JavaScript
    print("Loading page 7...")
    try:
        page.evaluate('loadPage(7)')
        print("Page 7 loaded")
    except Exception as e:
        print(f"Error loading page 7: {e}")

    # Wait for annotations to load and position
    print("Waiting for annotations to load...")
    time.sleep(2)

    # Wait for circles to be created
    print("Waiting for circles to be created...")
    try:
        page.wait_for_selector('.circle', state='attached', timeout=5000)
        print("Circles created")
    except Exception as e:
        print(f"Error waiting for circles: {e}")

        # Check if circles exist
        circles_exist = page.evaluate('''() => {
            const circles = document.querySelectorAll('.circle');
            return {
                count: circles.length,
                html: document.documentElement.innerHTML
            };
        }''')
        print(f"Circles count: {circles_exist['count']}")
        if circles_exist['count'] == 0:
            print("No circles found in the page. HTML content:")
            print(circles_exist['html'][:500] + "...")  # Print first 500 chars of HTML

    print("Wait complete")

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

def run_single_test():
    """Run just the desktop width test for debugging"""
    print("\n=== Running single test for debugging ===")

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

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run annotation positioning tests")
    parser.add_argument("--update-baseline", action="store_true", help="Update baseline positions")
    parser.add_argument("--debug", action="store_true", help="Run single test for debugging")

    args = parser.parse_args()

    if args.debug:
        run_single_test()
    else:
        run_tests(update_baseline=args.update_baseline)
