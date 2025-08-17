import os
import sys
import time
from contextlib import contextmanager

# Reuse helpers from annotation_position_tests if available
# We rely on its HTTP server utilities to keep behavior consistent

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
PUBLISH_DIR = os.path.join(BASE_DIR, 'redpen-publish')

# We import lazily inside functions to avoid hard dependency at import time

def find_free_port():
    from tests import annotation_position_tests as apt  # type: ignore
    return apt.find_free_port()


def start_http_server(root_dir, port):
    from tests import annotation_position_tests as apt  # type: ignore
    return apt.start_http_server(root_dir, port)


def document_url_base(port, target_dir=None):
    if target_dir:
        pub = os.path.abspath(target_dir)
    else:
        pub = PUBLISH_DIR
    # Choose a document folder to test; medinsky11klass exists in content and is published by default
    doc_folder = 'medinsky11klass'
    return f"http://localhost:{port}/{doc_folder}/index.html"


def wait_for_selector_absence(page, selector, timeout_ms=2000):
    # Helper to wait briefly and confirm selector is not present
    end = time.time() + (timeout_ms / 1000.0)
    while time.time() < end:
        els = page.query_selector_all(selector)
        if not els:
            return True
        time.sleep(0.05)
    # Final check
    return len(page.query_selector_all(selector)) == 0


def run_tests(target_dir=None):
    """
    Run editor mode visibility tests.

    Returns True if all tests pass, False otherwise.
    """
    # Import Playwright only when running
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as e:
        print(f"[editor_mode_tests] Playwright not available: {e}")
        return False

    # Start HTTP server
    port = find_free_port()
    publish_dir = os.path.abspath(target_dir) if target_dir else PUBLISH_DIR
    print(f"[editor_mode_tests] Serving {publish_dir} on port {port}")
    server = start_http_server(publish_dir, port)

    all_pass = False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})

            base_url = document_url_base(port, target_dir)

            # Test A: No editor flag => editor panel must not exist
            url_no_flag = base_url
            print(f"[editor_mode_tests] Test A: opening {url_no_flag}")
            page.goto(url_no_flag, wait_until="load")

            # Wait for the application to initialize (image load etc.)
            page.wait_for_timeout(600)  # 0.6s

            # Assert that editor panel is absent
            editor_present = page.query_selector('.redpen-editor') is not None
            if editor_present:
                print("[FAIL] Editor panel should not be present without ?editor=1")
                browser.close()
                return False

            # Also verify state flag if available
            try:
                editor_mode = page.evaluate("() => window.RedPenEditor ? window.RedPenEditor.state.editorMode : false")
                if editor_mode:
                    print("[FAIL] editorMode should be false without ?editor=1")
                    browser.close()
                    return False
            except Exception:
                # If RedPenEditor is not defined at all, that's acceptable (since editor.js is loaded but gated)
                pass

            # Test B: With editor=1 => editor panel must appear and editorMode true
            url_with_flag = base_url + ("&" if "?" in base_url else "?") + "editor=1"
            print(f"[editor_mode_tests] Test B: opening {url_with_flag}")
            page.goto(url_with_flag, wait_until="load")

            # Allow the script's DOMContentLoaded/init and editor init to run
            page.wait_for_timeout(800)

            # Check presence of editor UI
            panel = page.query_selector('.redpen-editor')
            if panel is None:
                # give a little more time in case of slow init
                page.wait_for_timeout(1200)
                panel = page.query_selector('.redpen-editor')
            if panel is None:
                print("[FAIL] Editor panel not found with ?editor=1")
                browser.close()
                return False

            # Check state flag
            try:
                editor_mode = page.evaluate("() => window.RedPenEditor ? window.RedPenEditor.state.editorMode : null")
            except Exception as e:
                print(f"[FAIL] Could not read RedPenEditor.state.editorMode: {e}")
                browser.close()
                return False

            if editor_mode is not True:
                print(f"[FAIL] editorMode expected True with ?editor=1, got {editor_mode}")
                browser.close()
                return False

            # All checks passed
            print("[editor_mode_tests] All checks passed")
            all_pass = True
            browser.close()
    finally:
        server.shutdown()

    return all_pass


if __name__ == '__main__':
    ok = run_tests()
    sys.exit(0 if ok else 1)
