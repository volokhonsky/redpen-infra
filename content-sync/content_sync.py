#!/usr/bin/env python3
import hmac
import hashlib
import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
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

# --- Parent repo + nested repos (single source of truth) ---
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
    """Update nested repos if present, without relying on Git submodules.

    This function looks for redpen-content and redpen-publish directories
    under the parent and, if they are Git repositories, performs fetch+pull.
    It's safe to call even if those directories are not git repos.
    """
    for name in ("redpen-content", "redpen-publish"):
        repo_dir = parent / name
        if not repo_dir.exists():
            continue
        if is_git_repo(repo_dir):
            try:
                run(["git", "fetch", "--all", "--prune"], cwd=str(repo_dir))
                br = detect_branch(repo_dir)
                try:
                    run(["git", "pull", "--rebase", "origin", br], cwd=str(repo_dir))
                except subprocess.CalledProcessError:
                    try:
                        run(["git", "rebase", "--abort"], cwd=str(repo_dir))
                    except Exception:
                        pass
                    log(f"[{repo_dir}] rebase conflict during pull; leaving as-is")
            except Exception as e:
                log(f"failed to update nested repo {repo_dir}:", e)

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

def mutate_staging(staging_dir: Path, api_base_url: str) -> None:
    """Правки статики на месте развёртывания. Сейчас — ни одной.

    Здесь жила подстановка адреса API: файл `app-config.js` с `window.APP_CONFIG`,
    тег на него в HTML клиентов API и заплатка `apiBase()` в
    `redpen-editor-bootstrap.js`. Единственным потребителем `APP_CONFIG` был этот
    самый бутстрап старого SPA; SPA удалён 2026-08-30, а /work/ и /survey/
    определяют адрес API сами, инлайновым блоком в своём index.html.
    Функция оставлена точкой расширения: `--mutate-only` зовут из
    `entrypoint.sh`, и убирать вызов — отдельная выкладка контейнера.
    """
    return


def publish_from_parent(parent: Path, public_dir: Path, staging_dir: Path, api_base_url: str) -> None:
    src = parent / "redpen-publish"
    if not src.exists():
        log("publish source redpen-publish not found in parent repo")
        return
    if staging_dir.exists():
        subprocess.call(["rm", "-rf", str(staging_dir)])
    staging_dir.mkdir(parents=True, exist_ok=True)
    # */remarks/ is owned by the API (stage 2: SQLite is canonical, the
    # API's publisher.py writes page_NNN.json straight into public_dir). Without
    # --delete-excluded, excluding it from --delete also protects it from being
    # wiped by this sync -- only the API and import_remarks.py write there.
    # Исключение для */annotations/ (прежнее имя того же каталога) снято в
    # фазе 6 переименования 2026-08-30: publisher туда больше не пишет, и
    # первый прогон после выкладки уносит каталог с тома. Это и есть уборка.
    run(["rsync", "-a", "--delete", "--exclude", ".git", "--exclude", "/*/remarks/", f"{src}/", f"{staging_dir}/"])
    mutate_staging(staging_dir, api_base_url)
    run(["rsync", "-a", "--delete", "--exclude", "/*/remarks/", f"{staging_dir}/", f"{public_dir}/"])
    _ensure_api_owned_dirs(public_dir)
    try:
        (public_dir / ".published_by_sync").write_text(str(int(time.time())), encoding="utf-8")
    except Exception as e:
        log("failed to write publish stamp:", e)


#: Каталоги в томе, куда пишет API (uid 10001). content-sync работает от root,
#: и после каждого rsync владелец возвращается к root — поэтому владение
#: восстанавливается здесь, после публикации.
#:
#: `remarks` — канон публикации, его владелец API с этапа 2.
#: `pages` — HTML читателя. У него два писателя: сборка (через git и rsync) и
#: API, который перерисовывает затронутую страницу на каждую правку. Просмотрщик
#: читает замечания не из JSON, а из инлайнового блока внутри этого HTML, так что
#: без права записи сюда правка через редактор до читателя не доезжает.
_API_OWNED_DIRS = ("remarks", "pages")


def _ensure_api_owned_dirs(public_dir: Path) -> None:
    """Create the API-owned directories for any new docId and fix ownership on
    existing ones. content-sync runs as root, but the API container writes as
    uid 10001 -- without this, a fresh docId directory (or one still owned by
    root after an rsync) makes the API's writes fail."""
    if not public_dir.exists():
        return
    for doc_dir in public_dir.iterdir():
        # Каталог книги опознаём по metadata.json: на первом уровне тома лежат
        # ещё js/, css/, work/, survey/ — им пустые remarks/ и pages/
        # ни к чему (до 2026-08-31 цикл создавал их всем подряд).
        if not (doc_dir / "metadata.json").is_file():
            continue
        for name in _API_OWNED_DIRS:
            target = doc_dir / name
            target.mkdir(exist_ok=True)
            subprocess.call(["chown", "-R", "10001:10001", str(target)])

def bump_parent_submodules(parent: Path, msg: str) -> bool:
    """No-op in independent-repos mode.

    Historically, this staged submodule pointers in the parent repo. Now that
    redpen-content and redpen-publish are independent repos, there are no
    gitlinks to bump in the parent. We keep this function to preserve flow,
    but it simply returns True.
    """
    try:
        # If someone still uses submodules, gracefully handle by attempting to add
        gm = parent / ".gitmodules"
        if gm.exists():
            try:
                run(["git", "add", "redpen-content", "redpen-publish", ".gitmodules"], cwd=str(parent))
                return commit_pull_push(parent, msg)
            except Exception:
                pass
        return True
    except Exception as e:
        log("bump_parent_submodules noop failed:", e)
        return True


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
#: Больше этого вебхук не читает. Тело события GitHub про один push — это
#: килобайты JSON. Предел стоит здесь, а не только в Caddy: маршрут открыт
#: наружу, тело читается в память целиком, и без предела один большой POST
#: стоит дороже тысячи обычных. Проверяется ДО чтения, по Content-Length:
#: читать гигабайт, чтобы потом отвергнуть его по подписи, незачем.
MAX_WEBHOOK_BODY = 256 * 1024


#: Синхронизация идёт в фоне, и звать её второй раз, пока идёт первая, незачем:
#: она в любом случае забирает текущее состояние репозитория, а не то, которое
#: было в момент вызова. Флаг заодно не даёт развести потоки без счёта.
_sync_pending = threading.Lock()


def run_sync_under_lock() -> None:
    """Синхронизация под межпроцессным замком. Вызывается из потока вебхука."""
    try:
        _run_sync_body()
    finally:
        _sync_pending.release()


def _run_sync_body() -> None:
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
        except Exception as exc:
            log("sync failed with an exception:", exc)
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, body: bytes = b"") -> None:
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path not in ("/webhook", "/.hooks/redpen-publish"):
            self._reply(404)
            return
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except (TypeError, ValueError):
            # Мусор в заголовке прежде ронял обработчик исключением.
            log("bad Content-Length:", raw_length)
            self._reply(400)
            return
        if length < 0 or length > MAX_WEBHOOK_BODY:
            log("body too large:", length)
            self._reply(413)
            return
        payload = self.rfile.read(length)
        signature = self.headers.get("X-Hub-Signature-256", "")
        secret = os.environ.get("WEBHOOK_SECRET", "")
        if not secret or not verify_signature(secret, payload, signature):
            log("invalid signature")
            self._reply(401)
            return
        evt = self.headers.get("X-GitHub-Event", "")
        if evt and evt != "push":
            self._reply(202)
            return
        # Ответ раньше работы: git fetch и rsync занимают минуты, и держать
        # ради них соединение открытым незачем — отправитель вебхука
        # результата всё равно не читает. Работа идёт в отдельном потоке,
        # межпроцессный замок в run_sync_under_lock оставляет её
        # единственной, даже если вебхук позвали десять раз подряд.
        self._reply(202, b"accepted")
        if not _sync_pending.acquire(blocking=False):
            log("sync already running; this call is folded into it")
            return
        threading.Thread(target=run_sync_under_lock,
                         name="webhook-sync", daemon=True).start()

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
        log("content directory not found; content watcher disabled")

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

    # ThreadingHTTPServer, а не HTTPServer: одиночный сервер обслуживал по
    # одному соединению за раз, и одного медленного клиента хватало, чтобы
    # вебхук перестал отвечать. Маршрут открыт наружу (Caddy проксирует
    # /.hooks/redpen-publish), поэтому занять его может кто угодно.
    httpd = ThreadingHTTPServer((addr, port), Handler)
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
