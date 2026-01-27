from fastapi import WebSocket, WebSocketDisconnect
from app.core.security import verify_token
from app.db.user_operations import get_user
from app.websocket.manager import ws_manager


async def websocket_endpoint(ws: WebSocket):
    """WebSocket endpoint for real-time communication"""
    token = ws.query_params.get("token")
    if not token:
        await ws.close(code=4401)
        return
    
    try:
        payload = verify_token(token)
        username = payload.get("sub")
        user = await get_user(username)
        if not user:
            await ws.close(code=4403)
            return
        user_id = user.id
    except Exception:
        await ws.close(code=4401)
        return

    await ws_manager.connect(user_id, ws)
    try:
        # Keep connection alive by receiving messages
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(user_id, ws)


