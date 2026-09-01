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

# Общий модуль категорий лежит в scripts/, а на sys.path у контейнера только
# scripts/api (см. scripts/api/Dockerfile). Репозиторий скопирован целиком,
# поэтому каталог достаточно добавить руками.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import annotation_categories  # noqa: E402
import config
import db
import publisher
import rating_scales
import remark_actions
import ratelimit
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
    # PATCH обязателен: узкие операции редактора (status/category/tags) ходят
    # именно им, а браузер шлёт на них предварительный OPTIONS — без метода в
    # списке он получает 400 и запрос до сервера не доходит вовсе. Список
    # писался до появления этих операций и за ними не поехал.
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    # X-Survey-Token — опознание респондента опроса: он ходит с другого
    # источника (сайт), а токен вместо куки выбран как раз затем, чтобы
    # не заводить CSRF там, где её можно не заводить.
    allow_headers=["Content-Type", "X-CSRF-Token", "X-Survey-Token"],
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
        "kind": user["kind"],
        "displayName": user["displayName"],
        "role": user["role"],
        # username: подпись актора в интерфейсе. Псевдоним, а не имя из Google —
        # его там больше нет (docs/anonymity-model.md).
        "username": user["displayName"] or f"Участник №{user['id']}",
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
        "kind": user["kind"],
        "displayName": user["displayName"],
        "role": user["role"],
        "username": user["displayName"] or f"Участник №{user['id']}",
    }


async def require_csrf(request: Request, user: Dict[str, str] = Depends(require_user)) -> Dict[str, str]:
    """FastAPI dependency: verify X-CSRF-Token against the session-bound token."""
    header_token = request.headers.get("X-CSRF-Token")
    session_csrf = user.get("csrf")
    if not header_token or not session_csrf or not secrets.compare_digest(header_token, session_csrf):
        raise HTTPException(status_code=403, detail="invalid csrf token")
    return user


#: Лестница ролей: viewer < editor < admin.
#:
#: `reviewer` упразднён 2026-08-31 вместе со слиянием интерфейсов. Он обещал
#: отделить «принимать чужое» от «писать», но ни одна ветка кода этого не
#: делала: роль была равна editor в API и не пускалась вовсе в кабинете.
#: Существующие строки переводит `db._retire_reviewer_role`.
EDITOR_ROLES = ("editor", "admin")

MAX_SUMMARY_LENGTH = 200
REMARK_STATUSES = ("published", "draft", "deleted")
# "general" ушёл: см. docs/general-migration-map.json. Строки со status='deleted'
# могут по-прежнему иметь kind='general' — их никто не читает, кроме истории.
REMARK_KINDS = ("major", "minor")


async def require_editor(user: Dict[str, Any] = Depends(require_csrf)) -> Dict[str, Any]:
    """FastAPI dependency: authenticated + CSRF-checked + editor role or above."""
    if user.get("role") not in EDITOR_ROLES:
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


def _parse_remark_body(body: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    remark_kind = body.get("kind")
    text = body.get("text")
    coords = body.get("coords", None)

    if not isinstance(remark_kind, str) or remark_kind.strip() == "":
        raise HTTPException(status_code=400, detail="kind must be a string")
    # "general" (общее замечание к странице) is retired: it had no anchor on
    # the scan, and with per-page addresses every comment needs one. Rejected
    # rather than silently accepted so old clients fail loudly.
    if remark_kind not in REMARK_KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {', '.join(REMARK_KINDS)}")
    if not isinstance(text, str):
        raise HTTPException(status_code=400, detail="text must be a string")

    ann: Dict[str, Any] = {"kind": remark_kind, "text": text}

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

    # Категория — ровно одна на замечание, отдельным полем. Отсутствие ключа =
    # «не трогать» (см. _resolve_category), null = сбросить в 'other'.
    if "category" in body:
        try:
            ann["category"] = annotation_categories.normalize_category(body["category"])
        except annotation_categories.CategoryError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # Резюме правки — как в Википедии: одна строка «что и зачем изменено»,
    # видимая в истории замечания и в ленте изменений. Необязательное:
    # заставлять писать его на каждое движение маркера не за что.
    if "summary" in body and body["summary"] is not None:
        summary = str(body["summary"]).strip()
        if len(summary) > MAX_SUMMARY_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=f"summary must be at most {MAX_SUMMARY_LENGTH} characters")
        ann["summary"] = summary or None

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


#: Дорогие ручки, открытые всем: проверка Google-токена ходит в сеть и считает
#: подпись, вход по токену сравнивает секреты. Им отдельный, жёсткий предел.
#: Опросные маршруты здесь же, но по другой причине: это единственные
#: анонимные пути записи в системе, и общий предел для них слишком щедр.
AUTH_PATHS = ("/api/auth/google", "/api/auth/login",
              "/api/survey/session", "/api/survey/ratings")

_rate_general = ratelimit.TokenBucket(config.RATE_LIMIT_PER_MINUTE, config.RATE_LIMIT_BURST)
_rate_auth = ratelimit.TokenBucket(config.RATE_LIMIT_AUTH_PER_MINUTE, config.RATE_LIMIT_AUTH_BURST)


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    """Ограничить частоту запросов к API по адресу клиента.

    `/api/health` не ограничивается: это проверка живости, и глушить её значит
    ослепнуть ровно тогда, когда что-то происходит."""
    path = request.url.path
    if path.startswith("/api/") and path != "/api/health":
        key = ratelimit.client_key(request)
        bucket = _rate_auth if path in AUTH_PATHS else _rate_general
        if not bucket.allow(key):
            logger.warning("rate limit hit path=%s", path)
            return JSONResponse(
                status_code=429,
                content={"detail": "too many requests"},
                headers={"Retry-After": "60"},
            )
    return await call_next(request)


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

    # Вход по токену — это вход агента: его правки ничем не хуже человеческих,
    # но авторство у них другой природы (за ними стоит прогон, agent_runs).
    user = db.get_or_create_agent_actor(username)
    session_id = db.create_session(user["id"])
    _set_session_cookie(response, session_id)

    logger.info("login: success agent=%s userId=%s", username, user["id"])
    return {"userId": user["id"], "username": username, "kind": user["kind"]}


@app.get("/api/auth/csrf")
async def get_csrf(user: Dict[str, Any] = Depends(require_user)):
    """Issue a CSRF token bound to the current session (requires login)"""
    csrf_token = f"csrf-{secrets.token_hex(16)}"
    db.set_session_csrf(user["sessionId"], csrf_token)
    logger.info("csrf: token issued length=%d", len(csrf_token))
    return {"csrfToken": csrf_token}


@app.get("/api/auth/me")
async def get_me(response: Response, user: Dict[str, Any] = Depends(require_user)):
    """Return current user info from session"""
    # Личность и роль актора: кэшировать нечего и негде. На общей машине
    # соседний вход не должен вытащить чужой ответ из истории/bfcache, а смена
    # роли обязана быть видна сразу. У /api/* нет Cache-Control ни на одном
    # ярусе (Caddy проксирует, nginx-кэш — только для статики), поэтому ставим
    # здесь.
    response.headers["Cache-Control"] = "no-store"
    logger.info("auth/me: success userId=%s role=%s", user["userId"], user["role"])
    return {
        "userId": user["userId"],
        "role": user["role"],
        "kind": user["kind"],
        "displayName": user["displayName"],
        # username: подпись актора в интерфейсе; email и аватара больше нет.
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

    # Из токена берётся ровно `sub`. Email, имя и аватар не читаются и никуда
    # не сохраняются: у изъятого сервера должно быть нечего забрать.
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="invalid credential")

    invite = body.get("invite") if isinstance(body, dict) else None
    try:
        user = db.login_with_google_sub(sub, invite_code=invite)
    except db.IdentityError:
        logger.error("google auth: IDENTITY_PEPPER is not configured")
        raise HTTPException(status_code=503, detail="identity is not configured")

    if user is None:
        # Не «неверный пароль», а «доступ не выдан»: круг участников закрыт, и
        # вход в него — только по коду, переданному вне системы.
        logger.info("google auth: rejected, no account and no valid invite")
        raise HTTPException(status_code=403, detail="invite required")

    session_id = db.create_session(user["id"])
    _set_session_cookie(response, session_id)

    logger.info("google auth: success userId=%s role=%s", user["id"], user["role"])
    return {
        "userId": user["id"],
        "role": user["role"],
        "kind": user["kind"],
        "displayName": user["displayName"],
    }


# ===== ADMIN: EDITOR ALLOWLIST =====

@app.get("/api/admin/invites")
async def get_invites(user: Dict[str, Any] = Depends(require_admin)):
    """Выданные приглашения. Кодов здесь нет — в БД лежат только их хеши."""
    return {"invites": db.list_invites()}


@app.post("/api/admin/invites")
async def create_invite(request: Request, user: Dict[str, Any] = Depends(require_admin_csrf)):
    """Выписать одноразовое приглашение.

    Код возвращается ровно один раз и передаётся человеку вне системы. Ни email,
    ни имени приглашаемого система не знает и знать не должна: список участников
    в открытом виде — это и есть то, чего мы не храним."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    role = body.get("role") if isinstance(body, dict) else None
    note = body.get("note") if isinstance(body, dict) else None
    if role is None:
        role = "editor"
    if role not in ("viewer", "editor", "admin"):
        raise HTTPException(status_code=400,
                            detail="role must be viewer, editor or admin")
    if note is not None and not isinstance(note, str):
        raise HTTPException(status_code=400, detail="note must be a string")
    if isinstance(note, str) and len(note) > 200:
        raise HTTPException(status_code=400, detail="note must be at most 200 characters")

    code, invite = db.create_invite(role=role, note=note, created_by=user["userId"])
    logger.info("admin: invite created role=%s by=%s", role, user["userId"])
    # Единственный раз, когда код виден. Потерянный не восстанавливается.
    return {"code": code, "invite": invite, "invites": db.list_invites()}


@app.delete("/api/admin/invites/{codeHash}")
async def revoke_invite(codeHash: str, user: Dict[str, Any] = Depends(require_admin_csrf)):
    if not db.revoke_invite(codeHash):
        raise HTTPException(status_code=404, detail="not found or already used")
    logger.info("admin: invite revoked by=%s", user["userId"])
    return {"invites": db.list_invites()}


@app.post("/api/admin/users/{userId}/role")
async def set_user_role(userId: int, request: Request,
                        user: Dict[str, Any] = Depends(require_admin_csrf)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    role = body.get("role") if isinstance(body, dict) else None
    try:
        updated = db.set_user_role(userId, role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if updated is None:
        raise HTTPException(status_code=404, detail="user not found")
    logger.info("admin: role set userId=%s role=%s by=%s", userId, role, user["userId"])
    return {"user": updated}


@app.post("/api/admin/users/{userId}/retire")
async def retire_user(userId: int, user: Dict[str, Any] = Depends(require_admin_csrf)):
    """Отвязать участника от аккаунта, сохранив связность истории."""
    updated = db.retire_user(userId)
    if updated is None:
        raise HTTPException(status_code=404, detail="user not found")
    logger.info("admin: user retired userId=%s by=%s", userId, user["userId"])
    return {"user": updated}


@app.post("/api/auth/display-name")
async def set_display_name(request: Request, user: Dict[str, Any] = Depends(require_csrf)):
    """Выбрать псевдоним. Это единственное имя, которым система оперирует."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    name = body.get("displayName") if isinstance(body, dict) else None
    if name is not None and not isinstance(name, str):
        raise HTTPException(status_code=400, detail="displayName must be a string")
    if isinstance(name, str) and len(name.strip()) > 60:
        raise HTTPException(status_code=400, detail="displayName must be at most 60 characters")
    updated = db.set_display_name(user["userId"], name)
    return {"user": updated}


@app.post("/api/auth/leave")
async def leave_project(user: Dict[str, Any] = Depends(require_csrf)):
    """«Покинуть проект»: то же, что admin retire, но по своей воле."""
    db.retire_user(user["userId"])
    logger.info("auth: user left userId=%s", user["userId"])
    return {"ok": True}


@app.post("/api/admin/publish-all")
async def admin_publish_all(user: Dict[str, Any] = Depends(require_admin_csrf)):
    """Republish every page in the DB to PUBLISH_DIR (volume self-heal / manual repair)."""
    result = publisher.publish_all()
    logger.info("admin: publish-all pages=%d failed=%d by=%s",
                result["pages"], result["failed"], user["userId"])
    return result


# ===== Инбокс этапа 0: /api/hello, /api/store-raw, /api/store =====
#
# ВНИМАНИЕ: клиентов нет. Ни просмотрщик, ни /work/, ни content-sync
# к этим трём эндпоинтам не обращаются — они остались от этапа 0, когда правки
# складывались в файлы инбокса до появления SQLite-канона. Вместе с ними жив
# модуль scripts/api/storage.py, существующий только ради них. Оставлены
# намеренно (решение 2026-08-30), но работающей частью системы не являются.


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
    Rendered from the SQLite remarks store (stage 2); imageUrl/origW/origH
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
        "remarks": rendered,
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
    existing remark's status (a PUT without status must not silently
    publish a draft); brand-new remarks default to published."""
    if "status" in parsed:
        return parsed["status"]
    if existing is not None:
        return existing["status"]
    return "published"


def _resolve_category(parsed: Dict[str, Any]) -> Optional[str]:
    """None ("category" absent) tells upsert_remark_db to leave the category
    alone -- the editor has no category UI yet and must not reset everything to
    'other' just by saving a text edit."""
    return parsed.get("category") if "category" in parsed else None


def _resolve_tags(parsed: Dict[str, Any]) -> Optional[List[str]]:
    """None ("tags" absent) tells upsert_remark_db to leave the tag set
    alone -- the editor doesn't send tags yet and must not wipe them. An
    explicit [] clears them."""
    return parsed.get("tags") if "tags" in parsed else None


@app.get("/api/editor/{docId}/{pageNum}")
async def get_editor_page(docId: str, pageNum: str, user: Optional[Dict[str, Any]] = Depends(get_optional_user)):
    """GET page data for editor, rendered from the SQLite remarks store.
    Anonymous/viewer callers see only published remarks; editor/admin
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
    # sha считается по «голому» render_page ДО обогащения: категория и теги в
    # него не входят и входить не должны (см. docstring render_page).
    sha = publisher.compute_page_sha(rendered)
    published_rows = db.list_page_remarks(docId, page_num_str)
    tags_by_id = {ann["remarkId"]: ann["tags"] for ann in published_rows}
    category_by_id = {ann["remarkId"]: ann["category"] for ann in published_rows}
    remarks = []
    for item in rendered:
        item = dict(item)
        if tags_by_id.get(item["id"]):
            item["tags"] = tags_by_id[item["id"]]
        item["category"] = annotation_categories.normalize_category(
            category_by_id.get(item["id"])
        )
        # render_page() заморожен в легаси-именах НАВСЕГДА: он вход
        # оптимистической блокировки, и сдвиг хеша выдал бы 409 всем открытым
        # сессиям редактора (см. его docstring). Наружу этот массив не отдаётся,
        # поэтому здесь, уже после подсчёта sha, вид переводится в текущее имя,
        # а прежнее из выдачи убирается.
        item["kind"] = db.LEGACY_KINDS.get(item.get("annType"), item.get("annType"))
        item.pop("annType", None)
        remarks.append(item)

    if user is not None and user.get("role") in EDITOR_ROLES:
        for ann in db.list_remarks(doc_id=docId, page_num=page_num_str, status="draft", limit=1000):
            item: Dict[str, Any] = {
                "id": ann["remarkId"], "text": ann["text"], "kind": ann["kind"],
                "draft": True,
            }
            if ann["coordX"] is not None and ann["coordY"] is not None:
                item["coords"] = [ann["coordX"], ann["coordY"]]
            if ann["tags"]:
                item["tags"] = ann["tags"]
            item["category"] = annotation_categories.normalize_category(ann.get("category"))
            remarks.append(item)

    logger.info("GET editor docId=%s pageKey=%s remarks=%d", docId, page_num_str, len(remarks))
    return {
        "pageId": f"{docId}_page_{page_num_str}",
        "serverPageSha": sha,
        "remarks": remarks,
    }


@app.post("/api/editor/{docId}/{pageNum}")
async def post_editor_remark(docId: str, pageNum: str, request: Request, user: Dict[str, Any] = Depends(require_editor)):
    """POST new remark: upserts into the DB, records history, republishes."""
    if not _validate_doc_id(docId):
        raise HTTPException(status_code=400, detail="invalid docId")
    page_num_str = _validate_page_key(pageNum)
    if page_num_str is None:
        raise HTTPException(status_code=400, detail="invalid pageNum")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    ann = _parse_remark_body(body if isinstance(body, dict) else {})
    if not ann.get("id"):
        ann["id"] = f"srv-{int(time.time())}-{uuid4().hex[:6]}"

    conflict = _check_optimistic_lock(
        body if isinstance(body, dict) else {}, _current_page_sha(docId, page_num_str), docId, page_num_str
    )
    if conflict is not None:
        return conflict

    coords = ann.get("coords")
    coord_x, coord_y = (coords[0], coords[1]) if coords else (None, None)
    existing = db.get_remark(docId, page_num_str, ann["id"])
    action = "update" if existing else "create"
    status = _resolve_status(ann, existing)

    db.upsert_remark_db(
        docId, page_num_str, ann["id"], ann["kind"], ann["text"],
        coord_x=coord_x, coord_y=coord_y, status=status, author_id=user["userId"], action=action,
        tags=_resolve_tags(ann),
        category=_resolve_category(ann),
        summary=ann.get("summary"),
    )
    write_ok = publisher.publish_page(docId, page_num_str)
    published = write_ok and status == "published"
    new_sha = _current_page_sha(docId, page_num_str)

    logger.info(
        "POST editor SUCCESS docId=%s pageKey=%s remarkId=%s status=%s published=%s",
        docId, page_num_str, ann["id"], status, published,
    )
    return {"id": ann["id"], "serverPageSha": new_sha, "published": published}


@app.put("/api/editor/{docId}/{pageNum}/{remarkId}")
async def put_editor_remark(docId: str, pageNum: str, remarkId: str, request: Request, user: Dict[str, Any] = Depends(require_editor)):
    """PUT update (or create, if remarkId doesn't exist yet) an remark."""
    if not _validate_doc_id(docId):
        raise HTTPException(status_code=400, detail="invalid docId")
    page_num_str = _validate_page_key(pageNum)
    if page_num_str is None:
        raise HTTPException(status_code=400, detail="invalid pageNum")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    parsed = _parse_remark_body(body if isinstance(body, dict) else {})
    parsed["id"] = remarkId

    conflict = _check_optimistic_lock(
        body if isinstance(body, dict) else {}, _current_page_sha(docId, page_num_str), docId, page_num_str
    )
    if conflict is not None:
        return conflict

    coords = parsed.get("coords")
    coord_x, coord_y = (coords[0], coords[1]) if coords else (None, None)
    existing = db.get_remark(docId, page_num_str, remarkId)
    action = "update" if existing else "create"
    status = _resolve_status(parsed, existing)

    db.upsert_remark_db(
        docId, page_num_str, remarkId, parsed["kind"], parsed["text"],
        coord_x=coord_x, coord_y=coord_y, status=status, author_id=user["userId"], action=action,
        tags=_resolve_tags(parsed),
        category=_resolve_category(parsed),
        summary=parsed.get("summary"),
    )
    write_ok = publisher.publish_page(docId, page_num_str)
    published = write_ok and status == "published"
    new_sha = _current_page_sha(docId, page_num_str)

    logger.info(
        "PUT editor SUCCESS docId=%s pageKey=%s remarkId=%s status=%s published=%s",
        docId, page_num_str, remarkId, status, published,
    )
    return {"id": remarkId, "serverPageSha": new_sha, "published": published}


@app.delete("/api/editor/{docId}/{pageNum}/{remarkId}")
async def delete_editor_remark(docId: str, pageNum: str, remarkId: str, user: Dict[str, Any] = Depends(require_editor)):
    """Soft-delete an remark and republish the page."""
    if not _validate_doc_id(docId):
        raise HTTPException(status_code=400, detail="invalid docId")
    page_num_str = _validate_page_key(pageNum)
    if page_num_str is None:
        raise HTTPException(status_code=400, detail="invalid pageNum")

    deleted = db.soft_delete_remark(docId, page_num_str, remarkId,
                                        author_id=user["userId"])
    if not deleted:
        raise HTTPException(status_code=404, detail="remark not found")

    published = publisher.publish_page(docId, page_num_str)
    new_sha = _current_page_sha(docId, page_num_str)

    logger.info(
        "DELETE editor SUCCESS docId=%s pageKey=%s remarkId=%s published=%s",
        docId, page_num_str, remarkId, published,
    )
    return {"id": remarkId, "serverPageSha": new_sha, "published": published}


# ===== УЗКИЕ ДЕЙСТВИЯ НАД ЗАМЕЧАНИЕМ =====
#
# Публикация черновика, смена категории и правка тегов ездили в общем PUT
# вместе с текстом: чтобы опубликовать замечание, очередь приёмки была обязана
# прислать его целиком. Отдельные операции делают действие явным — и в журнале
# (состав изменения выходит ровно один), и в логе, и в UI.
#
# serverPageSha эти операции не требуют. Оптимистическая блокировка защищает от
# того, что двое одновременно правят один текст; статус, категория и теги в этом
# смысле не конфликтуют. Публикация черновика при этом sha страницы всё же
# сдвигает — черновики не входят в render_page() — и открытая сессия редактора
# получит 409 на следующем сохранении. Это правильно: страница действительно
# изменилась.

def _patch_target(docId: str, pageNum: str) -> str:
    if not _validate_doc_id(docId):
        raise HTTPException(status_code=400, detail="invalid docId")
    page_num_str = _validate_page_key(pageNum)
    if page_num_str is None:
        raise HTTPException(status_code=400, detail="invalid pageNum")
    return page_num_str


async def _patch_body(request: Request) -> Dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    return body if isinstance(body, dict) else {}


def _patch_summary(body: Dict[str, Any]) -> Optional[str]:
    if "summary" not in body or body["summary"] is None:
        return None
    summary = str(body["summary"]).strip()
    if len(summary) > MAX_SUMMARY_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"summary must be at most {MAX_SUMMARY_LENGTH} characters")
    return summary or None


def _patch_result(docId: str, page_num_str: str, remarkId: str,
                  ann: Dict[str, Any], operation: str) -> Dict[str, Any]:
    published = publisher.publish_page(docId, page_num_str)
    new_sha = _current_page_sha(docId, page_num_str)
    logger.info(
        "PATCH %s SUCCESS docId=%s pageKey=%s remarkId=%s status=%s published=%s",
        operation, docId, page_num_str, remarkId, ann["status"], published,
    )
    return {"id": remarkId, "remark": ann, "serverPageSha": new_sha,
            "published": published}


@app.patch("/api/editor/{docId}/{pageNum}/{remarkId}/status")
async def patch_remark_status(docId: str, pageNum: str, remarkId: str, request: Request,
                              user: Dict[str, Any] = Depends(require_editor)):
    """Опубликовать черновик или вернуть замечание в черновики.

    'deleted' сюда не принимается: удаление — отдельная операция с собственным
    маршрутом (DELETE), и смешивать их значило бы прятать удаление за словом
    «статус»."""
    page_num_str = _patch_target(docId, pageNum)
    body = await _patch_body(request)
    status = body.get("status")
    if status not in ("draft", "published"):
        raise HTTPException(status_code=400, detail="status must be 'draft' or 'published'")

    ann = db.set_status_db(docId, page_num_str, remarkId, status,
                           author_id=user["userId"], summary=_patch_summary(body))
    if ann is None:
        raise HTTPException(status_code=404, detail="remark not found")
    return _patch_result(docId, page_num_str, remarkId, ann, "status")


@app.patch("/api/editor/{docId}/{pageNum}/{remarkId}/category")
async def patch_remark_category(docId: str, pageNum: str, remarkId: str, request: Request,
                                user: Dict[str, Any] = Depends(require_editor)):
    """Сменить категорию. null сбрасывает в «Прочее».

    В отличие от PUT, здесь отсутствие ключа не значит «не трогать»: смена
    категории — единственное, зачем этот маршрут зовут."""
    page_num_str = _patch_target(docId, pageNum)
    body = await _patch_body(request)
    if "category" not in body:
        raise HTTPException(status_code=400, detail="category is required")
    try:
        category = annotation_categories.normalize_category(body["category"])
    except annotation_categories.CategoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    ann = db.set_category_db(docId, page_num_str, remarkId, category,
                             author_id=user["userId"], summary=_patch_summary(body))
    if ann is None:
        raise HTTPException(status_code=404, detail="remark not found")
    return _patch_result(docId, page_num_str, remarkId, ann, "category")


@app.patch("/api/editor/{docId}/{pageNum}/{remarkId}/tags")
async def patch_remark_tags(docId: str, pageNum: str, remarkId: str, request: Request,
                            user: Dict[str, Any] = Depends(require_editor)):
    """Заменить набор тегов целиком. Пустой список очищает теги."""
    page_num_str = _patch_target(docId, pageNum)
    body = await _patch_body(request)
    if "tags" not in body:
        raise HTTPException(status_code=400, detail="tags is required")
    try:
        tags = db.normalize_tags(body["tags"])
    except db.TagError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    ann = db.set_tags_db(docId, page_num_str, remarkId, tags,
                         author_id=user["userId"], summary=_patch_summary(body))
    if ann is None:
        raise HTTPException(status_code=404, detail="remark not found")
    return _patch_result(docId, page_num_str, remarkId, ann, "tags")


# ===== CABINET (stage 3) =====

#: Резюме правки: одна строка, а не второй текст замечания.


async def require_editor_read(user: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    """Editor role or above, no CSRF (GET-only cabinet list endpoints)."""
    if user.get("role") not in EDITOR_ROLES:
        raise HTTPException(status_code=403, detail="editor role required")
    return user


def _validate_list_params(
    docId: Optional[str], pageKey: Optional[str], kind: Optional[str],
    status: Optional[str], limit: int, offset: int, q: Optional[str],
) -> Dict[str, Any]:
    if docId is not None and not _validate_doc_id(docId):
        raise HTTPException(status_code=400, detail="invalid docId")
    page_num_str = None
    if pageKey is not None:
        page_num_str = _validate_page_key(pageKey)
        if page_num_str is None:
            raise HTTPException(status_code=400, detail="invalid pageKey")
    if status is not None and status not in REMARK_STATUSES:
        raise HTTPException(status_code=400, detail="invalid status")
    if kind is not None:
        if kind not in REMARK_KINDS:
            raise HTTPException(status_code=400, detail="invalid kind")
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")
    if q is not None and len(q) > 200:
        raise HTTPException(status_code=400, detail="q must be at most 200 characters")
    return {"pageKey": page_num_str, "kind": kind}


@app.get("/api/remarks")
async def list_remarks(
    docId: Optional[str] = None,
    pageKey: Optional[str] = None,
    kind: Optional[str] = None,
    status: Optional[str] = None,
    authorId: Optional[int] = None,
    q: Optional[str] = None,
    tag: Optional[str] = None,
    category: Optional[str] = None,
    categorySource: Optional[str] = None,
    section: Optional[str] = None,
    inPool: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
    user: Dict[str, Any] = Depends(require_editor_read),
):
    validated = _validate_list_params(docId, pageKey, kind,
                                      status, limit, offset, q)
    if tag is not None:
        try:
            tag = db.normalize_tag(tag)
        except db.TagError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    # Категория и её источник — вход очереди приёмки: «показать неразобранное»
    # это categorySource=default, «проверить решения агента» — categorySource=agent.
    if category is not None:
        try:
            category = annotation_categories.normalize_category(category)
        except annotation_categories.CategoryError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if categorySource is not None:
        try:
            categorySource = db.normalize_category_source(categorySource)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    kind = validated["kind"]
    # inPool — членство в пуле опроса. Признак приходит в каждой строке, а не
    # только в фильтре: без него кнопка «в опрос» работала в один конец —
    # положить можно было, а увидеть, что замечание уже там, нельзя.
    items = db.list_remarks(
        doc_id=docId, page_num=validated["pageKey"], kind=kind, status=status,
        author_id=authorId, q=q, limit=limit, offset=offset, tag=tag,
        category=category, category_source=categorySource, section_id=section,
        in_pool=inPool, with_pool=True,
    )
    total = db.count_remarks(
        doc_id=docId, page_num=validated["pageKey"], kind=kind, status=status,
        author_id=authorId, q=q, tag=tag,
        category=category, category_source=categorySource, section_id=section,
        in_pool=inPool,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.get("/api/remarks/{docId}/{pageKey}/{remarkId}")
async def get_one_remark(docId: str, pageKey: str, remarkId: str,
                             user: Dict[str, Any] = Depends(require_editor_read)):
    """Одно замечание целиком — вход карточки в редакторе.

    Список `/api/remarks` для этого не годится: карточке нужен конкретный
    замечание по адресу, а не страница выдачи, из которой его надо выуживать."""
    if not _validate_doc_id(docId):
        raise HTTPException(status_code=400, detail="invalid docId")
    page_num_str = _validate_page_key(pageKey)
    ann = db.get_remark(docId, page_num_str, remarkId, with_pool=True)
    if ann is None:
        raise HTTPException(status_code=404, detail="remark not found")
    section = db.find_section_for_page(docId, page_num_str)
    return {"remark": ann, "section": section}


# ===== ОЦЕНКИ И КОММЕНТАРИИ =====
#
# Рабочие данные редактора. Ни один маршрут ниже не вызывает publish_page:
# оценки и комментарии в статику не попадают — это не «пока не сделано», а
# условие задачи (главный инвариант проекта, docs/README.md).


def _remark_target(docId: str, pageKey: str, remarkId: str) -> str:
    """Проверить адрес и убедиться, что замечание существует. Возвращает
    нормализованный ключ страницы."""
    if not _validate_doc_id(docId):
        raise HTTPException(status_code=400, detail="invalid docId")
    page_num_str = _validate_page_key(pageKey)
    if page_num_str is None:
        raise HTTPException(status_code=400, detail="invalid pageKey")
    if db.get_remark(docId, page_num_str, remarkId) is None:
        raise HTTPException(status_code=404, detail="remark not found")
    return page_num_str


@app.get("/api/rating-scales")
async def get_rating_scales(user: Dict[str, Any] = Depends(require_editor_read)):
    """Какие шкалы существуют и как называются — единственный источник для UI."""
    return {"scales": rating_scales.describe()}


@app.get("/api/remarks/{docId}/{pageKey}/{remarkId}/ratings")
async def get_remark_ratings(docId: str, pageKey: str, remarkId: str,
                             user: Dict[str, Any] = Depends(require_editor_read)):
    page_num_str = _remark_target(docId, pageKey, remarkId)
    return {
        "summary": db.summarize_ratings(docId, page_num_str, remarkId,
                                        rater_id=user["userId"]),
        "items": db.list_ratings(docId, page_num_str, remarkId),
    }


@app.put("/api/remarks/{docId}/{pageKey}/{remarkId}/ratings/{scale}")
async def put_remark_rating(docId: str, pageKey: str, remarkId: str, scale: str,
                            request: Request,
                            user: Dict[str, Any] = Depends(require_editor)):
    """Поставить или изменить свою оценку по одной шкале.

    Оценка ничего не публикует и ничего не скрывает: кворум удалённой
    ревью-подсистемы не воскрешаем, публикация остаётся явным действием."""
    page_num_str = _remark_target(docId, pageKey, remarkId)
    body = await _patch_body(request)
    try:
        scale_name = rating_scales.normalize_scale(scale)
        value = rating_scales.normalize_value(scale_name, body.get("value"))
        note = rating_scales.normalize_note(body.get("note"))
    except rating_scales.ScaleError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    rating = db.set_rating(docId, page_num_str, remarkId, scale_name, value,
                           rater_id=user["userId"], note=note)
    logger.info("rating set docId=%s pageKey=%s remarkId=%s scale=%s by=%s",
                docId, page_num_str, remarkId, scale_name, user["userId"])
    return {"rating": rating,
            "summary": db.summarize_ratings(docId, page_num_str, remarkId,
                                            rater_id=user["userId"])}


@app.delete("/api/remarks/{docId}/{pageKey}/{remarkId}/ratings/{scale}")
async def delete_remark_rating(docId: str, pageKey: str, remarkId: str, scale: str,
                               user: Dict[str, Any] = Depends(require_editor)):
    """Снять свою оценку. Чужие оценки этим маршрутом не трогаются."""
    page_num_str = _remark_target(docId, pageKey, remarkId)
    try:
        scale_name = rating_scales.normalize_scale(scale)
    except rating_scales.ScaleError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not db.clear_rating(docId, page_num_str, remarkId, scale_name, user["userId"]):
        raise HTTPException(status_code=404, detail="rating not found")
    return {"summary": db.summarize_ratings(docId, page_num_str, remarkId,
                                            rater_id=user["userId"])}


@app.get("/api/remarks/{docId}/{pageKey}/{remarkId}/notes")
async def get_remark_notes(docId: str, pageKey: str, remarkId: str,
                           user: Dict[str, Any] = Depends(require_editor_read)):
    page_num_str = _remark_target(docId, pageKey, remarkId)
    return {"items": db.list_notes(docId, page_num_str, remarkId),
            "open": db.count_open_notes(docId, page_num_str, remarkId)}


@app.post("/api/remarks/{docId}/{pageKey}/{remarkId}/notes")
async def post_remark_note(docId: str, pageKey: str, remarkId: str, request: Request,
                           user: Dict[str, Any] = Depends(require_editor)):
    """Оставить комментарий или ответ на него (`parentId`)."""
    page_num_str = _remark_target(docId, pageKey, remarkId)
    body = await _patch_body(request)
    parent_id = body.get("parentId")
    if parent_id is not None and not isinstance(parent_id, int):
        raise HTTPException(status_code=400, detail="parentId must be an integer")
    try:
        note = db.add_note(docId, page_num_str, remarkId, user["userId"],
                           body.get("body"), parent_id=parent_id)
    except db.NoteError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    logger.info("note added docId=%s pageKey=%s remarkId=%s by=%s parent=%s",
                docId, page_num_str, remarkId, user["userId"], parent_id)
    return {"note": note}


def _own_note_or_403(note_id: int, user: Dict[str, Any]) -> Dict[str, Any]:
    note = db.get_note(note_id)
    if note is None or note["deleted"]:
        raise HTTPException(status_code=404, detail="note not found")
    if note["authorId"] != user["userId"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="not your note")
    return note


@app.patch("/api/notes/{noteId}")
async def patch_note(noteId: int, request: Request,
                     user: Dict[str, Any] = Depends(require_editor)):
    """Поправить свой комментарий либо пометить тред решённым.

    Закрыть тред может любой редактор, а не только автор: решение — про работу,
    а не про авторство. Править текст — только автор (или админ).
    """
    body = await _patch_body(request)
    if "resolved" in body:
        if not isinstance(body["resolved"], bool):
            raise HTTPException(status_code=400, detail="resolved must be a boolean")
        try:
            note = db.resolve_note(noteId, body["resolved"], user["userId"])
        except db.NoteError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if note is None:
            raise HTTPException(status_code=404, detail="note not found")
        return {"note": note}

    if "body" not in body:
        raise HTTPException(status_code=400, detail="body or resolved is required")
    _own_note_or_403(noteId, user)
    try:
        note = db.edit_note(noteId, body["body"])
    except db.NoteError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")
    return {"note": note}


@app.delete("/api/notes/{noteId}")
async def delete_note(noteId: int, user: Dict[str, Any] = Depends(require_editor)):
    """Мягко удалить свой комментарий: строка остаётся ради связности треда,
    тело затирается."""
    _own_note_or_403(noteId, user)
    note = db.delete_note(noteId)
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")
    return {"note": note}


@app.get("/api/remarks/{docId}/{pageKey}/{remarkId}/timeline")
async def get_remark_timeline(docId: str, pageKey: str, remarkId: str,
                              limit: int = 100,
                              user: Dict[str, Any] = Depends(require_editor_read)):
    """Всё, что происходило с замечанием, одним списком: ревизии, оценки
    участников, ответы опроса, комментарии. Новые сверху.

    Слияние здесь, а не в БД: источники живут в разных таблицах намеренно
    (ревизия — снимок состояния, оценка и комментарий состояния не меняют,
    а ответ опроса приходит от человека вне круга и в `users` не заводится),
    и сводить их в одну таблицу ради удобства чтения значило бы сломать откат.
    """
    page_num_str = _remark_target(docId, pageKey, remarkId)
    items: List[Dict[str, Any]] = []

    for rev in db.list_history(doc_id=docId, page_num=page_num_str,
                               remark_id=remarkId, limit=limit):
        items.append({
            "kind": "revision",
            "id": rev["id"],
            "actions": rev["changes"],
            "actionLabel": rev["actionLabel"],
            "actorId": rev["authorId"],
            "actorName": rev["authorName"],
            "createdAt": rev["createdAt"],
            "revNo": rev["revNo"],
            "agentRunId": rev["agentRunId"],
            "summary": rev["summary"],
            "text": (rev["snapshot"] or {}).get("text"),
        })

    for rating in db.list_ratings(docId, page_num_str, remarkId):
        items.append({
            "kind": "rating",
            "source": "editor",
            "id": rating["id"],
            "actions": ["rate"],
            "actionLabel": remark_actions.LABELS["rate"],
            "actorId": rating["raterId"],
            "actorName": rating["raterName"],
            # Оценка перезаписывается, и в ленте она стоит по времени последней
            # правки: показывать её на месте первой было бы неверно.
            "createdAt": rating["updatedAt"],
            "scale": rating["scale"],
            "value": rating["value"],
            "note": rating["note"],
        })

    for answer in db.list_survey_ratings(docId, page_num_str, remarkId):
        items.append({
            "kind": "rating",
            # Тот же вид события, но другой источник: оценку участника и ответ
            # с улицы нельзя складывать в одно среднее, и в ленте они тоже
            # должны различаться на вид.
            "source": "survey",
            "id": answer["id"],
            "actions": ["rate"],
            "actionLabel": remark_actions.LABELS["rate"],
            # Респондента нет в `users`, и actorId ему взять неоткуда:
            # подписью служит `anonymous:<псевдоним>`.
            "actorId": None,
            "actorName": answer["author"],
            "createdAt": answer["updatedAt"],
            "scale": answer["scale"],
            "value": answer["value"],
            "note": None,
        })

    for note in db.list_notes(docId, page_num_str, remarkId):
        items.append({
            "kind": "note",
            "id": note["id"],
            "actions": ["note"],
            "actionLabel": remark_actions.LABELS["note"],
            "actorId": note["authorId"],
            "actorName": note["authorName"],
            "createdAt": note["createdAt"],
            "body": note["body"],
            "parentId": note["parentId"],
            "resolved": note["resolved"],
        })

    # Метки времени огрублены до дня (docs/anonymity-model.md), поэтому внутри
    # одного дня порядок задаёт id — он же порядок записи. id считается в
    # пределах своей таблицы, поэтому вид события входит в ключ: иначе порядок
    # зависел бы от того, в какой таблице счётчик убежал дальше.
    items.sort(key=lambda item: (item["createdAt"], item["kind"], item["id"]),
               reverse=True)
    return {"items": items[:limit]}


# ===== ОПРОС =====
#
# Опрос спрашивает у людей вне закрытого круга то, на что круг ответить не
# может: интересен ли факт и можно ли предъявлять замечание в такой
# формулировке. Как и оценки участников, ни один маршрут ниже не вызывает
# publish_page — в статику опрос не попадает никогда.
#
# Респондент опознаётся токеном в заголовке `X-Survey-Token`, а не кукой.
# Кука потребовала бы CSRF-защиты; токен, недоступный чужому сайту, снимает
# вопрос целиком. В базе от токена остаётся только хеш.
# Модель субъекта — docs/anonymity-model.md, «Анонимные респонденты опроса».


async def require_respondent(request: Request) -> Dict[str, Any]:
    """FastAPI dependency: респондент опроса по токену или 401."""
    respondent = db.get_respondent_by_token(request.headers.get("X-Survey-Token"))
    if not respondent:
        raise HTTPException(status_code=401, detail="survey session required")
    return respondent


@app.post("/api/survey/session")
async def create_survey_session(body: Dict[str, Any]):
    """Начать опрос: назваться псевдонимом и получить токен сессии.

    Единственный маршрут в системе, который заводит субъекта без приглашения.
    Ничего, кроме псевдонима и хеша токена, при этом не записывается.
    """
    try:
        respondent = db.create_respondent(body.get("pseudonym"))
    except db.SurveyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    logger.info("survey session created respondent_id=%s", respondent["id"])
    # Шкалы отдаются здесь же. Заводить для них анонимный маршрут не нужно, а
    # переписывать словарь шкал в опроснике — тем более: у описания шкал ровно
    # один источник (rating_scales), и он должен остаться единственным.
    return dict(respondent, scales=rating_scales.describe())


@app.get("/api/survey/batch")
async def get_survey_batch(limit: int = db.SURVEY_BATCH_SIZE,
                           respondent: Dict[str, Any] = Depends(require_respondent)):
    """Очередная порция замечаний: случайные из пула, которых этот респондент
    ещё не оценивал.

    Отдаются одни адреса. Текст замечания опросник берёт с обычной читательской
    страницы (`?only=<id>`) — второго пути к тексту, да ещё анонимного, заводить
    не нужно.
    """
    limit = max(1, min(limit, db.MAX_SURVEY_BATCH_SIZE))
    batch = db.pool_pick(respondent["id"], limit=limit)
    # Шкалы прилагаются и здесь: вкладку могли перезагрузить посреди опроса,
    # и тогда ответа `/session` со словарём шкал у опросника уже нет.
    return {"items": batch["items"], "remaining": batch["remaining"],
            "author": respondent["author"], "scales": rating_scales.describe()}


@app.put("/api/survey/ratings")
async def put_survey_rating(body: Dict[str, Any],
                            respondent: Dict[str, Any] = Depends(require_respondent)):
    """Ответы по одному замечанию — все шкалы одним вызовом.

    Карточка опроса оценивается целиком, и разбивать её на три запроса значило
    бы допускать наполовину заполненные ответы там, где половины не бывает.
    Незаполненную шкалу можно не присылать; пустой ответ — 400.
    """
    doc_id = str(body.get("docId") or "")
    page_key = str(body.get("pageKey") or body.get("pageNum") or "")
    remark_id = str(body.get("remarkId") or "")
    page_num_str = _remark_target(doc_id, page_key, remark_id)
    if not db.pool_contains(doc_id, page_num_str, remark_id):
        # Отвечать можно только на то, что вынесено на оценку: иначе опрос
        # превращается в анонимную запись по любому адресу.
        raise HTTPException(status_code=403, detail="remark is not in the rating pool")

    values: Dict[str, int] = {}
    for scale in rating_scales.names():
        if body.get(scale) is None:
            continue
        try:
            values[scale] = rating_scales.normalize_value(scale, body.get(scale))
        except rating_scales.ScaleError as exc:
            raise HTTPException(status_code=400, detail=f"{scale}: {exc}")
    if not values:
        raise HTTPException(status_code=400, detail="no answers given")

    for scale, value in values.items():
        db.set_survey_rating(respondent["id"], doc_id, page_num_str, remark_id,
                             scale, value)
    return {"saved": sorted(values), "docId": doc_id, "pageNum": page_num_str,
            "remarkId": remark_id}


@app.get("/api/survey/pool")
async def get_survey_pool(docId: Optional[str] = None, limit: int = 200, offset: int = 0,
                          user: Dict[str, Any] = Depends(require_editor_read)):
    """Пул — редакторская работа: положить замечание на оценку решает тот, кто
    его читает. Админской остаётся сводка ответов (`/api/survey/results`):
    агрегированные мнения анонимных респондентов ближе к персональным данным,
    чем к содержанию разбора (docs/anonymity-model.md)."""
    if docId is not None and not _validate_doc_id(docId):
        raise HTTPException(status_code=400, detail="invalid docId")
    return db.pool_list(doc_id=docId, limit=max(1, min(limit, 500)), offset=max(0, offset))


@app.post("/api/survey/pool")
async def add_to_survey_pool(body: Dict[str, Any],
                             user: Dict[str, Any] = Depends(require_editor)):
    """Вынести замечание на оценку. Повтор — не ошибка."""
    page_num_str = _remark_target(str(body.get("docId") or ""),
                                  str(body.get("pageKey") or body.get("pageNum") or ""),
                                  str(body.get("remarkId") or ""))
    item = db.pool_add(str(body.get("docId")), page_num_str,
                       str(body.get("remarkId")), added_by=user["userId"])
    return {"item": item}


@app.delete("/api/survey/pool/{docId}/{pageKey}/{remarkId}")
async def remove_from_survey_pool(docId: str, pageKey: str, remarkId: str,
                                  user: Dict[str, Any] = Depends(require_editor)):
    """Убрать из раздачи. Уже полученные ответы остаются: снять вопрос и
    стереть ответы — разные действия, и второе здесь не подразумевается."""
    if not _validate_doc_id(docId):
        raise HTTPException(status_code=400, detail="invalid docId")
    page_num_str = _validate_page_key(pageKey)
    if page_num_str is None:
        raise HTTPException(status_code=400, detail="invalid pageKey")
    if not db.pool_remove(docId, page_num_str, remarkId):
        raise HTTPException(status_code=404, detail="not in the rating pool")
    return {"removed": True}


@app.get("/api/survey/results")
async def get_survey_results(docId: Optional[str] = None, limit: int = 100, offset: int = 0,
                             user: Dict[str, Any] = Depends(require_admin)):
    """Таблица результатов. Считается по ответам, а не по пулу: замечание могли
    снять с раздачи, но полученные ответы от этого не исчезли."""
    if docId is not None and not _validate_doc_id(docId):
        raise HTTPException(status_code=400, detail="invalid docId")
    return db.survey_results(doc_id=docId, limit=max(1, min(limit, 200)),
                             offset=max(0, offset))


@app.get("/api/sections")
async def get_sections(docId: str, user: Dict[str, Any] = Depends(require_editor_read)):
    """Параграфы документа со сводкой — доска работ редактора.

    Сводка включает число черновиков и неразобранных по категориям, поэтому
    эндпоинт редакторский, хотя сама разметка параграфов публична (она приезжает
    из manifest metadata.json через scripts/api/import_sections.py)."""
    if not _validate_doc_id(docId):
        raise HTTPException(status_code=400, detail="invalid docId")
    return {"sections": db.list_sections(docId)}


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
    remarkId: Optional[str] = None,
    authorId: Optional[int] = None,
    action: Optional[str] = None,
    # Состав изменения: `action` отвечает, кто и чем записал ревизию, `changed`
    # — что при этом изменилось. Разные вопросы, разные фильтры.
    changed: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    user: Dict[str, Any] = Depends(require_editor_read),
):
    validated = _validate_list_params(docId, pageKey, None, None, limit, offset, None)
    if changed is not None and not remark_actions.is_known(changed):
        raise HTTPException(
            status_code=400,
            detail=f"changed must be one of: {', '.join(remark_actions.ACTIONS)}")
    items = db.list_history(
        doc_id=docId, page_num=validated["pageKey"], remark_id=remarkId,
        author_id=authorId,
        action=action, changed=changed, limit=limit, offset=offset,
    )
    return {"items": items, "hasMore": len(items) == limit, "limit": limit, "offset": offset}


@app.get("/api/stats")
async def get_stats(user: Dict[str, Any] = Depends(require_user)):
    return db.get_stats()


@app.post("/api/history/{histId}/revert")
async def revert_history(histId: int, user: Dict[str, Any] = Depends(require_editor)):
    """Restore a remark to the exact state recorded in a history
    snapshot (including that snapshot's own status -- reverting to a
    delete-record re-deletes, which is intentional: the cabinet shows every
    record's action and lets the user pick the state they want back)."""
    record = db.get_history_record(histId)
    if record is None:
        raise HTTPException(status_code=404, detail="history record not found")

    snapshot = record["snapshot"] or {}
    doc_id = record["docId"]
    page_num = record["pageNum"]
    remark_id = record["remarkId"]
    coords = None
    if snapshot.get("coordX") is not None and snapshot.get("coordY") is not None:
        coords = [snapshot["coordX"], snapshot["coordY"]]

    db.upsert_remark_db(
        doc_id, page_num, remark_id, snapshot.get("kind"), snapshot.get("text"),
        coord_x=coords[0] if coords else None, coord_y=coords[1] if coords else None,
        status=snapshot.get("status", "published"), author_id=user["userId"], action="revert",
        # Only snapshots taken after tags existed carry the key; older ones must
        # leave the current tag set alone rather than clear it.
        tags=snapshot["tags"] if "tags" in snapshot else None,
        category=snapshot.get("category"),
    )
    published = publisher.publish_page(doc_id, page_num)
    new_sha = _current_page_sha(doc_id, page_num)

    logger.info(
        "revert histId=%s docId=%s pageKey=%s remarkId=%s by=%s",
        histId, doc_id, page_num, remark_id, user["userId"],
    )
    return {"remarkId": remark_id, "docId": doc_id, "pageNum": page_num, "serverPageSha": new_sha, "published": published}


@app.get("/api/admin/users")
async def get_admin_users(user: Dict[str, Any] = Depends(require_admin)):
    return {"users": db.list_users()}


# Allow running with `python main.py` for local dev

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=False, workers=1, proxy_headers=True)
