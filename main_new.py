"""
New main entry point for the refactored Grade Book server.
This file imports and runs the FastAPI application from the app module.
"""

from app.main import app

if __name__ == "__main__":
    import os
    import uvicorn
    uvicorn.run(app, host=os.getenv("APP_HOST", "127.0.0.1"), port=8001)

