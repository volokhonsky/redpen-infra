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

def run_tests(update_baseline=False):
    """Run all annotation positioning tests"""
    # Create results directory
    os.makedirs(RESULTS_DIR, exist_ok=True)
    
    # Load baseline positions
    baseline = load_baseline_positions()
    
    # Get the base path and publish directory
    base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    publish_dir = os.path.join(base_path, 'redpen-publish')
    
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