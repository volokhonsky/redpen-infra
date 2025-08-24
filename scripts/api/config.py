import os
from typing import List, Union

# Defaults per spec
DEFAULT_STORAGE_DIR = "/data"
DEFAULT_LOG_LEVEL = "INFO"
# Special handling for CORS: env default is "_" which we treat as wildcard "*"
DEFAULT_CORS_ENV = "_"


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
    return parts or ["*"]


# Public config values
STORAGE_DIR: str = os.getenv("STORAGE_DIR", DEFAULT_STORAGE_DIR)
LOG_LEVEL: str = os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL).upper()
CORS_ALLOW_ORIGINS_RAW: str = os.getenv("CORS_ALLOW_ORIGINS", DEFAULT_CORS_ENV)
CORS_ALLOW_ORIGINS = _parse_cors_origins(CORS_ALLOW_ORIGINS_RAW)
