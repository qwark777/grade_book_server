import asyncio
import logging
import os
import sys
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from urllib.parse import quote

if __package__ in (None, ""):
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.staticfiles import StaticFiles

from app.core.config import settings
from app.core.distributed_lock import mysql_named_lock
from app.core.subscription_gate import check_school_subscription_gate
from app.db.connection import close_db_pool, init_db, init_db_pool
from app.api.main import api_router
from app.websocket.endpoints import websocket_endpoint
from app.services.startup import create_first_owner
from app.services.achievement_service import ensure_achievement_catalog
from app.db.seed_subscriptions import ensure_subscription_data
from app.services.subscription_sync import run_subscription_sync_loop
from app.websocket.manager import ws_manager


request_logger = logging.getLogger("grade_book.requests")
MAX_REQUEST_ID_LENGTH = 128
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    await init_db_pool()
    if settings.INIT_DB_ON_STARTUP:
        await init_db()
    async with mysql_named_lock(
        "gradebook:startup",
        timeout_seconds=30,
    ) as startup_leader:
        if startup_leader:
            await create_first_owner()
            await ensure_subscription_data()
            await ensure_achievement_catalog()
    await ws_manager.start()
    subscription_sync_task = asyncio.create_task(run_subscription_sync_loop())
    try:
        yield
    finally:
        subscription_sync_task.cancel()
        try:
            await subscription_sync_task
        except asyncio.CancelledError:
            pass
        await ws_manager.stop()
        await close_db_pool()


# Create FastAPI application
app = FastAPI(
    title=settings.PROJECT_NAME,
    lifespan=lifespan
)

_request_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_REQUESTS)
_request_buckets = defaultdict(deque)
LOCAL_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5000",
    "http://localhost:8000",
    "http://localhost:8001",
    "http://localhost:8080",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5000",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:8001",
    "http://127.0.0.1:8080",
]


def _should_skip_rate_limit(request: Request) -> bool:
    if request.method == "OPTIONS":
        return True

    path = request.url.path
    exempt_prefixes = (
        "/static",
        "/achievements",
    )
    return path.startswith(exempt_prefixes)


def _request_id_from_header(value: str | None) -> str:
    if not value:
        return uuid.uuid4().hex

    request_id = value.strip()
    if not request_id or len(request_id) > MAX_REQUEST_ID_LENGTH:
        return uuid.uuid4().hex

    return request_id


def _header_safe_text(value: str) -> str:
    return quote(value, safe="")


app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.TRUSTED_HOSTS,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS or LOCAL_CORS_ORIGINS,
    allow_origin_regex=None if settings.BACKEND_CORS_ORIGINS else r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def enforce_school_subscription(request: Request, call_next):
    gate = await check_school_subscription_gate(request)
    if gate.blocked:
        return JSONResponse(
            status_code=402,
            content={
                "detail": gate.message,
                "error": "subscription_required",
                "school_id": gate.school_id,
                "reason": gate.reason,
            },
            headers={
                "X-Subscription-Status": "blocked",
                "X-Subscription-Reason": gate.reason,
            },
        )

    response = await call_next(request)
    if gate.warning:
        response.headers["X-Subscription-Status"] = "warning"
        response.headers["X-Subscription-Message"] = _header_safe_text(gate.message)
        response.headers["X-Subscription-Reason"] = gate.reason
        if gate.days_left is not None:
            response.headers["X-Subscription-Days-Left"] = str(gate.days_left)
    return response


@app.middleware("http")
async def protect_from_overload(request: Request, call_next):
    client_host = request.client.host if request.client else "unknown"
    if not _should_skip_rate_limit(request):
        now = time.monotonic()
        bucket = _request_buckets[client_host]
        window = settings.RATE_LIMIT_WINDOW_SECONDS

        while bucket and (now - bucket[0]) > window:
            bucket.popleft()

        if len(bucket) >= settings.RATE_LIMIT_MAX_REQUESTS:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests. Please slow down and try again shortly."
                },
            )

        bucket.append(now)

    try:
        await asyncio.wait_for(_request_semaphore.acquire(), timeout=2.5)
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=503,
            content={"detail": "Server is busy. Please try again in a moment."},
        )

    try:
        response = await call_next(request)
        return response
    finally:
        _request_semaphore.release()


@app.middleware("http")
async def add_request_context(request: Request, call_next):
    request_id = _request_id_from_header(request.headers.get("x-request-id"))
    request.state.request_id = request_id
    started_at = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        elapsed = time.perf_counter() - started_at
        request_logger.exception(
            "request_failed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "elapsed_seconds": round(elapsed, 4),
            },
        )
        raise

    elapsed = time.perf_counter() - started_at
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time"] = f"{elapsed:.4f}"
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    request_logger.info(
        "request_completed",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "elapsed_seconds": round(elapsed, 4),
        },
    )
    return response

# Include API routes
app.include_router(api_router, prefix=settings.API_V1_STR)

# Add WebSocket endpoint
app.add_api_websocket_route("/ws", websocket_endpoint)

# Only public achievement images are exposed as static files. Mounting BASE_DIR
# would make source files and local configuration reachable via /static.
app.mount(
    "/static/achievements",
    StaticFiles(directory=settings.ACHIEVEMENTS_DIR, html=False),
    name="static-achievements",
)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "8001")),
    )
