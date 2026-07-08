import json
import logging
import sys
import time
import secrets
from uuid import uuid4
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

import os
import re
import tempfile
import importlib.util

# Optional .env loading
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

import config
import storage

# Store for session tokens (in-memory for now; moves to SQLite in stage 1)
_session_store: Dict[str, Dict[str, str]] = {}
SESSION_TTL_SECONDS = 86400 * 7  # 7 days, matches cookie max_age


def _create_session(user_id: str, username: str) -> str:
    session_id = secrets.token_hex(16)
    now = datetime.utcnow()
    _session_store[session_id] = {
        "userId": user_id,
        "username": username,
        "createdAt": now.isoformat(),
        "expiresAt": (now + timedelta(seconds=SESSION_TTL_SECONDS)).isoformat(),
    }
    return session_id


def _get_session(session_id: Optional[str]) -> Optional[Dict[str, str]]:
    """Look up a session by id, evicting it if expired or malformed."""
    if not session_id:
        return None
    session = _session_store.get(session_id)
    if not session:
        return None
    expires_at = session.get("expiresAt")
    try:
        if not expires_at or datetime.fromisoformat(expires_at) < datetime.utcnow():
            _session_store.pop(session_id, None)
            return None
    except ValueError:
        _session_store.pop(session_id, None)
        return None
    return session


# Absolute path of the log file, derived from the configurable log directory.
LOG_FILE = os.path.join(config.LOG_DIR, "redpen-api.log")


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("redpen.api")
    if not logger.handlers:
        # Ensure logs directory exists (configurable so the service runs and can
        # be tested outside the container where /app is read-only)
        logs_dir = config.LOG_DIR
        os.makedirs(logs_dir, exist_ok=True)
        
        # Console handler (stdout)
        console_handler = logging.StreamHandler(sys.stdout)
        console_fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%dT%H:%M:%S%z")
        console_handler.setFormatter(console_fmt)
        logger.addHandler(console_handler)
        
        # File handler (logs/redpen-api.log)
        file_handler = logging.FileHandler(LOG_FILE)
        file_fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s", "%Y-%m-%dT%H:%M:%S%z")
        file_handler.setFormatter(file_fmt)
        logger.addHandler(file_handler)
        
        logger.propagate = False
    
    # Set level from config
    level = getattr(logging, (config.LOG_LEVEL or "INFO").upper(), logging.INFO)
    logger.setLevel(level)
    return logger


logger = setup_logger()

app = FastAPI()

# Resolve project root (two levels up from this file)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Dynamically import the annotation converter to reuse existing parsing logic
_annotation_converter_spec = importlib.util.spec_from_file_location(
    "annotation_converter",
    os.path.join(PROJECT_ROOT, "scripts", "annotation_converter.py"),
)
annotation_converter = importlib.util.module_from_spec(_annotation_converter_spec)
assert _annotation_converter_spec is not None and _annotation_converter_spec.loader is not None
_annotation_converter_spec.loader.exec_module(annotation_converter)  # type: ignore

# CORS configuration

allow_origins = config.CORS_ALLOW_ORIGINS or ["*"]
if isinstance(allow_origins, list):
    allow_origins = list(dict.fromkeys(allow_origins))
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup Jinja2 templates
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
os.makedirs(TEMPLATES_DIR, exist_ok=True)
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("service started LOG_LEVEL=%s storage_dir=%s", config.LOG_LEVEL, config.STORAGE_DIR)


# ===== HELPER FUNCTIONS =====

def parse_log_line(line: str) -> dict:
    """Parse log line into structured data"""
    try:
        parts = line.strip().split(" | ")
        if len(parts) >= 3:
            return {
                "timestamp": parts[0],
                "level": parts[1],
                "message": " | ".join(parts[2:])
            }
        elif len(parts) >= 2:
            return {
                "timestamp": parts[0],
                "level": "INFO",
                "message": " | ".join(parts[1:])
            }
        else:
            return {
                "timestamp": "",
                "level": "UNKNOWN",
                "message": line.strip()
            }
    except Exception:
        return {
            "timestamp": "",
            "level": "ERROR",
            "message": line.strip()
        }


def _serialize_size(obj: Dict[str, Any]) -> int:
    try:
        s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return len(s.encode("utf-8"))
    except Exception:
        return 0


async def require_user(request: Request) -> Dict[str, str]:
    """FastAPI dependency: return session user data or raise 401."""
    session_id = request.cookies.get("redpen_session")
    session = _get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="not authenticated")
    return session


async def require_csrf(request: Request, user: Dict[str, str] = Depends(require_user)) -> Dict[str, str]:
    """FastAPI dependency: verify X-CSRF-Token against the session-bound token."""
    header_token = request.headers.get("X-CSRF-Token")
    session_csrf = user.get("csrf")
    if not header_token or not session_csrf or not secrets.compare_digest(header_token, session_csrf):
        raise HTTPException(status_code=403, detail="invalid csrf token")
    return user


def _parse_annotation_body(body: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    ann_type = body.get("annType")
    text = body.get("text")
    coords = body.get("coords", None)

    if not isinstance(ann_type, str) or ann_type.strip() == "":
        raise HTTPException(status_code=400, detail="annType must be a string")
    if not isinstance(text, str):
        raise HTTPException(status_code=400, detail="text must be a string")

    ann: Dict[str, Any] = {"annType": ann_type, "text": text}

    if ann_type != "general":
        if coords is not None:
            if (
                    isinstance(coords, list)
                    and len(coords) >= 2
                    and isinstance(coords[0], int)
                    and isinstance(coords[1], int)
            ):
                ann["coords"] = [coords[0], coords[1]]
            else:
                raise HTTPException(status_code=400, detail="coords must be [x,y] integers")

    if "id" in body and isinstance(body["id"], str) and body["id"].strip() != "":
        ann["id"] = body["id"].strip()

    return ann


# ===== LOG VIEWER ENDPOINTS =====

@app.get("/logs")
async def logs_page(request: Request, user: Dict[str, str] = Depends(require_user)):
    """Serve logs viewer page"""
    try:
        log_file = LOG_FILE
        logs_data = []

        if os.path.exists(log_file):
            with open(log_file, "r", encoding="utf-8") as f:
                lines = f.readlines()

            for line in lines[-500:]:
                if line.strip():
                    logs_data.append(parse_log_line(line))

        return templates.TemplateResponse("logs.html", {
            "request": request,
            "data": logs_data,
            "log_type": "API Logs"
        })
    except Exception as e:
        logger.exception("failed to render logs page")
        return HTMLResponse(f"<h1>Error loading logs</h1><p>{str(e)}</p>", status_code=500)


@app.get("/api/logs")
async def get_logs_json(lines: int = 100, user: Dict[str, str] = Depends(require_user)):
    """Return logs as JSON"""
    try:
        log_file = LOG_FILE
        logs_data = []

        if os.path.exists(log_file):
            with open(log_file, "r", encoding="utf-8") as f:
                all_lines = f.readlines()

            for line in all_lines[-lines:]:
                if line.strip():
                    logs_data.append(parse_log_line(line))

        return {
            "total_lines": len(all_lines) if os.path.exists(log_file) else 0,
            "returned_lines": len(logs_data),
            "logs": logs_data
        }
    except Exception as e:
        logger.exception("failed to read log file")
        return {"error": str(e), "logs": []}


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/auth/login")
async def login(request: Request, response: Response):
    """Accept personal token and create session"""
    try:
        body = await request.json()
    except Exception as e:
        logger.error("login: failed to parse JSON body: %s", str(e))
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    
    token = body.get("token", "").strip()

    logger.info("login: attempt with token length=%d", len(token))

    if not token:
        logger.warning("login: empty token provided")
        raise HTTPException(status_code=401, detail="empty token")

    # Check if token is valid
    username = config.EDITOR_TOKENS.get(token)
    if not username:
        logger.warning("login: invalid token (length=%d)", len(token))
        raise HTTPException(status_code=401, detail="invalid token")
    
    # Create session
    user_id = f"user-{abs(hash(username)) % 100000}"
    session_id = _create_session(user_id, username)

    # Set session cookie
    response.set_cookie(
        "redpen_session",
        session_id,
        httponly=True,
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
    )

    logger.info("login: success username=%s userId=%s", username, user_id)
    return {"userId": user_id, "username": username}


@app.get("/api/auth/csrf")
async def get_csrf(user: Dict[str, str] = Depends(require_user)):
    """Issue a CSRF token bound to the current session (requires login)"""
    csrf_token = f"csrf-{secrets.token_hex(16)}"
    user["csrf"] = csrf_token
    logger.info("csrf: token issued length=%d", len(csrf_token))
    return {"csrfToken": csrf_token}


@app.get("/api/auth/me")
async def get_me(user: Dict[str, str] = Depends(require_user)):
    """Return current user info from session"""
    logger.info("auth/me: success username=%s", user["username"])
    return {
        "userId": user["userId"],
        "username": user["username"]
    }


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    """Delete the current session and clear its cookie"""
    session_id = request.cookies.get("redpen_session")
    if session_id:
        _session_store.pop(session_id, None)
    response.delete_cookie("redpen_session")
    return {"ok": True}


@app.get("/api/hello")
async def hello():
    # Minimal Hello endpoint for local smoke tests
    now = datetime.now().isoformat()
    version = "local-dev"
    return {"message": "Hello, RedPen!", "version": version, "now": now}


@app.post("/api/store-raw")
async def store_raw(request: Request, user: Dict[str, str] = Depends(require_csrf)):
    # New endpoint that supports optional bucket/pageId and enhanced response
    try:
        body_any: Any = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    if not isinstance(body_any, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    # Extract optional fields
    raw_bucket = body_any.get("bucket") if isinstance(body_any.get("bucket"), str) else None
    page_id = body_any.get("pageId") if isinstance(body_any.get("pageId"), str) else None

    # Decide sanitization mode and candidate
    bucket = None
    if raw_bucket:
        cand = raw_bucket
        bucket = storage.sanitize_bucket(cand, for_page_id=False)
    elif page_id:
        cand = page_id
        bucket = storage.sanitize_bucket(cand, for_page_id=True)

    if not bucket:
        bucket = None

    # Prepare payload with metadata
    received_at = datetime.utcnow().isoformat()
    remote_addr: Optional[str] = None
    try:
        client = request.client
        if client:
            remote_addr = client.host
    except Exception:
        remote_addr = None

    payload = {
        "body": body_any,
        "receivedAt": received_at,
        "remoteAddr": remote_addr,
    }

    # Precompute size
    data_str = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    data_size = len(data_str.encode("utf-8"))

    # Generate id and write atomically to final dir
    uid = uuid4().hex
    filename = f"{uid}.json"
    try:
        rel_path = storage.save_inbox(payload, bucket=bucket, filename=filename)
    except Exception:
        logger.exception("failed to store incoming payload")
        raise HTTPException(status_code=500, detail="failed to store")

    # dateDir is the YYYYMMDD component
    parts = rel_path.split("/")
    date_dir = parts[1] if len(parts) >= 3 else None

    # Logging
    logger.info("stored file=%s size=%d bucket=%s", rel_path, data_size, bucket or "-")

    return {
        "stored": True,
        "id": uid,
        "dateDir": date_dir,
        "bucket": bucket if bucket else None,
        "relPath": rel_path,
        "size": data_size,
    }


@app.post("/api/store")
async def store(request: Request, user: Dict[str, str] = Depends(require_csrf)):
    try:
        body: Any = await request.json()
    except Exception:
        # Not a valid JSON
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    received_at = datetime.now().isoformat()
    remote_addr = None
    try:
        client = request.client
        if client:
            remote_addr = client.host
    except Exception:
        remote_addr = None

    payload = {
        "body": body,
        "receivedAt": received_at,
        "remoteAddr": remote_addr,
    }

    # Precompute size using same serialization options as storage
    data_str = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    data_size = len(data_str.encode("utf-8"))

    try:
        rel_path = storage.save_inbox(payload)
    except Exception:
        logger.exception("failed to store incoming payload")
        raise HTTPException(status_code=500, detail="failed to store")

    logger.info("stored file=%s size=%d", rel_path, data_size)
    return {"status": "stored", "path": rel_path}

@app.get("/api/pages/{pageId}")
async def get_page(pageId: str):
    """GET page data by pageId (legacy endpoint, for backwards compatibility)"""
    # pageId format: "medinsky11klass_page_006"
    parts = pageId.rsplit("_page_", 1)
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="invalid pageId format")
    
    doc_id, page_num = parts
    page = storage.load_page(doc_id, page_num)
    
    if not page.get("serverPageSha"):
        page_sha = storage.compute_sha(page)
        page["serverPageSha"] = page_sha
        try:
            storage.save_page(doc_id, page_num, page)
        except Exception:
            logger.exception("failed to persist serverPageSha for pageId=%s", pageId)
    
    size = _serialize_size(page)
    anns = page.get("annotations")
    ann_count = len(anns) if isinstance(anns, list) else 0
    logger.info("GET pageId=%s anns=%d size=%d", pageId, ann_count, size)
    return page


# @app.post("/api/pages/{pageId}/annotations")
# async def post_annotation(pageId: str, request: Request):
#     try:
#         body = await request.json()
#     except Exception:
#         raise HTTPException(status_code=400, detail="body must be a JSON object")
# 
#     ann = _parse_annotation_body(body if isinstance(body, dict) else {})
# 
#     # Generate id if not provided
#     if not ann.get("id"):
#         ann["id"] = f"srv-{int(time.time())}-{uuid4().hex[:6]}"
# 
#     page = storage.load_page(config.STORAGE_DIR, pageId)
#     storage.upsert_annotation(page, ann)
# 
#     try:
#         sha = storage.save_page(config.STORAGE_DIR, page)
#     except Exception:
#         logger.exception("failed to save page after POST annotation pageId=%s", pageId)
#         raise HTTPException(status_code=500, detail="failed to save page")
# 
#     # For response, return id and serverPageSha
#     result = {"id": ann["id"], "serverPageSha": sha}
# 
#     # Logging
#     anns = page.get("annotations")
#     ann_count = len(anns) if isinstance(anns, list) else 0
#     size = _serialize_size(page)
#     logger.info("POST pageId=%s anns=%d size=%d", pageId, ann_count, size)
#     return result

# 
# @app.put("/api/pages/{pageId}/annotations/{id}")
# async def put_annotation(pageId: str, id: str, request: Request):
#     try:
#         body = await request.json()
#     except Exception:
#         raise HTTPException(status_code=400, detail="body must be a JSON object")
# 
#     parsed = _parse_annotation_body(body if isinstance(body, dict) else {})
# 
#     # Ensure the provided id in path is used
#     parsed["id"] = id
# 
#     page = storage.load_page(config.STORAGE_DIR, pageId)
#     updated = storage.update_annotation(page, id, parsed)
#     if not updated:
#         # If not found, append as new per permissive spec
#         storage.upsert_annotation(page, parsed)
# 
#     try:
#         sha = storage.save_page(config.STORAGE_DIR, page)
#     except Exception:
#         logger.exception("failed to save page after PUT annotation pageId=%s id=%s", pageId, id)
#         raise HTTPException(status_code=500, detail="failed to save page")
# 
#     # Logging
#     anns = page.get("annotations")
#     ann_count = len(anns) if isinstance(anns, list) else 0
#     size = _serialize_size(page)
#     logger.info("PUT pageId=%s anns=%d size=%d", pageId, ann_count, size)
# 
#     return {"id": id, "serverPageSha": sha}


@app.post("/api/rebuild/{bookSlug}/annotations/{pageId}")
async def rebuild_annotation_page(bookSlug: str, pageId: str, user: Dict[str, str] = Depends(require_csrf)):
    started = time.time()

    # Validate bookSlug
    if not re.fullmatch(r"[a-z0-9_-]+", bookSlug or ""):
        raise HTTPException(status_code=400, detail="invalid bookSlug")

    # Validate pageId: page_XXX where XXX are digits with leading zeros
    if not re.fullmatch(r"page_\d{3}", pageId or ""):
        raise HTTPException(status_code=400, detail="invalid pageId")

    content_md = os.path.join(PROJECT_ROOT, "redpen-content", bookSlug, "annotations", f"{pageId}.md")
    publish_json_dir = os.path.join(PROJECT_ROOT, "redpen-publish", bookSlug, "annotations")
    publish_json = os.path.join(publish_json_dir, f"{pageId}.json")

    if not os.path.exists(content_md):
        raise HTTPException(status_code=404, detail="markdown not found")

    try:
        with open(content_md, "r", encoding="utf-8") as f:
            md_content = f.read()
    except Exception:
        logger.exception("failed to read markdown for bookSlug=%s pageId=%s path=%s", bookSlug, pageId, content_md)
        raise HTTPException(status_code=500, detail="failed to read markdown")

    try:
        # Reuse the existing converter's parsing logic
        annotations = annotation_converter.parse_markdown_annotation(md_content)
    except Exception:
        logger.exception("conversion failed for bookSlug=%s pageId=%s", bookSlug, pageId)
        raise HTTPException(status_code=500, detail="conversion failed")

    # Ensure target dir exists
    try:
        os.makedirs(publish_json_dir, exist_ok=True)
    except Exception:
        logger.exception("failed to ensure output dir for bookSlug=%s pageId=%s dir=%s", bookSlug, pageId, publish_json_dir)
        raise HTTPException(status_code=500, detail="failed to prepare output directory")

    # Serialize and atomically write JSON
    try:
        data_str = json.dumps(annotations, ensure_ascii=False, indent=2)
        data_bytes = data_str.encode("utf-8")
        fd, tmp_path = tempfile.mkstemp(dir=publish_json_dir, prefix="._tmp_", suffix=".json")
        try:
            with os.fdopen(fd, "wb") as tmp:
                tmp.write(data_bytes)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_path, publish_json)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
        size = len(data_bytes)
    except Exception:
        logger.exception("failed to write JSON for bookSlug=%s pageId=%s path=%s", bookSlug, pageId, publish_json)
        raise HTTPException(status_code=500, detail="failed to write json")

    duration_ms = int((time.time() - started) * 1000)
    rel_json_path = os.path.join("annotations", f"{pageId}.json")

    # Info log per requirements
    logger.info(
        "rebuild ok bookSlug=%s pageId=%s src=%s dst=%s size=%d durationMs=%d",
        bookSlug,
        pageId,
        content_md,
        publish_json,
        size,
        duration_ms,
    )

    return {
        "ok": True,
        "bookSlug": bookSlug,
        "pageId": pageId,
        "jsonPath": rel_json_path,
        "size": size,
        "regeneratedAt": datetime.utcnow().isoformat(),
    }


# ===== HELPER: Construct page file key =====
def _build_page_key(doc_id: str, page_num: int) -> str:
    """
    Construct page key: docId="medinsky11klass", pageNum=6 → "medinsky11klass_page_006"
    """
    return f"{doc_id}_page_{str(page_num).zfill(3)}"


def _validate_doc_id(doc_id: str) -> bool:
    """Validate docId: alphanumeric, underscore, hyphen"""
    return bool(re.fullmatch(r"[a-z0-9_-]+", doc_id or ""))


def _validate_page_num(page_num) -> bool:
    """Validate page number: 1-999"""
    try:
        p = int(page_num)
        return 1 <= p <= 999
    except (TypeError, ValueError):
        return False


# ===== NEW ENDPOINTS =====

@app.get("/api/editor/{docId}/{pageNum}")
async def get_editor_page(docId: str, pageNum: str):
    """GET page data for editor"""
    if not _validate_doc_id(docId):
        raise HTTPException(status_code=400, detail="invalid docId")
    if not _validate_page_num(pageNum):
        raise HTTPException(status_code=400, detail="invalid pageNum")
    
    pageNum = int(pageNum)
    page_num_str = str(pageNum).zfill(3)
    page = storage.load_page(docId, page_num_str)
    
    if not page.get("serverPageSha"):
        page_sha = storage.compute_sha(page)
        page["serverPageSha"] = page_sha
        try:
            storage.save_page(docId, page_num_str, page)
        except Exception:
            logger.exception("failed to persist serverPageSha docId=%s pageNum=%d", docId, pageNum)
    
    size = _serialize_size(page)
    anns = page.get("annotations")
    ann_count = len(anns) if isinstance(anns, list) else 0
    logger.info("GET editor docId=%s pageNum=%d anns=%d size=%d", docId, pageNum, ann_count, size)
    return page


@app.post("/api/editor/{docId}/{pageNum}")
async def post_editor_annotation(docId: str, pageNum: str, request: Request, user: Dict[str, str] = Depends(require_csrf)):
    """POST new annotation"""
    logger.info("POST editor START docId=%s pageNum=%s", docId, pageNum)
    
    if not _validate_doc_id(docId):
        logger.warning("POST editor invalid docId=%s", docId)
        raise HTTPException(status_code=400, detail="invalid docId")
    if not _validate_page_num(pageNum):
        logger.warning("POST editor invalid pageNum=%s", pageNum)
        raise HTTPException(status_code=400, detail="invalid pageNum")
    
    try:
        body = await request.json()
        logger.debug("POST editor body=%s", body)
    except Exception as e:
        logger.error("POST editor failed to parse JSON: %s", str(e))
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    try:
        ann = _parse_annotation_body(body if isinstance(body, dict) else {})
    except HTTPException:
        raise
    except Exception as e:
        logger.error("POST editor failed to parse annotation: %s", str(e))
        raise HTTPException(status_code=400, detail="invalid annotation")
    
    logger.debug("POST editor parsed annotation=%s", ann)

    if not ann.get("id"):
        ann["id"] = f"srv-{int(time.time())}-{uuid4().hex[:6]}"
        logger.debug("POST editor generated id=%s", ann["id"])

    pageNum_int = int(pageNum)
    page_num_str = str(pageNum_int).zfill(3)
    logger.debug("POST editor docId=%s page_num=%s", docId, page_num_str)
    
    try:
        page = storage.load_page(docId, page_num_str)
        logger.debug("POST editor loaded page with %d existing annotations", len(page.get("annotations", [])))
        
        storage.upsert_annotation(page, ann)
        logger.debug("POST editor upserted annotation id=%s", ann["id"])
        
        sha = storage.save_page(docId, page_num_str, page)
        logger.debug("POST editor saved page with sha=%s", sha)
    except Exception as e:
        logger.exception("POST editor failed to save page docId=%s pageNum=%d: %s", docId, pageNum_int, str(e))
        raise HTTPException(status_code=500, detail="failed to save page")

    anns = page.get("annotations")
    ann_count = len(anns) if isinstance(anns, list) else 0
    size = _serialize_size(page)
    
    logger.info("POST editor SUCCESS docId=%s pageNum=%d annId=%s anns=%d size=%d", 
                docId, pageNum_int, ann["id"], ann_count, size)
    
    return {"id": ann["id"], "serverPageSha": sha}


@app.put("/api/editor/{docId}/{pageNum}/{annId}")
async def put_editor_annotation(docId: str, pageNum: str, annId: str, request: Request, user: Dict[str, str] = Depends(require_csrf)):
    """PUT update annotation"""
    logger.info("PUT editor START docId=%s pageNum=%s annId=%s", docId, pageNum, annId)
    
    if not _validate_doc_id(docId):
        logger.warning("PUT editor invalid docId=%s", docId)
        raise HTTPException(status_code=400, detail="invalid docId")
    if not _validate_page_num(pageNum):
        logger.warning("PUT editor invalid pageNum=%s", pageNum)
        raise HTTPException(status_code=400, detail="invalid pageNum")
    
    try:
        body = await request.json()
        logger.debug("PUT editor body=%s", body)
    except Exception as e:
        logger.error("PUT editor failed to parse JSON: %s", str(e))
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    try:
        parsed = _parse_annotation_body(body if isinstance(body, dict) else {})
    except HTTPException:
        raise
    except Exception as e:
        logger.error("PUT editor failed to parse annotation: %s", str(e))
        raise HTTPException(status_code=400, detail="invalid annotation")
    
    parsed["id"] = annId
    logger.debug("PUT editor parsed annotation with id=%s", annId)

    pageNum_int = int(pageNum)
    page_num_str = str(pageNum_int).zfill(3)
    logger.debug("PUT editor docId=%s page_num=%s", docId, page_num_str)
    
    try:
        page = storage.load_page(docId, page_num_str)
        logger.debug("PUT editor loaded page with %d existing annotations", len(page.get("annotations", [])))
        
        updated = storage.update_annotation(page, annId, parsed)
        if not updated:
            logger.debug("PUT editor annotation id=%s not found, upserting as new", annId)
            storage.upsert_annotation(page, parsed)
        else:
            logger.debug("PUT editor updated existing annotation id=%s", annId)
        
        sha = storage.save_page(docId, page_num_str, page)
        logger.debug("PUT editor saved page with sha=%s", sha)
    except Exception as e:
        logger.exception("PUT editor failed to save page docId=%s pageNum=%d annId=%s: %s", 
                         docId, pageNum_int, annId, str(e))
        raise HTTPException(status_code=500, detail="failed to save page")

    anns = page.get("annotations")
    ann_count = len(anns) if isinstance(anns, list) else 0
    size = _serialize_size(page)
    
    logger.info("PUT editor SUCCESS docId=%s pageNum=%d annId=%s anns=%d size=%d", 
                docId, pageNum_int, annId, ann_count, size)

    return {"id": annId, "serverPageSha": sha}


# Allow running with `python main.py` for local dev

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=False, workers=1, proxy_headers=True)
