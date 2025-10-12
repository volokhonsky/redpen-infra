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

LOCK_FILE = "/srv/public/.sync.lock"
FINGERPRINT_FILE = "/srv/public/.last_fingerprint"

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

    # Write a publish stamp to help the public watcher ignore our own changes
    try:
        stamp = public_dir / ".published_by_sync"
        stamp.write_text(str(int(time.time())), encoding="utf-8")
    except Exception as e:
        log("failed to write publish stamp:", e)


def process_update(repo: Path, public: Path, staging: Path, git_ref: str, api_base: str) -> bool:
    # First: local bidirectional submodule sync (commit/pull --rebase/push)
    log("starting local submodules sync")
    subs_ok = local_submodules_sync(repo)
    if not subs_ok:
        log("submodule sync failed for at least one module; skipping publish")
        return False

    # Fetch/reset and publish
    run(["git", "fetch", "--all", "--prune"], cwd=str(repo))
    # Try to reset to origin/ref, fallback to ref
    try:
        run(["git", "reset", "--hard", f"origin/{git_ref}"], cwd=str(repo))
    except subprocess.CalledProcessError:
        run(["git", "checkout", "-f", git_ref], cwd=str(repo))

    # Sync submodule configurations (URLs/branches) before updating
    try:
        run(["git", "submodule", "sync", "--recursive"], cwd=str(repo))
    except Exception:
        pass

    # Update submodules: default strategy is remote-tracking with fallback
    strategy = os.environ.get("SUBMODULE_STRATEGY", "remote").strip().lower()
    if strategy == "remote":
        try:
            run(["git", "submodule", "update", "--init", "--recursive", "--remote"], cwd=str(repo))
        except Exception as e:
            log("remote submodule update failed, falling back to recorded commits:", e)
            try:
                run(["git", "submodule", "update", "--init", "--recursive"], cwd=str(repo))
            except Exception:
                pass
    else:
        # Recorded commits strategy
        try:
            run(["git", "submodule", "update", "--init", "--recursive"], cwd=str(repo))
        except Exception:
            pass

    # Optionally, force-clean submodules to origin/<branch> after update
    try:
        for name, path in list_submodules(repo):
            sub_dir = (repo / path).resolve()
            br = detect_branch_for_submodule(sub_dir)
            try:
                run(["git", "fetch", "--all", "--prune"], cwd=str(sub_dir))
                run(["git", "reset", "--hard", f"origin/{br}"], cwd=str(sub_dir))
            except Exception:
                pass
    except Exception:
        pass

    publish(repo, staging, public, api_base)
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


def compute_fingerprint(repo: Path, git_ref: str) -> str:
    # Ensure remotes are up to date before computing
    try:
        run(["git", "fetch", "--all", "--prune"], cwd=str(repo))
    except Exception:
        pass
    # Fetch inside submodules to see remote tips
    try:
        run(["git", "submodule", "foreach", "--recursive", "git fetch --all --prune || true"], cwd=str(repo))
    except Exception:
        pass
    # Remote tip of target branch
    remote_tip = ""
    try:
        out = subprocess.check_output(["git", "ls-remote", "--heads", "origin", git_ref], cwd=str(repo)).decode().strip()
        remote_tip = out.split()[0] if out else ""
    except Exception:
        remote_tip = ""
    # Local HEAD
    try:
        local_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(repo)).decode().strip()
    except Exception:
        local_head = ""
    # Submodule statuses (recursive)
    try:
        subs = subprocess.check_output(["git", "submodule", "status", "--recursive"], cwd=str(repo)).decode()
    except Exception:
        subs = ""

    # Submodule remote default heads (origin/HEAD target) after fetch
    try:
        sub_heads_cmd = [
            "git",
            "submodule",
            "foreach",
            "--recursive",
            "sh -c 'echo $name; git ls-remote --symref origin HEAD 2>/dev/null || true'",
        ]
        sub_remote_heads = subprocess.check_output(sub_heads_cmd, cwd=str(repo)).decode()
    except Exception:
        sub_remote_heads = ""

    # Dirty flag
    try:
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=str(repo)).decode().strip()
    except Exception:
        dirty = ""
    dirty_flag = "1" if dirty else "0"
    fp = (
        f"remote={remote_tip}\n"
        f"local={local_head}\n"
        f"dirty={dirty_flag}\n"
        f"subs:\n{subs}"
        f"sub_remote_heads:\n{sub_remote_heads}"
    )
    return fp


# --------------- Submodule bidirectional sync helpers ---------------

def list_submodules(repo: Path):
    """Return list of (name, path) tuples from .gitmodules; fallback to submodule status parsing."""
    res = []
    try:
        names_output = run_capture(["git", "config", "-f", ".gitmodules", "--name-only", "--get-regexp", "^submodule\\..*\\.path$"], cwd=str(repo))
        for line in names_output.splitlines():
            # line like: submodule.redpen-content.path
            name = line.split(".")[1]
            path = run_capture(["git", "config", "-f", ".gitmodules", f"submodule.{name}.path"], cwd=str(repo)).strip()
            if path:
                res.append((name, path))
    except Exception:
        pass
    if not res:
        # Fallback: parse 'git submodule status'
        try:
            out = run_capture(["git", "submodule", "status"], cwd=str(repo))
            for line in out.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2:
                    path = parts[1]
                    res.append((path.replace("/", "_"), path))
        except Exception:
            pass
    return res


def detect_branch_for_submodule(sub_dir: Path) -> str:
    # Try to detect upstream tracking branch name
    try:
        upstream = run_capture(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=str(sub_dir))
        if upstream and "/" in upstream:
            return upstream.split("/", 1)[1]
    except Exception:
        pass
    # Try current branch name
    try:
        br = run_capture(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], cwd=str(sub_dir))
        if br:
            return br
    except Exception:
        pass
    # Try remote HEAD
    try:
        headref = run_capture(["git", "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"], cwd=str(sub_dir))
        if headref and headref.startswith("origin/"):
            return headref.split("/", 1)[1]
    except Exception:
        pass
    return "main"


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


def sync_one_submodule(repo: Path, name: str, rel_path: str) -> bool:
    sub_dir = (repo / rel_path).resolve()
    branch = detect_branch_for_submodule(sub_dir)
    log(f"[submodule:{name}] branch={branch}")

    # Auto-commit local changes
    try:
        if has_worktree_changes(sub_dir):
            log(f"[submodule:{name}] local changes detected; committing")
            run(["git", "add", "-A"], cwd=str(sub_dir))
            run(["git", "-c", f"user.name={COMMIT_AUTHOR_NAME}", "-c", f"user.email={COMMIT_AUTHOR_EMAIL}", "commit", "-m", "chore(sync): server-side update"], cwd=str(sub_dir))
        else:
            log(f"[submodule:{name}] no worktree changes")
    except subprocess.CalledProcessError as e:
        log(f"[submodule:{name}] commit step failed:", e)
        return False

    # Rebase on origin and push
    try:
        run(["git", "fetch", "--all", "--prune"], cwd=str(sub_dir))
        # Attempt rebase
        try:
            run(["git", "pull", "--rebase", "origin", branch], cwd=str(sub_dir))
        except subprocess.CalledProcessError as e:
            # Conflict handling
            conflict_files = ""
            try:
                conflict_files = run_capture(["git", "diff", "--name-only", "--diff-filter=U"], cwd=str(sub_dir))
            except Exception:
                pass
            try:
                run(["git", "rebase", "--abort"], cwd=str(sub_dir))
            except Exception:
                pass
            log(f"[submodule:{name}] rebase conflict; files=\n{conflict_files}")
            return False

        ahead, behind = ahead_behind(sub_dir, branch)
        if ahead > 0 or has_worktree_changes(sub_dir):
            # Push local commits
            try:
                run(["git", "push", "origin", branch], cwd=str(sub_dir))
            except subprocess.CalledProcessError:
                # maybe no upstream; try set upstream
                try:
                    run(["git", "push", "--set-upstream", "origin", branch], cwd=str(sub_dir))
                except subprocess.CalledProcessError as e2:
                    log(f"[submodule:{name}] push failed:", e2)
                    return False
            log(f"[submodule:{name}] pushed to origin/{branch}")
        else:
            log(f"[submodule:{name}] nothing to push (ahead={ahead}, behind={behind})")
    except subprocess.CalledProcessError as e:
        log(f"[submodule:{name}] sync failed:", e)
        return False

    return True


def local_submodules_sync(repo: Path) -> bool:
    subs = list_submodules(repo)
    if not subs:
        log("no submodules found to sync")
        return True
    log(f"found {len(subs)} submodule(s) to sync: {[p for _, p in subs]}")
    overall_ok = True
    for name, path in subs:
        ok = sync_one_submodule(repo, name, path)
        overall_ok = overall_ok and ok
    return overall_ok


# --------------- Filesystem watchers and sync from local sources ---------------

from dataclasses import dataclass, field
import shutil


def get_submodule_path(repo: Path, env_var: str, keyword: str) -> Optional[Path]:
    # Try env override as relative or absolute path
    override = os.environ.get(env_var, "").strip()
    if override:
        p = Path(override)
        return p if p.is_absolute() else (repo / p)
    # Find by keyword
    for name, rel in list_submodules(repo):
        if keyword in name.lower() or keyword in rel.lower():
            return (repo / rel).resolve()
    return None


def rsync_copy(src: Path, dst: Path, mode: str) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    if mode == "public":
        run(["rsync", "-a", "--delete", "--exclude", ".git", f"{src}/", f"{dst}/"])  # type: ignore
    else:
        # content: only md and metadata
        cmd = [
            "rsync", "-a", "--delete",
            "--exclude", ".git",
            "--include", "*/",
            "--include", "*.md",
            "--include", "*.markdown",
            "--include", "*.json",
            "--include", "*.yaml",
            "--include", "*.yml",
            "--exclude", "*",
            f"{src}/", f"{dst}/",
        ]
        run(cmd)


def sync_source_to_submodule(source_name: str, source_dir: Path, repo: Path, mode: str) -> bool:
    sub_env = "SUBMODULE_PUBLISH_PATH" if mode == "public" else "SUBMODULE_CONTENT_PATH"
    keyword = "publish" if mode == "public" else "content"
    sub_dir = get_submodule_path(repo, sub_env, keyword)
    if not sub_dir:
        log(f"[{source_name}] submodule for '{keyword}' not found; skipping")
        return False
    # Copy changes into submodule worktree
    rsync_copy(source_dir, sub_dir, mode)

    # Commit and push
    try:
        if has_worktree_changes(sub_dir):
            run(["git", "add", "-A"], cwd=str(sub_dir))
            run([
                "git", "-c", f"user.name={COMMIT_AUTHOR_NAME}",
                "-c", f"user.email={COMMIT_AUTHOR_EMAIL}",
                "commit", "-m", f"chore(sync): {source_name} local update"
            ], cwd=str(sub_dir))
        else:
            log(f"[{source_name}] no changes after copy; nothing to commit")
    except subprocess.CalledProcessError as e:
        log(f"[{source_name}] commit failed:", e)
        return False

    # Pull --rebase and push
    branch = detect_branch_for_submodule(sub_dir)
    try:
        run(["git", "fetch", "--all", "--prune"], cwd=str(sub_dir))
        try:
            run(["git", "pull", "--rebase", "origin", branch], cwd=str(sub_dir))
        except subprocess.CalledProcessError as e:
            conflict_files = ""
            try:
                conflict_files = run_capture(["git", "diff", "--name-only", "--diff-filter=U"], cwd=str(sub_dir))
            except Exception:
                pass
            try:
                run(["git", "rebase", "--abort"], cwd=str(sub_dir))
            except Exception:
                pass
            log(f"[{source_name}] rebase conflict; files=\n{conflict_files}")
            return False
        ahead, behind = ahead_behind(sub_dir, branch)
        if ahead > 0 or has_worktree_changes(sub_dir):
            try:
                run(["git", "push", "origin", branch], cwd=str(sub_dir))
            except subprocess.CalledProcessError:
                try:
                    run(["git", "push", "--set-upstream", "origin", branch], cwd=str(sub_dir))
                except subprocess.CalledProcessError as e2:
                    log(f"[{source_name}] push failed:", e2)
                    return False
            log(f"[{source_name}] pushed to origin/{branch}")
        else:
            log(f"[{source_name}] nothing to push (ahead={ahead}, behind={behind})")
    except subprocess.CalledProcessError as e:
        log(f"[{source_name}] sync failed:", e)
        return False
    return True


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
    mode: str  # "public" or "content"
    interval: int
    debounce: int
    mask_mode: str = field(default="public")
    last_digest: str = field(default="")

    def _iter_files(self):
        if not self.directory.exists():
            return []
        res = []
        if self.mode == "public":
            for p in self.directory.rglob("*"):
                if p.is_file() and ".git" not in p.parts:
                    res.append(p)
        else:
            for p in self.directory.rglob("*"):
                if p.is_file() and ".git" not in p.parts:
                    if p.suffix.lower() in (".md", ".markdown", ".json", ".yaml", ".yml"):
                        res.append(p)
        return res

    def _compute_digest(self):
        files = self._iter_files()
        h = hashlib.sha256()
        rels = []
        base = self.directory
        for f in sorted(files):
            try:
                st = f.stat()
                rel = str(f.relative_to(base))
                rels.append(rel)
                h.update(rel.encode())
                h.update(str(st.st_mtime_ns).encode())
                h.update(str(st.st_size).encode())
            except Exception:
                continue
        return h.hexdigest(), rels

    def run(self, repo: Path, public_dir: Path, staging_dir: Path, git_ref: str, api_base: str):
        loop_guard = int(os.environ.get("LOOP_GUARD_SECONDS", "10"))
        while True:
            try:
                digest, files = self._compute_digest()
                if self.last_digest and digest != self.last_digest:
                    # Debounce
                    time.sleep(max(2, self.debounce))
                    digest2, files2 = self._compute_digest()
                    if digest2 != digest:
                        # More changes during debounce; update baseline and continue
                        self.last_digest = digest2
                        continue

                    # Loop prevention for public edits after publish
                    if self.mode == "public":
                        stamp_time = get_publish_stamp_time(public_dir)
                        if stamp_time and (time.time() - stamp_time) < loop_guard:
                            log(f"[{self.name}] changes ignored due to recent publish (loop guard {loop_guard}s)")
                            self.last_digest = digest2
                            continue

                    log(f"[{self.name}] detected changes in {self.directory}; files (debounced)={files2}")
                    # Acquire global lock and process
                    with open(LOCK_FILE, "a+") as lf:
                        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
                        try:
                            ok_src = sync_source_to_submodule(self.name, self.directory, repo, self.mode)
                            if not ok_src:
                                log(f"[{self.name}] source sync failed; not publishing (other sources unaffected)")
                            else:
                                ok_pub = process_update(repo, public_dir, staging_dir, git_ref, api_base)
                                if not ok_pub:
                                    log(f"[{self.name}] publish failed after source sync")
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
                repo = Path(os.environ.get("REPO_DIR", "/srv/repo"))
                public = Path(os.environ.get("PUBLIC_DIR", "/srv/public"))
                staging = Path(os.environ.get("STAGING_DIR", "/srv/staging"))
                git_ref = os.environ.get("GIT_REF", "main")
                api_base = os.environ.get("API_BASE_URL", "")
                ok = process_update(repo, public, staging, git_ref, api_base)
                if ok:
                    # Update fingerprint after successful publish
                    fp = compute_fingerprint(repo, git_ref)
                    write_fingerprint(fp)
                else:
                    log("sync failed; fingerprint not updated")
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, fmt, *args):
        # Silence default access log; use our log()
        log(fmt % args)


def start_server(addr: str, port: int, repo: Path, public: Path, staging: Path):
    os.environ["REPO_DIR"] = str(repo)
    os.environ["PUBLIC_DIR"] = str(public)
    os.environ["STAGING_DIR"] = str(staging)

    # Start background monitor thread
    interval = int(os.environ.get("MONITOR_INTERVAL_SECONDS", os.environ.get("MONITOR_INTERVAL", "60")))
    git_ref = os.environ.get("GIT_REF", "main")
    api_base = os.environ.get("API_BASE_URL", "")

    def monitor_loop():
        last_fp: Optional[str] = read_fingerprint()
        while True:
            try:
                fp = compute_fingerprint(repo, git_ref)
                if last_fp != fp:
                    log("change detected by monitor; acquiring lock to sync")
                    with open(LOCK_FILE, "a+") as lf:
                        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
                        try:
                            # Double-check after lock to avoid duplicate publish if another path updated
                            new_fp = compute_fingerprint(repo, git_ref)
                            if new_fp != read_fingerprint():
                                ok2 = process_update(repo, public, staging, git_ref, api_base)
                                if ok2:
                                    write_fingerprint(new_fp)
                                    last_fp = new_fp
                                else:
                                    log("sync failed in monitor; fingerprint unchanged")
                                    last_fp = read_fingerprint() or new_fp
                            else:
                                last_fp = new_fp
                        finally:
                            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
                else:
                    log("no changes detected (debounced)")
            except Exception as e:
                log("monitor error:", e)
            time.sleep(max(5, interval))

    t = threading.Thread(target=monitor_loop, name="content-monitor", daemon=True)
    t.start()

    # Start filesystem watchers (polling)
    watch_interval = int(os.environ.get("WATCH_INTERVAL_SECONDS", os.environ.get("FS_WATCH_INTERVAL", "5")))
    debounce = int(os.environ.get("DEBOUNCE_SECONDS", "3"))
    enable_public = os.environ.get("ENABLE_PUBLIC_WATCH", "1") not in ("0", "false", "False")
    enable_content = os.environ.get("ENABLE_CONTENT_WATCH", "1") not in ("0", "false", "False")

    public_watch_dir = Path(os.environ.get("PUBLIC_WATCH_DIR", str(public)))
    content_watch_dir = Path(os.environ.get("CONTENT_WATCH_DIR", "/srv/content"))

    if enable_public:
        pub_watcher = PollingWatcher(name="public", directory=public_watch_dir, mode="public", interval=watch_interval, debounce=debounce)
        threading.Thread(target=pub_watcher.run, args=(repo, public, staging, git_ref, api_base), name="public-watcher", daemon=True).start()
    else:
        log("public watcher disabled by env")

    if enable_content:
        cont_watcher = PollingWatcher(name="content", directory=content_watch_dir, mode="content", interval=watch_interval, debounce=debounce)
        threading.Thread(target=cont_watcher.run, args=(repo, public, staging, git_ref, api_base), name="content-watcher", daemon=True).start()
    else:
        log("content watcher disabled by env")

    httpd = HTTPServer((addr, port), Handler)
    log(f"Webhook server listening on {addr}:{port}; monitor interval={interval}s, fs watch interval={watch_interval}s, debounce={debounce}s")
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
