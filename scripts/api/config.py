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
