#!/usr/bin/env python3
import hmac
import hashlib
import json
import os
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
import subprocess
import fcntl
import time
import argparse
from pathlib import Path

LOCK_FILE = "/srv/repo/.sync.lock"


def log(*args):
    print("[content-sync]", *args, file=sys.stderr, flush=True)


def run(cmd, cwd=None, env=None):
    log("$", " ".join(cmd), f"cwd={cwd or os.getcwd()}")
    subprocess.check_call(cmd, cwd=cwd, env=env or os.environ.copy())


def verify_signature(secret: str, payload: bytes, signature_header: str) -> bool:
    try:
        algo, sig = signature_header.split("=", 1)
        if algo != "sha256":
            return False
        mac = hmac.new(secret.encode("utf-8"), msg=payload, digestmod=hashlib.sha256)
        expected = mac.hexdigest()
        return hmac.compare_digest(expected, sig)
    except Exception:
        return False


def write_app_config(staging_dir: Path, api_base_url: str) -> None:
    if not api_base_url:
        return
    (staging_dir / "app-config.js").write_text(f"window.APP_CONFIG={{apiBaseUrl:\"{api_base_url}\"}};", encoding="utf-8")


def inject_app_config_script(staging_dir: Path) -> None:
    # ensure <script src="/app-config.js"></script> before </head> in all *.html files
    for html in staging_dir.rglob("*.html"):
        try:
            text = html.read_text(encoding="utf-8")
            if "app-config.js" in text:
                continue
            new_text = text.replace("</head>", '  <script src="/app-config.js"></script>\n</head>')
            if new_text != text:
                html.write_text(new_text, encoding="utf-8")
        except Exception:
            continue


def patch_bootstrap_js(staging_dir: Path) -> None:
    p = staging_dir / "js" / "redpen-editor-bootstrap.js"
    if not p.exists():
        return
    try:
        s = p.read_text(encoding="utf-8")
        needle = "function apiBase(path){ return path; }"
        if needle in s:
            replacement = (
                "function apiBase(path){ try { var c = (window.APP_CONFIG && window.APP_CONFIG.apiBaseUrl) ? String(window.APP_CONFIG.apiBaseUrl) : null; "
                "if (c) { c = c.replace(/\\\\\\/$/, \"\"); return c + path; } } catch(e) {} return path; }"
            )
            s = s.replace(needle, replacement)
            p.write_text(s, encoding="utf-8")
    except Exception:
        pass


def mutate_staging(staging_dir: Path, api_base_url: str) -> None:
    write_app_config(staging_dir, api_base_url)
    inject_app_config_script(staging_dir)
    patch_bootstrap_js(staging_dir)


def publish(repo_dir: Path, staging_dir: Path, public_dir: Path, api_base_url: str) -> None:
    # rsync to staging then to public with --delete
    if staging_dir.exists():
        subprocess.call(["rm", "-rf", str(staging_dir)])
    staging_dir.mkdir(parents=True, exist_ok=True)
    run(["rsync", "-a", "--delete", "--exclude", ".git", f"{repo_dir}/", f"{staging_dir}/"])  # type: ignore

    mutate_staging(staging_dir, api_base_url)

    run(["rsync", "-a", "--delete", f"{staging_dir}/", f"{public_dir}/"])  # type: ignore


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path not in ("/webhook", "/.hooks/redpen-publish"):
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length)
        signature = self.headers.get("X-Hub-Signature-256", "")
        secret = os.environ.get("WEBHOOK_SECRET", "")
        if not secret or not verify_signature(secret, payload, signature):
            log("invalid signature")
            self.send_response(401)
            self.end_headers()
            return

        # Optionally check event type
        evt = self.headers.get("X-GitHub-Event", "")
        if evt and evt != "push":
            self.send_response(202)
            self.end_headers()
            return

        # Process update with file lock to avoid concurrency
        with open(LOCK_FILE, "a+") as lf:
            log("acquiring lock")
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                self._process_update()
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, fmt, *args):
        # Silence default access log; use our log()
        log(fmt % args)

    def _process_update(self):
        repo = Path(os.environ.get("REPO_DIR", "/srv/repo"))
        public = Path(os.environ.get("PUBLIC_DIR", "/srv/public"))
        staging = Path(os.environ.get("STAGING_DIR", "/srv/staging"))
        git_ref = os.environ.get("GIT_REF", "main")
        api_base = os.environ.get("API_BASE_URL", "")

        # Fetch/reset
        run(["git", "fetch", "--all", "--prune"], cwd=str(repo))
        # Try to reset to origin/ref, fallback to ref
        try:
            run(["git", "reset", "--hard", f"origin/{git_ref}"], cwd=str(repo))
        except subprocess.CalledProcessError:
            run(["git", "checkout", "-f", git_ref], cwd=str(repo))

        publish(repo, staging, public, api_base)


def start_server(addr: str, port: int, repo: Path, public: Path, staging: Path):
    os.environ["REPO_DIR"] = str(repo)
    os.environ["PUBLIC_DIR"] = str(public)
    os.environ["STAGING_DIR"] = str(staging)
    httpd = HTTPServer((addr, port), Handler)
    log(f"Webhook server listening on {addr}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", action="store_true")
    ap.add_argument("--repo")
    ap.add_argument("--public")
    ap.add_argument("--staging")
    ap.add_argument("--addr", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--mutate-only", action="store_true")
    args = ap.parse_args()

    if args.mutate_only:
        staging = Path(args.staging or "/srv/staging")
        mutate_staging(staging, os.environ.get("API_BASE_URL", ""))
        sys.exit(0)

    if args.server:
        repo = Path(args.repo or "/srv/repo")
        public = Path(args.public or "/srv/public")
        staging = Path(args.staging or "/srv/staging")
        start_server(args.addr, args.port, repo, public, staging)
    else:
        print("Specify --server or --mutate-only", file=sys.stderr)
        sys.exit(2)
