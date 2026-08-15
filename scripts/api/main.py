import json
import logging
import sys
import time
import secrets
from uuid import uuid4
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

import os
import re

# Optional .env loading
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

import config
import db
import publisher
import storage


def _set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        "redpen_session",
        session_id,
        httponly=True,
        samesite="lax",
        secure=config.COOKIE_SECURE,
        max_age=db.SESSION_TTL_SECONDS,
    )


def verify_google_token(credential: str) -> Dict[str, Any]:
    """Verify a Google ID-token (JWT) and return its claims. Wrapped in its
    own function so tests can monkeypatch it instead of hitting the network."""
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests

    return id_token.verify_oauth2_token(credential, google_requests.Request(), config.GOOGLE_CLIENT_ID)


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

# CORS configuration
#
# "*" + allow_credentials=True is an invalid combination (and browsers reject
# it): wildcard origins can't carry cookies, so only allow credentials when a
# real, explicit origin list is configured.

allow_origins, allow_credentials = config.cors_settings(config.CORS_ALLOW_ORIGINS or ["*"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=allow_credentials,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)

# Setup Jinja2 templates
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
os.makedirs(TEMPLATES_DIR, exist_ok=True)
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@app.on_event("startup")
async def on_startup() -> None:
    db.init_db()
    logger.info("service started LOG_LEVEL=%s storage_dir=%s db_path=%s", config.LOG_LEVEL, config.STORAGE_DIR, config.DB_PATH)

    # Self-heal the published static volume (e.g. after a fresh/recreated
    # container): republish everything already in the DB. Never block startup.
    if config.PUBLISH_DIR:
        try:
            result = publisher.publish_all()
            logger.info("startup publish_all pages=%d failed=%d", result["pages"], result["failed"])
        except Exception:
            logger.exception("startup publish_all failed")


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


async def require_user(request: Request) -> Dict[str, Any]:
    """FastAPI dependency: return the session+user data or raise 401."""
    session_id = request.cookies.get("redpen_session")
    result = db.get_session(session_id) if session_id else None
    if not result:
        raise HTTPException(status_code=401, detail="not authenticated")
    session, user = result
    return {
        "sessionId": session["id"],
        "csrf": session["csrf"],
        "userId": user["id"],
        "email": user["email"],
        "name": user["name"],
        "pictureUrl": user["pictureUrl"],
        "role": user["role"],
        # username: kept for the token-login/legacy frontend shape.
        "username": user["name"] or user["email"] or str(user["id"]),
    }


async def get_optional_user(request: Request) -> Optional[Dict[str, Any]]:
    """Like require_user, but returns None instead of raising 401 -- used by
    endpoints that behave differently for anonymous/viewer vs editor/admin
    without requiring a session (e.g. GET /api/editor/{docId}/{pageNum})."""
    session_id = request.cookies.get("redpen_session")
    result = db.get_session(session_id) if session_id else None
    if not result:
        return None
    session, user = result
    return {
        "sessionId": session["id"],
        "csrf": session["csrf"],
        "userId": user["id"],
        "email": user["email"],
        "name": user["name"],
        "pictureUrl": user["pictureUrl"],
        "role": user["role"],
        "username": user["name"] or user["email"] or str(user["id"]),
    }


async def require_csrf(request: Request, user: Dict[str, str] = Depends(require_user)) -> Dict[str, str]:
    """FastAPI dependency: verify X-CSRF-Token against the session-bound token."""
    header_token = request.headers.get("X-CSRF-Token")
    session_csrf = user.get("csrf")
    if not header_token or not session_csrf or not secrets.compare_digest(header_token, session_csrf):
        raise HTTPException(status_code=403, detail="invalid csrf token")
    return user


async def require_editor(user: Dict[str, Any] = Depends(require_csrf)) -> Dict[str, Any]:
    """FastAPI dependency: authenticated + CSRF-checked + editor/admin role."""
    if user.get("role") not in ("editor", "admin"):
        raise HTTPException(status_code=403, detail="editor role required")
    return user


async def require_admin(user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    """FastAPI dependency: authenticated + admin role (no CSRF; GET-only usage)."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return user


async def require_admin_csrf(user: Dict[str, Any] = Depends(require_csrf)) -> Dict[str, Any]:
    """FastAPI dependency: authenticated + CSRF-checked + admin role."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return user


def _check_optimistic_lock(body: Dict[str, Any], server_sha: str, docId: str, pageKey: str) -> Optional[Response]:
    """
    Compare the client's clientPageSha against the page's current
    serverPageSha (computed from the DB state). Returns a 409 Response if
    they conflict, else None. Missing clientPageSha is accepted
    (transitional) but logged.
    """
    client_sha = body.get("clientPageSha") if isinstance(body, dict) else None
    if not isinstance(client_sha, str) or not client_sha.strip():
        logger.warning("docId=%s pageKey=%s: clientPageSha missing (transitional)", docId, pageKey)
        return None

    if server_sha and client_sha != server_sha:
        logger.info("docId=%s pageKey=%s: optimistic lock conflict", docId, pageKey)
        return JSONResponse(status_code=409, content={"detail": "conflict", "serverPageSha": server_sha})
    return None


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

    if "status" in body:
        status = body["status"]
        if status not in ("draft", "published"):
            raise HTTPException(status_code=400, detail="status must be 'draft' or 'published'")
        ann["status"] = status

    # Absent "tags" means "leave them alone" (see _resolve_tags); [] clears them.
    if "tags" in body:
        try:
            ann["tags"] = db.normalize_tags(body["tags"])
        except db.TagError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return ann


# ===== LOG VIEWER ENDPOINTS =====

@app.get("/logs")
async def logs_page(request: Request, user: Dict[str, str] = Depends(require_admin)):
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
async def get_logs_json(lines: int = 100, user: Dict[str, str] = Depends(require_admin)):
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

    # Dev-fallback: token users always get the editor role (see db.get_or_create_user_token).
    user = db.get_or_create_user_token(username)
    session_id = db.create_session(user["id"])
    _set_session_cookie(response, session_id)

    logger.info("login: success username=%s userId=%s", username, user["id"])
    return {"userId": user["id"], "username": username}


@app.get("/api/auth/csrf")
async def get_csrf(user: Dict[str, Any] = Depends(require_user)):
    """Issue a CSRF token bound to the current session (requires login)"""
    csrf_token = f"csrf-{secrets.token_hex(16)}"
    db.set_session_csrf(user["sessionId"], csrf_token)
    logger.info("csrf: token issued length=%d", len(csrf_token))
    return {"csrfToken": csrf_token}


@app.get("/api/auth/me")
async def get_me(user: Dict[str, Any] = Depends(require_user)):
    """Return current user info from session"""
    logger.info("auth/me: success username=%s role=%s", user["username"], user["role"])
    return {
        "userId": user["userId"],
        "email": user["email"],
        "name": user["name"],
        "picture": user["pictureUrl"],
        "role": user["role"],
        # username: kept for the existing frontend shape (token + Google logins alike).
        "username": user["username"],
    }


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    """Delete the current session and clear its cookie"""
    session_id = request.cookies.get("redpen_session")
    if session_id:
        db.delete_session(session_id)
    response.delete_cookie("redpen_session")
    return {"ok": True}


@app.post("/api/auth/google")
async def auth_google(request: Request, response: Response):
    """Verify a Google ID-token (GIS) and create a session for the user"""
    if not config.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail="google auth is not configured")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    credential = body.get("credential") if isinstance(body, dict) else None
    if not isinstance(credential, str) or not credential.strip():
        raise HTTPException(status_code=400, detail="credential is required")

    try:
        claims = verify_google_token(credential)
    except Exception as e:
        logger.warning("google auth: token verification failed: %s", type(e).__name__)
        raise HTTPException(status_code=401, detail="invalid credential")

    if not claims.get("email_verified"):
        logger.warning("google auth: email not verified")
        raise HTTPException(status_code=401, detail="email not verified")

    sub = claims.get("sub")
    email = claims.get("email")
    if not sub or not email:
        raise HTTPException(status_code=401, detail="invalid credential")
    name = claims.get("name") or email.split("@")[0]
    picture = claims.get("picture")

    user = db.get_or_create_user_google(sub, email, name, picture)
    session_id = db.create_session(user["id"])
    _set_session_cookie(response, session_id)

    logger.info("google auth: success userId=%s role=%s", user["id"], user["role"])
    return {
        "userId": user["id"],
        "email": user["email"],
        "name": user["name"],
        "picture": user["pictureUrl"],
        "role": user["role"],
    }


# ===== ADMIN: EDITOR ALLOWLIST =====

@app.get("/api/admin/allowlist")
async def get_allowlist(user: Dict[str, Any] = Depends(require_admin)):
    return {"allowlist": db.list_allowlist()}


@app.post("/api/admin/allowlist")
async def upsert_allowlist_entry(request: Request, user: Dict[str, Any] = Depends(require_admin_csrf)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    email = body.get("email") if isinstance(body, dict) else None
    role = body.get("role") if isinstance(body, dict) else None
    if not isinstance(email, str) or not email.strip():
        raise HTTPException(status_code=400, detail="email is required")
    if role not in ("editor", "admin"):
        raise HTTPException(status_code=400, detail="role must be 'editor' or 'admin'")

    db.upsert_allowlist(email.strip(), role, user.get("email"))
    logger.info("admin: allowlist upsert email=%s role=%s by=%s", email, role, user.get("email"))
    return {"allowlist": db.list_allowlist()}


@app.delete("/api/admin/allowlist/{email}")
async def delete_allowlist_entry(email: str, user: Dict[str, Any] = Depends(require_admin_csrf)):
    removed = db.delete_allowlist(email)
    if not removed:
        raise HTTPException(status_code=404, detail="not found")
    logger.info("admin: allowlist delete email=%s by=%s", email, user.get("email"))
    return {"allowlist": db.list_allowlist()}


@app.post("/api/admin/publish-all")
async def admin_publish_all(user: Dict[str, Any] = Depends(require_admin_csrf)):
    """Republish every page in the DB to PUBLISH_DIR (volume self-heal / manual repair)."""
    result = publisher.publish_all()
    logger.info("admin: publish-all pages=%d failed=%d by=%s", result["pages"], result["failed"], user.get("email"))
    return result


@app.get("/api/hello")
async def hello():
    # Minimal Hello endpoint for local smoke tests
    now = datetime.now().isoformat()
    version = "local-dev"
    return {"message": "Hello, RedPen!", "version": version, "now": now}


@app.post("/api/store-raw")
async def store_raw(request: Request, user: Dict[str, str] = Depends(require_editor)):
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
async def store(request: Request, user: Dict[str, str] = Depends(require_editor)):
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
    """GET page data by pageId (legacy endpoint, for backwards compatibility).
    Rendered from the SQLite annotations store (stage 2); imageUrl/origW/origH
    were never populated by this endpoint and remain placeholders."""
    # pageId format: "medinsky11klass_page_006"
    parts = pageId.rsplit("_page_", 1)
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="invalid pageId format")

    doc_id, page_num_raw = parts
    page_num = _validate_page_key(page_num_raw)
    if page_num is None:
        raise HTTPException(status_code=400, detail="invalid pageId format")
    rendered = publisher.render_page(doc_id, page_num)
    sha = publisher.compute_page_sha(rendered)

    logger.info("GET pageId=%s anns=%d", pageId, len(rendered))
    return {
        "pageId": pageId,
        "imageUrl": "",
        "origW": 0,
        "origH": 0,
        "serverPageSha": sha,
        "annotations": rendered,
    }


def _validate_doc_id(doc_id: str) -> bool:
    """Validate docId: alphanumeric, underscore, hyphen"""
    return bool(re.fullmatch(r"[a-z0-9_-]+", doc_id or ""))


def _validate_page_key(key: str) -> Optional[str]:
    """
    Validate and normalize a page file key (docs/page-addressing-proposal.md
    B.4): accepts ^-?\\d{1,3}$ -- "6", "006", "000", "-1", "-01" -- and
    returns the normalized key matching the page_<key>.json filename:
    non-negative -> zfill(3) ("6"/"006" -> "006"); negative -> "-" +
    zfill(2) of the absolute value ("-1"/"-01" -> "-01"). Invalid input
    (wrong shape, out of range) -> None, caller responds 400.
    """
    if not isinstance(key, str) or not re.fullmatch(r"-?\d{1,3}", key):
        return None
    n = int(key)
    return f"-{abs(n):02d}" if n < 0 else f"{n:03d}"


# ===== NEW ENDPOINTS =====

def _current_page_sha(docId: str, page_num_str: str) -> str:
    return publisher.compute_page_sha(publisher.render_page(docId, page_num_str))


def _resolve_status(parsed: Dict[str, Any], existing: Optional[Dict[str, Any]]) -> str:
    """If the client sent an explicit status, use it. Otherwise preserve the
    existing annotation's status (a PUT without status must not silently
    publish a draft); brand-new annotations default to published."""
    if "status" in parsed:
        return parsed["status"]
    if existing is not None:
        return existing["status"]
    return "published"


def _resolve_tags(parsed: Dict[str, Any]) -> Optional[List[str]]:
    """None ("tags" absent) tells upsert_annotation_db to leave the tag set
    alone -- the editor doesn't send tags yet and must not wipe them. An
    explicit [] clears them."""
    return parsed.get("tags") if "tags" in parsed else None


@app.get("/api/editor/{docId}/{pageNum}")
async def get_editor_page(docId: str, pageNum: str, user: Optional[Dict[str, Any]] = Depends(get_optional_user)):
    """GET page data for editor, rendered from the SQLite annotations store.
    Anonymous/viewer callers see only published annotations; editor/admin
    additionally see drafts, flagged draft=true.

    Note the drafts are flagged with the boolean, NOT with a "draft" entry in
    "tags": the editor echoes the fields it received back in its PUT, and
    "draft" is a reserved tag name that would come back as a 400. The
    status->tag mirroring belongs to the static render only."""
    if not _validate_doc_id(docId):
        raise HTTPException(status_code=400, detail="invalid docId")
    page_num_str = _validate_page_key(pageNum)
    if page_num_str is None:
        raise HTTPException(status_code=400, detail="invalid pageNum")

    rendered = publisher.render_page(docId, page_num_str)
    sha = publisher.compute_page_sha(rendered)
    tags_by_id = {ann["annId"]: ann["tags"] for ann in db.list_page_annotations(docId, page_num_str)}
    annotations = []
    for item in rendered:
        item = dict(item)
        if tags_by_id.get(item["id"]):
            item["tags"] = tags_by_id[item["id"]]
        annotations.append(item)

    if user is not None and user.get("role") in ("editor", "admin"):
        for ann in db.list_annotations(doc_id=docId, page_num=page_num_str, status="draft", limit=1000):
            item: Dict[str, Any] = {"id": ann["annId"], "text": ann["text"], "annType": ann["annType"], "draft": True}
            if ann["coordX"] is not None and ann["coordY"] is not None:
                item["coords"] = [ann["coordX"], ann["coordY"]]
            if ann["tags"]:
                item["tags"] = ann["tags"]
            annotations.append(item)

    logger.info("GET editor docId=%s pageKey=%s anns=%d", docId, page_num_str, len(annotations))
    return {
        "pageId": f"{docId}_page_{page_num_str}",
        "serverPageSha": sha,
        "annotations": annotations,
    }


@app.post("/api/editor/{docId}/{pageNum}")
async def post_editor_annotation(docId: str, pageNum: str, request: Request, user: Dict[str, Any] = Depends(require_editor)):
    """POST new annotation: upserts into the DB, records history, republishes."""
    if not _validate_doc_id(docId):
        raise HTTPException(status_code=400, detail="invalid docId")
    page_num_str = _validate_page_key(pageNum)
    if page_num_str is None:
        raise HTTPException(status_code=400, detail="invalid pageNum")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    ann = _parse_annotation_body(body if isinstance(body, dict) else {})
    if not ann.get("id"):
        ann["id"] = f"srv-{int(time.time())}-{uuid4().hex[:6]}"

    conflict = _check_optimistic_lock(
        body if isinstance(body, dict) else {}, _current_page_sha(docId, page_num_str), docId, page_num_str
    )
    if conflict is not None:
        return conflict

    coords = ann.get("coords")
    coord_x, coord_y = (coords[0], coords[1]) if coords else (None, None)
    existing = db.get_annotation(docId, page_num_str, ann["id"])
    action = "update" if existing else "create"
    status = _resolve_status(ann, existing)

    db.upsert_annotation_db(
        docId, page_num_str, ann["id"], ann["annType"], ann["text"],
        coord_x=coord_x, coord_y=coord_y, status=status, author_id=user["userId"], action=action,
        tags=_resolve_tags(ann),
    )
    write_ok = publisher.publish_page(docId, page_num_str)
    published = write_ok and status == "published"
    new_sha = _current_page_sha(docId, page_num_str)

    logger.info(
        "POST editor SUCCESS docId=%s pageKey=%s annId=%s status=%s published=%s",
        docId, page_num_str, ann["id"], status, published,
    )
    return {"id": ann["id"], "serverPageSha": new_sha, "published": published}


@app.put("/api/editor/{docId}/{pageNum}/{annId}")
async def put_editor_annotation(docId: str, pageNum: str, annId: str, request: Request, user: Dict[str, Any] = Depends(require_editor)):
    """PUT update (or create, if annId doesn't exist yet) an annotation."""
    if not _validate_doc_id(docId):
        raise HTTPException(status_code=400, detail="invalid docId")
    page_num_str = _validate_page_key(pageNum)
    if page_num_str is None:
        raise HTTPException(status_code=400, detail="invalid pageNum")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    parsed = _parse_annotation_body(body if isinstance(body, dict) else {})
    parsed["id"] = annId

    conflict = _check_optimistic_lock(
        body if isinstance(body, dict) else {}, _current_page_sha(docId, page_num_str), docId, page_num_str
    )
    if conflict is not None:
        return conflict

    coords = parsed.get("coords")
    coord_x, coord_y = (coords[0], coords[1]) if coords else (None, None)
    existing = db.get_annotation(docId, page_num_str, annId)
    action = "update" if existing else "create"
    status = _resolve_status(parsed, existing)

    db.upsert_annotation_db(
        docId, page_num_str, annId, parsed["annType"], parsed["text"],
        coord_x=coord_x, coord_y=coord_y, status=status, author_id=user["userId"], action=action,
        tags=_resolve_tags(parsed),
    )
    write_ok = publisher.publish_page(docId, page_num_str)
    published = write_ok and status == "published"
    new_sha = _current_page_sha(docId, page_num_str)

    logger.info(
        "PUT editor SUCCESS docId=%s pageKey=%s annId=%s status=%s published=%s",
        docId, page_num_str, annId, status, published,
    )
    return {"id": annId, "serverPageSha": new_sha, "published": published}


@app.delete("/api/editor/{docId}/{pageNum}/{annId}")
async def delete_editor_annotation(docId: str, pageNum: str, annId: str, user: Dict[str, Any] = Depends(require_editor)):
    """Soft-delete an annotation and republish the page."""
    if not _validate_doc_id(docId):
        raise HTTPException(status_code=400, detail="invalid docId")
    page_num_str = _validate_page_key(pageNum)
    if page_num_str is None:
        raise HTTPException(status_code=400, detail="invalid pageNum")

    deleted = db.soft_delete_annotation(docId, page_num_str, annId, author_id=user["userId"])
    if not deleted:
        raise HTTPException(status_code=404, detail="annotation not found")

    published = publisher.publish_page(docId, page_num_str)
    new_sha = _current_page_sha(docId, page_num_str)

    logger.info(
        "DELETE editor SUCCESS docId=%s pageKey=%s annId=%s published=%s",
        docId, page_num_str, annId, published,
    )
    return {"id": annId, "serverPageSha": new_sha, "published": published}


# ===== CABINET (stage 3) =====

ANNOTATION_STATUSES = ("published", "draft", "deleted")
ANNOTATION_TYPES = ("main", "comment", "general")


async def require_editor_read(user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    """Editor/admin role, no CSRF (GET-only cabinet list endpoints)."""
    if user.get("role") not in ("editor", "admin"):
        raise HTTPException(status_code=403, detail="editor role required")
    return user


def _validate_list_params(
    docId: Optional[str], pageKey: Optional[str], annType: Optional[str],
    status: Optional[str], limit: int, offset: int, q: Optional[str],
) -> Dict[str, Any]:
    if docId is not None and not _validate_doc_id(docId):
        raise HTTPException(status_code=400, detail="invalid docId")
    page_num_str = None
    if pageKey is not None:
        page_num_str = _validate_page_key(pageKey)
        if page_num_str is None:
            raise HTTPException(status_code=400, detail="invalid pageKey")
    if status is not None and status not in ANNOTATION_STATUSES:
        raise HTTPException(status_code=400, detail="invalid status")
    if annType is not None and annType not in ANNOTATION_TYPES:
        raise HTTPException(status_code=400, detail="invalid annType")
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")
    if q is not None and len(q) > 200:
        raise HTTPException(status_code=400, detail="q must be at most 200 characters")
    return {"pageKey": page_num_str}


@app.get("/api/annotations")
async def list_annotations(
    docId: Optional[str] = None,
    pageKey: Optional[str] = None,
    annType: Optional[str] = None,
    status: Optional[str] = None,
    authorId: Optional[int] = None,
    q: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    user: Dict[str, Any] = Depends(require_editor_read),
):
    validated = _validate_list_params(docId, pageKey, annType, status, limit, offset, q)
    if tag is not None:
        try:
            tag = db.normalize_tag(tag)
        except db.TagError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    items = db.list_annotations(
        doc_id=docId, page_num=validated["pageKey"], ann_type=annType, status=status,
        author_id=authorId, q=q, limit=limit, offset=offset, tag=tag,
    )
    total = db.count_annotations(
        doc_id=docId, page_num=validated["pageKey"], ann_type=annType, status=status,
        author_id=authorId, q=q, tag=tag,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.get("/api/tags")
async def get_tags(docId: Optional[str] = None, user: Dict[str, Any] = Depends(require_editor_read)):
    """Tag vocabulary in use, with counts -- populates the cabinet's filter."""
    if docId is not None and not _validate_doc_id(docId):
        raise HTTPException(status_code=400, detail="invalid docId")
    return {"tags": db.list_all_tags(docId)}


@app.get("/api/history")
async def list_history(
    docId: Optional[str] = None,
    pageKey: Optional[str] = None,
    annId: Optional[str] = None,
    authorId: Optional[int] = None,
    action: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    user: Dict[str, Any] = Depends(require_editor_read),
):
    validated = _validate_list_params(docId, pageKey, None, None, limit, offset, None)
    items = db.list_history(
        doc_id=docId, page_num=validated["pageKey"], ann_id=annId, author_id=authorId,
        action=action, limit=limit, offset=offset,
    )
    return {"items": items, "hasMore": len(items) == limit, "limit": limit, "offset": offset}


@app.get("/api/stats")
async def get_stats(user: Dict[str, Any] = Depends(require_user)):
    return db.get_stats()


@app.post("/api/history/{histId}/revert")
async def revert_history(histId: int, user: Dict[str, Any] = Depends(require_editor)):
    """Restore an annotation to the exact state recorded in a history
    snapshot (including that snapshot's own status -- reverting to a
    delete-record re-deletes, which is intentional: the cabinet shows every
    record's action and lets the user pick the state they want back)."""
    record = db.get_history_record(histId)
    if record is None:
        raise HTTPException(status_code=404, detail="history record not found")

    snapshot = record["snapshot"] or {}
    doc_id = record["docId"]
    page_num = record["pageNum"]
    ann_id = record["annId"]
    coords = None
    if snapshot.get("coordX") is not None and snapshot.get("coordY") is not None:
        coords = [snapshot["coordX"], snapshot["coordY"]]

    db.upsert_annotation_db(
        doc_id, page_num, ann_id, snapshot.get("annType"), snapshot.get("text"),
        coord_x=coords[0] if coords else None, coord_y=coords[1] if coords else None,
        status=snapshot.get("status", "published"), author_id=user["userId"], action="revert",
        # Only snapshots taken after tags existed carry the key; older ones must
        # leave the current tag set alone rather than clear it.
        tags=snapshot["tags"] if "tags" in snapshot else None,
    )
    published = publisher.publish_page(doc_id, page_num)
    new_sha = _current_page_sha(doc_id, page_num)

    logger.info(
        "revert histId=%s docId=%s pageKey=%s annId=%s by=%s",
        histId, doc_id, page_num, ann_id, user.get("email"),
    )
    return {"annId": ann_id, "docId": doc_id, "pageNum": page_num, "serverPageSha": new_sha, "published": published}


@app.get("/api/admin/users")
async def get_admin_users(user: Dict[str, Any] = Depends(require_admin)):
    return {"users": db.list_users()}


# Allow running with `python main.py` for local dev

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=False, workers=1, proxy_headers=True)
