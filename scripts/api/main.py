import json
import logging
import sys
import time
import secrets
from uuid import uuid4
from datetime import datetime
from typing import Any, Dict, Optional

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


def _check_optimistic_lock(body: Dict[str, Any], server_sha: str, docId: str, pageNum: int) -> Optional[Response]:
    """
    Compare the client's clientPageSha against the page's current
    serverPageSha (computed from the DB state). Returns a 409 Response if
    they conflict, else None. Missing clientPageSha is accepted
    (transitional) but logged.
    """
    client_sha = body.get("clientPageSha") if isinstance(body, dict) else None
    if not isinstance(client_sha, str) or not client_sha.strip():
        logger.warning("docId=%s pageNum=%d: clientPageSha missing (transitional)", docId, pageNum)
        return None

    if server_sha and client_sha != server_sha:
        logger.info("docId=%s pageNum=%d: optimistic lock conflict", docId, pageNum)
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

    doc_id, page_num = parts
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

def _current_page_sha(docId: str, page_num_str: str) -> str:
    return publisher.compute_page_sha(publisher.render_page(docId, page_num_str))


@app.get("/api/editor/{docId}/{pageNum}")
async def get_editor_page(docId: str, pageNum: str):
    """GET page data for editor, rendered from the SQLite annotations store."""
    if not _validate_doc_id(docId):
        raise HTTPException(status_code=400, detail="invalid docId")
    if not _validate_page_num(pageNum):
        raise HTTPException(status_code=400, detail="invalid pageNum")

    pageNum_int = int(pageNum)
    page_num_str = str(pageNum_int).zfill(3)
    rendered = publisher.render_page(docId, page_num_str)
    sha = publisher.compute_page_sha(rendered)

    logger.info("GET editor docId=%s pageNum=%d anns=%d", docId, pageNum_int, len(rendered))
    return {
        "pageId": f"{docId}_page_{page_num_str}",
        "serverPageSha": sha,
        "annotations": rendered,
    }


@app.post("/api/editor/{docId}/{pageNum}")
async def post_editor_annotation(docId: str, pageNum: str, request: Request, user: Dict[str, Any] = Depends(require_editor)):
    """POST new annotation: upserts into the DB, records history, republishes."""
    if not _validate_doc_id(docId):
        raise HTTPException(status_code=400, detail="invalid docId")
    if not _validate_page_num(pageNum):
        raise HTTPException(status_code=400, detail="invalid pageNum")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    ann = _parse_annotation_body(body if isinstance(body, dict) else {})
    if not ann.get("id"):
        ann["id"] = f"srv-{int(time.time())}-{uuid4().hex[:6]}"

    pageNum_int = int(pageNum)
    page_num_str = str(pageNum_int).zfill(3)

    conflict = _check_optimistic_lock(
        body if isinstance(body, dict) else {}, _current_page_sha(docId, page_num_str), docId, pageNum_int
    )
    if conflict is not None:
        return conflict

    coords = ann.get("coords")
    coord_x, coord_y = (coords[0], coords[1]) if coords else (None, None)
    action = "update" if db.get_annotation(docId, page_num_str, ann["id"]) else "create"

    db.upsert_annotation_db(
        docId, page_num_str, ann["id"], ann["annType"], ann["text"],
        coord_x=coord_x, coord_y=coord_y, author_id=user["userId"], action=action,
    )
    published = publisher.publish_page(docId, page_num_str)
    new_sha = _current_page_sha(docId, page_num_str)

    logger.info(
        "POST editor SUCCESS docId=%s pageNum=%d annId=%s published=%s",
        docId, pageNum_int, ann["id"], published,
    )
    return {"id": ann["id"], "serverPageSha": new_sha, "published": published}


@app.put("/api/editor/{docId}/{pageNum}/{annId}")
async def put_editor_annotation(docId: str, pageNum: str, annId: str, request: Request, user: Dict[str, Any] = Depends(require_editor)):
    """PUT update (or create, if annId doesn't exist yet) an annotation."""
    if not _validate_doc_id(docId):
        raise HTTPException(status_code=400, detail="invalid docId")
    if not _validate_page_num(pageNum):
        raise HTTPException(status_code=400, detail="invalid pageNum")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    parsed = _parse_annotation_body(body if isinstance(body, dict) else {})
    parsed["id"] = annId

    pageNum_int = int(pageNum)
    page_num_str = str(pageNum_int).zfill(3)

    conflict = _check_optimistic_lock(
        body if isinstance(body, dict) else {}, _current_page_sha(docId, page_num_str), docId, pageNum_int
    )
    if conflict is not None:
        return conflict

    coords = parsed.get("coords")
    coord_x, coord_y = (coords[0], coords[1]) if coords else (None, None)
    action = "update" if db.get_annotation(docId, page_num_str, annId) else "create"

    db.upsert_annotation_db(
        docId, page_num_str, annId, parsed["annType"], parsed["text"],
        coord_x=coord_x, coord_y=coord_y, author_id=user["userId"], action=action,
    )
    published = publisher.publish_page(docId, page_num_str)
    new_sha = _current_page_sha(docId, page_num_str)

    logger.info(
        "PUT editor SUCCESS docId=%s pageNum=%d annId=%s published=%s",
        docId, pageNum_int, annId, published,
    )
    return {"id": annId, "serverPageSha": new_sha, "published": published}


@app.delete("/api/editor/{docId}/{pageNum}/{annId}")
async def delete_editor_annotation(docId: str, pageNum: str, annId: str, user: Dict[str, Any] = Depends(require_editor)):
    """Soft-delete an annotation and republish the page."""
    if not _validate_doc_id(docId):
        raise HTTPException(status_code=400, detail="invalid docId")
    if not _validate_page_num(pageNum):
        raise HTTPException(status_code=400, detail="invalid pageNum")

    pageNum_int = int(pageNum)
    page_num_str = str(pageNum_int).zfill(3)

    deleted = db.soft_delete_annotation(docId, page_num_str, annId, author_id=user["userId"])
    if not deleted:
        raise HTTPException(status_code=404, detail="annotation not found")

    published = publisher.publish_page(docId, page_num_str)
    new_sha = _current_page_sha(docId, page_num_str)

    logger.info(
        "DELETE editor SUCCESS docId=%s pageNum=%d annId=%s published=%s",
        docId, pageNum_int, annId, published,
    )
    return {"id": annId, "serverPageSha": new_sha, "published": published}


# Allow running with `python main.py` for local dev

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=False, workers=1, proxy_headers=True)
