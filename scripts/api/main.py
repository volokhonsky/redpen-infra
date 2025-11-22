import json
import logging
import sys
import time
import secrets
from uuid import uuid4
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, Response
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

# Store for session tokens (in-memory for now)
_session_store: Dict[str, Dict[str, str]] = {}

# Hardcoded valid tokens for editor access
VALID_TOKENS = {
    "dev-token-123": "john_doe",
    "demo-token-456": "demo_user",
    "tokentoken":"user12"
}


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("redpen.api")
    if not logger.handlers:
        # Ensure logs directory exists
        logs_dir = "/app/logs"
        os.makedirs(logs_dir, exist_ok=True)
        
        # Console handler (stdout)
        console_handler = logging.StreamHandler(sys.stdout)
        console_fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%dT%H:%M:%S%z")
        console_handler.setFormatter(console_fmt)
        logger.addHandler(console_handler)
        
        # File handler (logs/redpen-api.log)
        file_handler = logging.FileHandler(os.path.join(logs_dir, "redpen-api.log"))
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

# Ensure no duplicates
if isinstance(allow_origins, list):
    allow_origins = list(dict.fromkeys(allow_origins))  # Remove duplicates, preserve order

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=False,
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
async def logs_page(request: Request):
    """Serve logs viewer page"""
    try:
        log_file = "/app/logs/redpen-api.log"
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
async def get_logs_json(lines: int = 100):
    """Return logs as JSON"""
    try:
        log_file = "/app/logs/redpen-api.log"
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
    username = VALID_TOKENS.get(token)
    if not username:
        logger.warning("login: invalid token (first 8 chars): %s...", token[:8])
        logger.debug("login: available tokens: %s", list(VALID_TOKENS.keys()))
        raise HTTPException(status_code=401, detail="invalid token")
    
    # Create session
    session_id = secrets.token_hex(16)
    user_id = f"user-{abs(hash(username)) % 100000}"
    
    _session_store[session_id] = {
        "userId": user_id,
        "username": username,
        "token": token
    }
    
    # Set session cookie
    response.set_cookie(
        "redpen_session",
        session_id,
        httponly=True,
        samesite="lax",
        max_age=86400 * 7  # 7 days
    )
    
    logger.info("login: success username=%s userId=%s sessionId=%s", username, user_id, session_id)
    return {"userId": user_id, "username": username}


@app.get("/api/auth/csrf")
async def get_csrf(request: Request, response: Response):
    """Issue CSRF token for POST requests"""
    csrf_token = f"csrf-{secrets.token_hex(16)}"
    response.set_cookie(
        "csrf_token", 
        csrf_token, 
        httponly=True, 
        samesite="lax"
    )
    logger.info("csrf: token issued csrfToken=%s", csrf_token[:16] + "...")
    return {"csrfToken": csrf_token}


@app.get("/api/auth/me")
async def get_me(request: Request):
    """Return current user info from session"""
    # Get session cookie
    session_id = request.cookies.get("redpen_session")
    
    logger.debug("auth/me: session_id from cookie=%s", session_id[:8] + "..." if session_id else "None")
    
    if not session_id or session_id not in _session_store:
        logger.warning("auth/me: invalid or missing session_id")
        raise HTTPException(status_code=401, detail="not authenticated")
    
    user_data = _session_store[session_id]
    logger.info("auth/me: success username=%s", user_data["username"])
    return {
        "userId": user_data["userId"],
        "username": user_data["username"]
    }


@app.get("/api/hello")
async def hello():
    # Minimal Hello endpoint for local smoke tests
    now = datetime.now().isoformat()
    version = "local-dev"
    return {"message": "Hello, RedPen!", "version": version, "now": now}


@app.post("/api/store-raw")
async def store_raw(request: Request):
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
        rel_path = storage.save_inbox(payload, config.STORAGE_DIR, bucket=bucket, filename=filename)
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
async def store(request: Request):
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
        rel_path = storage.save_inbox(payload, config.STORAGE_DIR)
    except Exception:
        logger.exception("failed to store incoming payload")
        raise HTTPException(status_code=500, detail="failed to store")

    logger.info("stored file=%s size=%d", rel_path, data_size)
    return {"status": "stored", "path": rel_path}

@app.get("/api/pages/{pageId}")
async def get_page(pageId: str):
    page = storage.load_page(config.STORAGE_DIR, pageId)
    if not page.get("serverPageSha"):
        # compute and persist once
        page_sha = storage.compute_sha(page)
        page["serverPageSha"] = page_sha
        try:
            storage.save_page(config.STORAGE_DIR, page)
        except Exception:
            logger.exception("failed to persist serverPageSha for pageId=%s", pageId)
            # still return calculated one in response
    size = _serialize_size(page)
    anns = page.get("annotations")
    ann_count = len(anns) if isinstance(anns, list) else 0
    logger.info("GET pageId=%s anns=%d size=%d", pageId, ann_count, size)
    return page


@app.post("/api/pages/{pageId}/annotations")
async def post_annotation(pageId: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    ann = _parse_annotation_body(body if isinstance(body, dict) else {})

    # Generate id if not provided
    if not ann.get("id"):
        ann["id"] = f"srv-{int(time.time())}-{uuid4().hex[:6]}"

    page = storage.load_page(config.STORAGE_DIR, pageId)
    storage.upsert_annotation(page, ann)

    try:
        sha = storage.save_page(config.STORAGE_DIR, page)
    except Exception:
        logger.exception("failed to save page after POST annotation pageId=%s", pageId)
        raise HTTPException(status_code=500, detail="failed to save page")

    # For response, return id and serverPageSha
    result = {"id": ann["id"], "serverPageSha": sha}

    # Logging
    anns = page.get("annotations")
    ann_count = len(anns) if isinstance(anns, list) else 0
    size = _serialize_size(page)
    logger.info("POST pageId=%s anns=%d size=%d", pageId, ann_count, size)
    return result


@app.put("/api/pages/{pageId}/annotations/{id}")
async def put_annotation(pageId: str, id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    parsed = _parse_annotation_body(body if isinstance(body, dict) else {})

    # Ensure the provided id in path is used
    parsed["id"] = id

    page = storage.load_page(config.STORAGE_DIR, pageId)
    updated = storage.update_annotation(page, id, parsed)
    if not updated:
        # If not found, append as new per permissive spec
        storage.upsert_annotation(page, parsed)

    try:
        sha = storage.save_page(config.STORAGE_DIR, page)
    except Exception:
        logger.exception("failed to save page after PUT annotation pageId=%s id=%s", pageId, id)
        raise HTTPException(status_code=500, detail="failed to save page")

    # Logging
    anns = page.get("annotations")
    ann_count = len(anns) if isinstance(anns, list) else 0
    size = _serialize_size(page)
    logger.info("PUT pageId=%s anns=%d size=%d", pageId, ann_count, size)

    return {"id": id, "serverPageSha": sha}


@app.post("/api/rebuild/{bookSlug}/annotations/{pageId}")
async def rebuild_annotation_page(bookSlug: str, pageId: str):
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


# Allow running with `python main.py` for local dev

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=False, workers=1, proxy_headers=True)

