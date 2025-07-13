#!/usr/bin/env python3
"""
Simple test script to verify basic functionality of the document_index.html template.
"""

import os
import tempfile
import shutil
import time
from playwright.sync_api import sync_playwright
import http.server
import socketserver
import threading
import socket

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

def create_test_files():
    """Create minimal test files for testing"""
    # Create a temporary directory
    temp_dir = tempfile.mkdtemp(prefix="redpen_simple_test_")
    
    # Create metadata.json
    with open(os.path.join(temp_dir, 'metadata.json'), 'w') as f:
        f.write('{"totalPages": 10, "defaultPage": 1, "pageNumbering": {"physicalStart": 1, "logicalStart": 1}}')
    
    # Create minimal document_index.html
    with open(os.path.join(temp_dir, 'document_index.html'), 'w') as f:
        f.write('''<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <title>RedPen Test</title>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <style>
    #page-image {
      display: block;
      max-width: 100%;
      height: auto;
      margin: 0 auto;
    }
  </style>
</head>
<body>
  <div id="image-container">
    <img id="page-image" src="test_image.png"/>
  </div>
  
  <script>
    document.addEventListener('DOMContentLoaded', function() {
      console.log('Page loaded');
      const img = document.getElementById('page-image');
      console.log('Image element:', img);
      console.log('Image visible:', img.offsetWidth > 0);
    });
  </script>
</body>
</html>''')
    
    # Create a simple test image
    with open(os.path.join(temp_dir, 'test_image.png'), 'wb') as f:
        # Create a minimal PNG file (1x1 pixel)
        f.write(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')
    
    return temp_dir

def run_simple_test():
    """Run a simple test to verify basic functionality"""
    print("Creating test files...")
    test_dir = create_test_files()
    
    # Start HTTP server
    port = find_free_port()
    print(f"Starting HTTP server on port {port}...")
    server = start_http_server(test_dir, port)
    
    try:
        with sync_playwright() as p:
            print("Launching browser...")
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(viewport={'width': 1280, 'height': 800})
            page = context.new_page()
            
            # Navigate to the page
            url = f"http://localhost:{port}/document_index.html"
            print(f"Opening: {url}")
            page.goto(url)
            
            # Wait for the page to load
            print("Waiting for load state...")
            page.wait_for_load_state('networkidle')
            print("Network is idle")
            
            # Check if page-image exists and is visible
            print("Checking if page-image exists and is visible...")
            image_exists = page.evaluate('''() => {
                const img = document.getElementById('page-image');
                return {
                    exists: img !== null,
                    visible: img !== null && img.offsetWidth > 0,
                    src: img !== null ? img.src : null
                };
            }''')
            print(f"page-image exists: {image_exists['exists']}")
            print(f"page-image visible: {image_exists['visible']}")
            print(f"page-image src: {image_exists['src']}")
            
            # Take a screenshot
            screenshot_path = os.path.join(test_dir, 'screenshot.png')
            page.screenshot(path=screenshot_path)
            print(f"Screenshot saved to: {screenshot_path}")
            
            # Wait a moment to see the page
            print("Waiting for 2 seconds...")
            time.sleep(2)
            print("Wait complete")
            
            # Close browser
            browser.close()
    finally:
        # Shutdown the server
        server.shutdown()
        
        # Clean up temporary directory
        print(f"Cleaning up temporary directory: {test_dir}")
        shutil.rmtree(test_dir)

if __name__ == "__main__":
    run_simple_test()