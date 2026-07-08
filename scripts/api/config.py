import os
from typing import Dict, List, Union

# Defaults per spec
DEFAULT_STORAGE_DIR = "/data"
DEFAULT_LOG_LEVEL = "INFO"
# Special handling for CORS: env default is "_" which we treat as wildcard "*"
DEFAULT_CORS_ENV = "_"


def _parse_editor_tokens(value: str) -> Dict[str, str]:
    """
    Parse EDITOR_TOKENS environment value into a {token: username} dict.
    Format: "token1:username1,token2:username2". Empty/missing -> {}.
    """
    if not value:
        return {}
    tokens: Dict[str, str] = {}
    for pair in value.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        token, username = pair.split(":", 1)
        token = token.strip()
        username = username.strip()
        if token and username:
            tokens[token] = username
    return tokens


def cors_settings(origins: List[str]):
    """
    Given a parsed origins list, return (origins, allow_credentials).
    "*" + allow_credentials=True is an invalid/rejected combination, so
    credentials are only allowed alongside an explicit origin list.
    """
    origins = list(dict.fromkeys(origins))
    allow_credentials = origins != ["*"]
    return origins, allow_credentials


def _parse_cors_origins(value: str) -> Union[List[str], List[str]]:
    """
    Parse CORS_ALLOW_ORIGINS environment value into a list for CORSMiddleware.
    - "_" or "*" -> ["*"]
    - Comma-separated list -> [origins]
    - Single value -> [value]
    """
    if value is None:
        return ["*"]
    val = value.strip()
    if val in ("_", "*"):
        return ["*"]
    # split by comma
    parts = [p.strip() for p in val.split(",") if p.strip()]
    # Remove duplicates while preserving order
    parts = list(dict.fromkeys(parts))
    return parts or ["*"]


# Default directory for application log files. Overridable via LOG_DIR so the
# service can run (and be tested) outside the container, where /app is absent.
DEFAULT_LOG_DIR = "/app/logs"

# Public config values
STORAGE_DIR: str = os.getenv("STORAGE_DIR", DEFAULT_STORAGE_DIR)
LOG_LEVEL: str = os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL).upper()
LOG_DIR: str = os.getenv("LOG_DIR", DEFAULT_LOG_DIR)
CORS_ALLOW_ORIGINS_RAW: str = os.getenv("CORS_ALLOW_ORIGINS", DEFAULT_CORS_ENV)
CORS_ALLOW_ORIGINS = _parse_cors_origins(CORS_ALLOW_ORIGINS_RAW)

# Editor access tokens: "token1:username1,token2:username2" -> {token: username}.
# Empty/missing -> {} (token login disabled).
EDITOR_TOKENS: Dict[str, str] = _parse_editor_tokens(os.getenv("EDITOR_TOKENS", ""))

# SQLite database for users/sessions/allowlist (stage 1). Deliberately NOT
# under STORAGE_DIR: that path is the mounted redpen-publish working copy, and
# the DB file must not end up inside the publication git repo.
DEFAULT_DB_PATH = "/var/redpen-db/redpen.db"
DB_PATH: str = os.getenv("DB_PATH", DEFAULT_DB_PATH)

# Emails granted the admin role unconditionally (comma-separated).
ADMIN_EMAILS: List[str] = [
    e.strip() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()
]

# Google Identity Services OAuth client id (audience for ID-token verification).
# Empty -> POST /api/auth/google responds 503 (not configured).
GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")

# Whether the session cookie gets the Secure flag. Defaults to true (prod is
# HTTPS); set to false for local http development.
COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE", "true").strip().lower() not in ("0", "false", "no")
