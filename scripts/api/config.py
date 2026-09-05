import os
from typing import Dict, List, Union

# Defaults per spec
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
LOG_LEVEL: str = os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL).upper()
LOG_DIR: str = os.getenv("LOG_DIR", DEFAULT_LOG_DIR)
CORS_ALLOW_ORIGINS_RAW: str = os.getenv("CORS_ALLOW_ORIGINS", DEFAULT_CORS_ENV)
CORS_ALLOW_ORIGINS = _parse_cors_origins(CORS_ALLOW_ORIGINS_RAW)

# Agent access tokens: "token1:agentname1,token2:agentname2" -> {token: name}.
# Empty/missing -> {} (token login disabled). Это вход для агентов: правки
# агента ничем не хуже человеческих, но авторство у них другой природы —
# за ними стоит прогон с версией промпта (таблица agent_runs).
# EDITOR_TOKENS — прежнее имя той же переменной, читается для совместимости.
AGENT_TOKENS: Dict[str, str] = _parse_editor_tokens(
    os.getenv("AGENT_TOKENS", "") or os.getenv("EDITOR_TOKENS", "")
)
#: Deprecated alias, оставлен чтобы не ломать существующие вызовы.
EDITOR_TOKENS: Dict[str, str] = AGENT_TOKENS

# SQLite database for users/sessions/allowlist (stage 1). Deliberately NOT
# under PUBLISH_DIR: that path is the mounted redpen-publish working copy, and
# the DB file must not end up inside the publication git repo.
DEFAULT_DB_PATH = "/var/redpen-db/redpen.db"
DB_PATH: str = os.getenv("DB_PATH", DEFAULT_DB_PATH)

# Перец для хеширования идентификатора Google (`sub`). Живёт только здесь, в
# окружении сервера, и НЕ попадает ни в БД, ни в её бэкапы: утечка бэкапа даёт
# непрозрачные хеши, которые без содействия Google ни к кому не привязать.
#
# Пустое значение — это НЕ «выключено»: без перца хеш вырождается в обычный
# sha256 от `sub`, поэтому вход через Google в таком случае отвечает 503.
# Потеря перца = все участники перерегистрируются по инвайтам; цена известна.
IDENTITY_PEPPER: str = os.getenv("IDENTITY_PEPPER", "")

# Одноразовый код для выдачи первой роли admin на пустой базе. Список
# администраторов по email намеренно упразднён: он хранил бы личности в
# открытом виде в окружении прода, рядом с бэкапами БД.
BOOTSTRAP_INVITE_CODE: str = os.getenv("BOOTSTRAP_INVITE_CODE", "")

# Ограничение частоты запросов к API (защита от простого залива).
# Пределы на один адрес в минуту; 0 отключает проверку.
#
# Обычный предел щедрый: живой редактор шлёт десятки запросов на страницу.
# Предел для входа — жёсткий: проверка Google-токена ходит в сеть и считает
# подпись, то есть это самая дорогая ручка, и она открыта всем.
RATE_LIMIT_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "240"))
RATE_LIMIT_BURST: int = int(os.getenv("RATE_LIMIT_BURST", "60"))
RATE_LIMIT_AUTH_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_AUTH_PER_MINUTE", "12"))
RATE_LIMIT_AUTH_BURST: int = int(os.getenv("RATE_LIMIT_AUTH_BURST", "6"))

# Google Identity Services OAuth client id (audience for ID-token verification).
# Empty -> POST /api/auth/google responds 503 (not configured).
GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")

# Whether the session cookie gets the Secure flag. Defaults to true (prod is
# HTTPS); set to false for local http development.
COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE", "true").strip().lower() not in ("0", "false", "no")

# Directory the publisher (scripts/api/publisher.py) writes rendered
# remarks/page_NNN.json snapshots into (stage 2). Empty -> publication is
# disabled (tests, local dev without the redpen_public volume mounted).
PUBLISH_DIR: str = os.getenv("PUBLISH_DIR", "")
