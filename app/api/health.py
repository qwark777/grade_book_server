import os
from datetime import datetime, UTC

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.db.connection import get_db_connection
from app.websocket.manager import ws_manager

router = APIRouter()

STORAGE_DIRS = (
    "PROFILE_PHOTOS_DIR",
    "ACHIEVEMENTS_DIR",
    "HOMEWORK_SUBMISSIONS_DIR",
    "HOMEWORK_ATTACHMENTS_DIR",
    "MESSAGE_ATTACHMENTS_DIR",
)


def _row_ok(row) -> bool:
    if isinstance(row, dict):
        return row.get("ok") == 1
    if isinstance(row, (list, tuple)):
        return bool(row and row[0] == 1)
    return bool(row)


def _storage_ok() -> bool:
    for attr in STORAGE_DIRS:
        path = getattr(settings, attr)
        if not os.path.isdir(path) or not os.access(path, os.W_OK):
            return False
    return True


@router.get("/health")
async def healthcheck():
    realtime = await ws_manager.snapshot()
    checks = {
        "database": False,
        "storage": _storage_ok(),
        "realtime": bool(realtime["broker"]["healthy"]),
    }

    conn = None
    try:
        conn = await get_db_connection()
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT 1 AS ok")
            row = await cursor.fetchone()
            checks["database"] = _row_ok(row)
    finally:
        if conn is not None:
            conn.close()

    status = "ok" if all(checks.values()) else "degraded"
    payload = {
        "status": status,
        "service": settings.PROJECT_NAME,
        "time": datetime.now(UTC).isoformat(),
        "checks": checks,
        "realtime": realtime,
    }
    return JSONResponse(payload, status_code=200 if status == "ok" else 503)
