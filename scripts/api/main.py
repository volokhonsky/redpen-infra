import json
import logging
import sys
import time
from uuid import uuid4
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

# Optional .env loading
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

import config
import storage


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("redpen.api")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%dT%H:%M:%S%z")
        handler.setFormatter(fmt)
        logger.addHandler(handler)
        logger.propagate = False
    # Set level from config
    level = getattr(logging, (config.LOG_LEVEL or "INFO").upper(), logging.INFO)
    logger.setLevel(level)
    return logger


logger = setup_logger()

app = FastAPI()

# CORS configuration
allow_origins = config.CORS_ALLOW_ORIGINS or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("service started LOG_LEVEL=%s storage_dir=%s", config.LOG_LEVEL, config.STORAGE_DIR)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


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


# ---------------- Pages Endpoints ----------------

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
    # else ignore coords for general

    # Optional id in POST body
    if "id" in body and isinstance(body["id"], str) and body["id"].strip() != "":
        ann["id"] = body["id"].strip()

    return ann


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


# Allow running with `python main.py` for local dev
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=False, workers=1, proxy_headers=True)
