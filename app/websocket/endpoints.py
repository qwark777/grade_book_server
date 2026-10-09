import logging
from datetime import UTC

from fastapi import WebSocket, WebSocketDisconnect
from app.core.config import settings
from app.core.security import verify_token
from app.db.user_operations import get_user
from app.db.connection import get_db_connection
from app.websocket.manager import ws_manager


logger = logging.getLogger(__name__)


async def _conversation_peer_ids(user_id: int) -> list[int]:
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT DISTINCT
                    IF(user1_id = %s, user2_id, user1_id) AS peer_id
                FROM conversations
                WHERE %s IN (user1_id, user2_id)
                """,
                (user_id, user_id),
            )
            return [
                int(
                    row["peer_id"]
                    if isinstance(row, dict)
                    else row[0]
                )
                for row in await cursor.fetchall()
            ]
    finally:
        conn.close()


async def _notify_presence(
    user_id: int,
    *,
    is_online: bool,
    last_seen_at: str | None = None,
) -> None:
    payload = {
        "type": "presence_changed",
        "data": {
            "user_id": user_id,
            "is_online": is_online,
            "last_seen_at": last_seen_at,
        },
    }
    for peer_id in await _conversation_peer_ids(user_id):
        await ws_manager.send_to_user(peer_id, payload)


async def _mark_user_offline(user_id: int) -> str:
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE users
                SET last_seen_at = UTC_TIMESTAMP()
                WHERE id = %s
                """,
                (user_id,),
            )
            await cursor.execute(
                "SELECT last_seen_at FROM users WHERE id = %s",
                (user_id,),
            )
            row = await cursor.fetchone()
            await conn.commit()
            value = (
                row["last_seen_at"]
                if isinstance(row, dict)
                else row[0]
            )
            return value.replace(tzinfo=UTC).isoformat()
    finally:
        conn.close()


async def _broadcast_presence_safely(
    user_id: int,
    *,
    is_online: bool,
    last_seen_at: str | None = None,
) -> None:
    try:
        await _notify_presence(
            user_id,
            is_online=is_online,
            last_seen_at=last_seen_at,
        )
    except Exception:
        logger.exception("Failed to broadcast presence for user %s", user_id)


def _token_from_subprotocols(ws: WebSocket) -> tuple[str | None, str | None]:
    raw = ws.headers.get("sec-websocket-protocol", "")
    protocols = [part.strip() for part in raw.split(",") if part.strip()]
    if len(protocols) >= 2 and protocols[0] == "gradebook.jwt":
        return protocols[1], "gradebook.jwt"
    return None, None


async def websocket_endpoint(ws: WebSocket):
    """WebSocket endpoint for real-time communication"""
    token, accepted_subprotocol = _token_from_subprotocols(ws)
    if not token and settings.ALLOW_WEBSOCKET_QUERY_TOKEN:
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

    if (
        await ws_manager.connection_count(user_id)
        >= settings.MAX_WEBSOCKETS_PER_USER
    ):
        await ws.close(code=4429)
        return

    await ws_manager.connect(user_id, ws, subprotocol=accepted_subprotocol)
    await _broadcast_presence_safely(user_id, is_online=True)
    try:
        # Keep connection alive by receiving messages
        while True:
            message = await ws.receive_text()
            if message == "ping":
                await ws.send_json({"type": "pong", "data": None})
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(user_id, ws)
        if await ws_manager.connection_count(user_id) == 0:
            try:
                last_seen_at = await _mark_user_offline(user_id)
            except Exception:
                logger.exception(
                    "Failed to update last seen for user %s",
                    user_id,
                )
            else:
                await _broadcast_presence_safely(
                    user_id,
                    is_online=False,
                    last_seen_at=last_seen_at,
                )
