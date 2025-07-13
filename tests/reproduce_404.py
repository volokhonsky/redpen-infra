#!/usr/bin/env python3
"""
Script to reproduce the 404 error when running build_website.py with tests.
"""

import os
import sys
import tempfile
import importlib.util
import shutil
import http.server
import socketserver
import threading
import socket
import time
from urllib.request import urlopen
from urllib.error import HTTPError, URLError

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(project_root)

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

def check_url(url):
    """Check if a URL is accessible"""
    try:
        response = urlopen(url)
        return True, response.getcode()
    except HTTPError as e:
        return False, e.code
    except URLError as e:
        return False, str(e.reason)
    except Exception as e:
        return False, str(e)

def main():
    """Main function to reproduce the 404 error"""
    # Create a temporary directory for the build output
    temp_output_dir = tempfile.mkdtemp(prefix="redpen_test_output_")
    print(f"Created temporary directory: {temp_output_dir}")

    try:
        # Import the build_website module
        build_website_path = os.path.join(project_root, 'scripts', 'build_website.py')
        spec = importlib.util.spec_from_file_location("build_website", build_website_path)
        build_website = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build_website)

        # Save current directory
        original_dir = os.getcwd()

        # Run the build steps with our temporary directory
        print(f"Converting annotations to {temp_output_dir}...")
        build_website.convert_annotations(temp_output_dir)

        print(f"Publishing website data to {temp_output_dir}...")
        build_website.publish_website_data(temp_output_dir)

        # List files in the output directory
        print("\nFiles in the output directory:")
        for root, dirs, files in os.walk(temp_output_dir):
            for file in files:
                print(os.path.join(root, file))

        # Start a local HTTP server
        port = find_free_port()
        print(f"\nStarting HTTP server on port {port}...")
        server = start_http_server(temp_output_dir, port)

        # Check if document_index.html exists
        document_index_url = f"http://localhost:{port}/document_index.html"
        success, status = check_url(document_index_url)
        print(f"\nChecking {document_index_url}")
        print(f"Success: {success}, Status: {status}")

        # Check if index.html exists
        index_url = f"http://localhost:{port}/index.html"
        success, status = check_url(index_url)
        print(f"\nChecking {index_url}")
        print(f"Success: {success}, Status: {status}")

        # Check if document directories have index.html
        document_folders = build_website.get_document_folders()
        for doc in document_folders:
            doc_index_url = f"http://localhost:{port}/{doc}/index.html"
            success, status = check_url(doc_index_url)
            print(f"\nChecking {doc_index_url}")
            print(f"Success: {success}, Status: {status}")

            doc_document_index_url = f"http://localhost:{port}/{doc}/document_index.html"
            success, status = check_url(doc_document_index_url)
            print(f"\nChecking {doc_document_index_url}")
            print(f"Success: {success}, Status: {status}")

        # Wait a moment to see the output
        print("\nWaiting for 2 seconds...")
        time.sleep(2)

    finally:
        # Shutdown the server if it exists
        if 'server' in locals():
            server.shutdown()

        # Restore original directory
        if 'original_dir' in locals():
            os.chdir(original_dir)

        # Clean up temporary directory
        print(f"Cleaning up temporary directory: {temp_output_dir}")
        shutil.rmtree(temp_output_dir)

if __name__ == "__main__":
    main()