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
from typing import Optional

LOCK_FILE = "/srv/repo/.sync.lock"
FINGERPRINT_FILE = "/srv/repo/.last_fingerprint"

# Default commit author (can be overridden via env)
COMMIT_AUTHOR_NAME = os.environ.get("COMMIT_AUTHOR_NAME", "medinsky.net")
COMMIT_AUTHOR_EMAIL = os.environ.get("COMMIT_AUTHOR_EMAIL", "volokhonsky@gmail.com")


def log(*args):
    print("[content-sync]", *args, file=sys.stderr, flush=True)


def run(cmd, cwd=None, env=None):
    log("$", " ".join(cmd), f"cwd={cwd or os.getcwd()}")
    subprocess.check_call(cmd, cwd=cwd, env=env or os.environ.copy())

# ... existing code ...
def run_capture(cmd, cwd=None, env=None) -> str:
    log("$", " ".join(cmd), f"cwd={cwd or os.getcwd()}")
    out = subprocess.check_output(cmd, cwd=cwd, env=env or os.environ.copy())
    try:
        return out.decode().strip()
    except Exception:
        return str(out)

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

# --- Parent repo + submodules (single source of truth) ---
def is_git_repo(path: Path) -> bool:
    try:
        return run_capture(["git", "rev-parse", "--is-inside-work-tree"], cwd=str(path)).strip() == "true"
    except Exception:
        return False

def parent_fetch_reset(parent: Path, ref: str) -> None:
    run(["git", "fetch", "--all", "--prune"], cwd=str(parent))
    try:
        run(["git", "reset", "--hard", f"origin/{ref}"], cwd=str(parent))
    except subprocess.CalledProcessError:
        run(["git", "checkout", "-f", ref], cwd=str(parent))

def submodules_sync_update(parent: Path, use_remote: bool = True) -> None:
    try:
        run(["git", "submodule", "sync", "--recursive"], cwd=str(parent))
    except Exception:
        pass
    try:
        run(["git", "submodule", "update", "--init", "--recursive"] + (["--remote"] if use_remote else []), cwd=str(parent))
    except Exception:
        pass

def detect_branch(repo_dir: Path) -> str:
    try:
        br = run_capture(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], cwd=str(repo_dir))
        if br:
            return br
    except Exception:
        pass
    try:
        headref = run_capture(["git", "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"], cwd=str(repo_dir))
        if headref and headref.startswith("origin/"):
            return headref.split("/", 1)[1]
    except Exception:
        pass
    return "main"

def commit_pull_push(repo_dir: Path, msg: str) -> bool:
    try:
        if run_capture(["git", "status", "--porcelain"], cwd=str(repo_dir)).strip():
            run(["git", "add", "-A"], cwd=str(repo_dir))
            run(["git", "-c", f"user.name={COMMIT_AUTHOR_NAME}", "-c", f"user.email={COMMIT_AUTHOR_EMAIL}", "commit", "-m", msg], cwd=str(repo_dir))
        run(["git", "fetch", "--all", "--prune"], cwd=str(repo_dir))
        br = detect_branch(repo_dir)
        try:
            run(["git", "pull", "--rebase", "origin", br], cwd=str(repo_dir))
        except subprocess.CalledProcessError:
            try:
                run(["git", "rebase", "--abort"], cwd=str(repo_dir))
            except Exception:
                pass
            log(f"[{repo_dir}] rebase conflict; aborting")
            return False
        try:
            run(["git", "push"], cwd=str(repo_dir))
        except subprocess.CalledProcessError:
            try:
                run(["git", "push", "--set-upstream", "origin", br], cwd=str(repo_dir))
            except subprocess.CalledProcessError as e2:
                log(f"[{repo_dir}] push failed:", e2)
                return False
        return True
    except subprocess.CalledProcessError as e:
        log(f"[{repo_dir}] sync failed:", e)
        return False

def write_app_config(staging_dir: Path, api_base_url: str) -> None:
    if not api_base_url:
        return
    (staging_dir / "app-config.js").write_text(f"window.APP_CONFIG={{apiBaseUrl:\"{api_base_url}\"}};", encoding="utf-8")

def inject_app_config_script(staging_dir: Path) -> None:
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

def publish_from_parent(parent: Path, public_dir: Path, staging_dir: Path, api_base_url: str) -> None:
    src = parent / "redpen-publish"
    if not src.exists():
        log("publish source redpen-publish not found in parent repo")
        return
    if staging_dir.exists():
        subprocess.call(["rm", "-rf", str(staging_dir)])
    staging_dir.mkdir(parents=True, exist_ok=True)
    run(["rsync", "-a", "--delete", "--exclude", ".git", f"{src}/", f"{staging_dir}/"])
    mutate_staging(staging_dir, api_base_url)
    run(["rsync", "-a", "--delete", f"{staging_dir}/", f"{public_dir}/"])
    try:
        (public_dir / ".published_by_sync").write_text(str(int(time.time())), encoding="utf-8")
    except Exception as e:
        log("failed to write publish stamp:", e)

def bump_parent_submodules(parent: Path, msg: str) -> bool:
    try:
        # Stage submodule pointers (and .gitmodules if changed)
        try:
            run(["git", "add", "redpen-content", "redpen-publish", ".gitmodules"], cwd=str(parent))
        except subprocess.CalledProcessError:
            # Fallback: add submodule dirs only
            run(["git", "add", "redpen-content", "redpen-publish"], cwd=str(parent))
        return commit_pull_push(parent, msg)
    except Exception as e:
        log("failed to bump parent submodules:", e)
        return False


def process_update(parent: Path, public: Path, staging: Path, git_ref: str, api_base: str) -> bool:
    # Pull parent and submodules from remote, bump pointers in parent, then publish
    if not is_git_repo(parent):
        log("parent repo at /srv/repo is not a git repo; skipping")
        return False
    parent_fetch_reset(parent, git_ref)
    use_remote = (os.environ.get("SUBMODULE_STRATEGY", "remote").strip().lower() == "remote")
    submodules_sync_update(parent, use_remote=use_remote)
    # Commit updated submodule SHAs in parent and push
    bump_parent_submodules(parent, "chore(sync): bump submodules")
    publish_from_parent(parent, public, staging, api_base)
    return True

def read_fingerprint() -> Optional[str]:
    try:
        return Path(FINGERPRINT_FILE).read_text(encoding="utf-8")
    except Exception:
        return None

def write_fingerprint(fp: str) -> None:
    try:
        Path(FINGERPRINT_FILE).write_text(fp, encoding="utf-8")
    except Exception as e:
        log("failed to persist fingerprint:", e)

def compute_fingerprint(parent: Path, git_ref: str) -> str:
    def dir_digest(base: Path, mask=None):
        try:
            h = hashlib.sha256()
            for p in sorted(base.rglob("*")):
                if p.is_file() and ".git" not in p.parts:
                    if mask and not mask(p):
                        continue
                    st = p.stat()
                    rel = str(p.relative_to(base))
                    h.update(rel.encode()); h.update(str(st.st_mtime_ns).encode()); h.update(str(st.st_size).encode())
            return h.hexdigest()
        except Exception:
            return ""
    content_digest = dir_digest((parent / "redpen-content"), mask=lambda p: p.suffix.lower() in (".md", ".markdown", ".json", ".yaml", ".yml"))
    publish_digest = dir_digest((parent / "redpen-publish"))
    return hashlib.sha256((content_digest + "|" + publish_digest).encode()).hexdigest()

# ---------------- Watchers (work on submodules inside parent) ----------------
from dataclasses import dataclass, field

def get_publish_stamp_time(public_dir: Path) -> float:
    try:
        p = public_dir / ".published_by_sync"
        if p.exists():
            return p.stat().st_mtime
    except Exception:
        pass
    return 0.0

@dataclass
class PollingWatcher:
    name: str
    directory: Path
    interval: int
    debounce: int
    last_digest: str = field(default="")

    def _iter_files(self):
        if not self.directory.exists():
            return []
        files = []
        for p in self.directory.rglob("*"):
            if p.is_file() and ".git" not in p.parts:
                files.append(p)
        return files

    def _compute_digest(self):
        h = hashlib.sha256()
        for f in sorted(self._iter_files()):
            try:
                st = f.stat()
                rel = str(f.relative_to(self.directory))
                h.update(rel.encode()); h.update(str(st.st_mtime_ns).encode()); h.update(str(st.st_size).encode())
            except Exception:
                continue
        return h.hexdigest()

    def run(self, parent: Path, public_dir: Path, staging_dir: Path, git_ref: str, api_base: str, repo_dir_for_commit: Path, commit_msg: str, loop_guard_sec: int = 10):
        while True:
            try:
                digest = self._compute_digest()
                if self.last_digest and digest != self.last_digest:
                    time.sleep(max(2, self.debounce))
                    digest2 = self._compute_digest()
                    if digest2 != digest:
                        self.last_digest = digest2
                        continue

                    # Ignore publish loop for publish watcher
                    if self.name == "publish":
                        stamp_time = get_publish_stamp_time(public_dir)
                        if stamp_time and (time.time() - stamp_time) < loop_guard_sec:
                            self.last_digest = digest2
                            time.sleep(max(2, self.interval))
                            continue

                    with open(LOCK_FILE, "a+") as lf:
                        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
                        try:
                            if is_git_repo(parent):
                                # Variant B: do not commit inside submodules; update from remote, bump in parent, then publish
                                submodules_sync_update(parent, use_remote=True)
                                ok = bump_parent_submodules(parent, "chore(sync): bump submodules")
                                # Publish current state to public
                                publish_from_parent(parent, public_dir, staging_dir, api_base)
                                # Fingerprint will be updated by server webhook path; here we just proceed
                            else:
                                log(f"[{self.name}] parent repo {parent} is not a git repo; skipping")
                        finally:
                            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

                    self.last_digest = digest2
                else:
                    if not self.last_digest:
                        self.last_digest = digest
                time.sleep(max(2, self.interval))
            except Exception as e:
                log(f"[{self.name}] watcher error:", e)
                time.sleep(max(2, self.interval))

# ---------------- HTTP/Webhook and server bootstrap ----------------
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
        evt = self.headers.get("X-GitHub-Event", "")
        if evt and evt != "push":
            self.send_response(202)
            self.end_headers()
            return
        with open(LOCK_FILE, "a+") as lf:
            log("acquiring lock")
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                parent = Path(os.environ.get("REPO_DIR", "/srv/repo"))
                # Use PUBLIC_DIR if provided; fallback to /srv/public for backward compatibility
                public = Path(os.environ.get("PUBLIC_DIR", "/srv/public"))
                staging = Path(os.environ.get("STAGING_DIR", "/srv/staging"))
                git_ref = os.environ.get("GIT_REF", "main")
                api_base = os.environ.get("API_BASE_URL", "")
                ok = process_update(parent, public, staging, git_ref, api_base)
                if ok:
                    fp = compute_fingerprint(parent, git_ref)
                    write_fingerprint(fp)
                else:
                    log("sync failed; fingerprint not updated")
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, fmt, *args):
        log(fmt % args)

def start_server(addr: str, port: int, parent: Path, public: Path, staging: Path):
    os.environ["REPO_DIR"] = str(parent)
    os.environ["PUBLIC_DIR"] = str(public)
    os.environ["STAGING_DIR"] = str(staging)

    # Watchers on submodules inside parent
    watch_interval = int(os.environ.get("WATCH_INTERVAL_SECONDS", os.environ.get("FS_WATCH_INTERVAL", "5")))
    debounce = int(os.environ.get("DEBOUNCE_SECONDS", "3"))
    git_ref = os.environ.get("GIT_REF", "main")
    api_base = os.environ.get("API_BASE_URL", "")

    content_dir = parent / "redpen-content"
    publish_dir = parent / "redpen-publish"

    if content_dir.exists():
        cont_watcher = PollingWatcher(name="content", directory=content_dir, interval=watch_interval, debounce=debounce)
        threading.Thread(
            target=cont_watcher.run,
            args=(parent, public, staging, git_ref, api_base, content_dir, "chore(sync): content local update"),
            name="content-watcher",
            daemon=True
        ).start()
    else:
        log("content submodule directory not found; content watcher disabled")

    if publish_dir.exists():
        pub_watcher = PollingWatcher(name="publish", directory=publish_dir, interval=watch_interval, debounce=debounce)
        threading.Thread(
            target=pub_watcher.run,
            args=(parent, public, staging, git_ref, api_base, publish_dir, "chore(sync): publish local update"),
            name="publish-watcher",
            daemon=True
        ).start()
    else:
        log("publish submodule directory not found; publish watcher disabled")

    httpd = HTTPServer((addr, port), Handler)
    log(f"Webhook server listening on {addr}:{port}; fs watch interval={watch_interval}s, debounce={debounce}s")
    httpd.serve_forever()














# --------------- Submodule bidirectional sync helpers ---------------





def has_worktree_changes(sub_dir: Path) -> bool:
    try:
        st = run_capture(["git", "status", "--porcelain"], cwd=str(sub_dir))
        return bool(st.strip())
    except Exception:
        return False


def ahead_behind(sub_dir: Path, branch: str):
    try:
        run(["git", "fetch", "--all", "--prune"], cwd=str(sub_dir))
        out = run_capture(["git", "rev-list", "--left-right", "--count", f"HEAD...origin/{branch}"], cwd=str(sub_dir))
        left, right = out.split()
        return int(left), int(right)
    except Exception:
        return 0, 0






# --------------- Filesystem watchers and sync from local sources ---------------

from dataclasses import dataclass, field
import shutil










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
        parent = Path(args.repo or "/srv/repo")
        public = Path(args.public or "/srv/public")
        staging = Path(args.staging or "/srv/staging")
        start_server(args.addr, args.port, parent, public, staging)
    else:
        print("Specify --server or --mutate-only", file=sys.stderr)
        sys.exit(2)
