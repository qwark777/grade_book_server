from contextlib import asynccontextmanager
from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from app.core.config import settings
from app.db.connection import init_db
from app.api.main import api_router
from app.websocket.endpoints import websocket_endpoint


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    # Initialize database on startup
    await init_db()
    yield


# Create FastAPI application
app = FastAPI(
    title=settings.PROJECT_NAME,
    lifespan=lifespan
)

# Include API routes
app.include_router(api_router, prefix=settings.API_V1_STR)

# Add WebSocket endpoint
app.add_websocket_route("/ws", websocket_endpoint)

# Mount static files for profile photos
app.mount("/static", StaticFiles(directory=".", html=False), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)


