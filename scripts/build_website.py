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
    python scripts/build_website.py [--skip-tests] [--skip-push] [--target-dir TARGET_DIR]

Options:
    --skip-tests    Skip running annotation position tests
    --skip-push     Skip pushing changes to the redpen-publish submodule
    --target-dir    Specify a target directory for the build output (default: redpen-publish)
"""

import os
import sys
import argparse
import subprocess
import importlib.util

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(project_root)

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

def convert_annotations(target_dir=None):
    """Convert markdown annotations to JSON"""
    print("\n=== Converting Markdown Annotations to JSON ===")
    md_dir = os.path.join(project_root, 'redpen-content', 'annotations')

    # Use target_dir if provided, otherwise use default redpen-publish
    if target_dir:
        json_dir = os.path.join(target_dir, 'annotations')
    else:
        json_dir = os.path.join(project_root, 'redpen-publish', 'annotations')

    # Create the directory if it doesn't exist
    os.makedirs(json_dir, exist_ok=True)

    try:
        annotation_converter.md_to_json(md_dir, json_dir)
        return True
    except Exception as e:
        print(f"Error converting annotations: {e}")
        return False

def run_annotation_tests(target_dir=None):
    """Run annotation position tests"""
    print("\n=== Running Annotation Position Tests ===")

    # Import the annotation_position_tests module
    tests_module = import_module_from_file(
        'annotation_position_tests',
        os.path.join(project_root, 'tests', 'annotation_position_tests.py')
    )

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

def publish_website_data(target_dir=None):
    """Publish data to the target directory"""
    print("\n=== Publishing Website Data ===")

    images_dir = os.path.join(project_root, 'redpen-content', 'images')
    text_dir = os.path.join(project_root, 'redpen-content', 'text')
    # Skip annotations as they're already converted and in the right place
    annotations_dir = None
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
        # Publish content data
        publish_data.publish_data(images_dir, text_dir, annotations_dir, output_dir)

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

        # Copy HTML files
        if os.path.exists(templates_dir):
            publish_data.copy_files(templates_dir, output_dir, "*.html")

        # Copy favicon
        if os.path.exists(templates_dir):
            publish_data.copy_files(templates_dir, output_dir, "*.svg")

        return True
    except Exception as e:
        print(f"Error publishing data: {e}")
        return False

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

    # Step 1: Convert markdown annotations to JSON
    if not convert_annotations(target_dir):
        print("Failed to convert annotations. Aborting.")
        sys.exit(1)

    # Step 2: Run annotation position tests (if not skipped)
    if not args.skip_tests:
        tests_passed = run_annotation_tests(target_dir)
        if not tests_passed:
            print("Annotation position tests failed. Aborting.")
            sys.exit(1)
    else:
        print("Skipping annotation position tests")

    # Step 3: Publish data
    if not publish_website_data(target_dir):
        print("Failed to publish website data. Aborting.")
        sys.exit(1)

    # Step 4: Push changes to submodule (if not skipped)
    if not args.skip_push:
        if not push_to_submodule(target_dir):
            print("Failed to push changes to redpen-publish submodule. Aborting.")
            sys.exit(1)
    else:
        print("Skipping push to redpen-publish submodule")

    print("\n=== Website Build Completed Successfully ===")

if __name__ == "__main__":
    main()
