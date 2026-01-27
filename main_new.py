"""
New main entry point for the refactored Grade Book server.
This file imports and runs the FastAPI application from the app module.
"""

from app.main import app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)


