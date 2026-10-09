import json
from typing import Iterable, Optional

from app.db.connection import get_db_connection
from app.websocket.manager import ws_manager


def _normalize_route_params(route_params: Optional[dict]) -> str | None:
    if not route_params:
        return None
    return json.dumps(route_params, ensure_ascii=False)


async def create_notification(
    recipient_id: int,
    title: str,
    body: str,
    *,
    notification_type: str = "system",
    sender_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    route_name: str | None = None,
    route_params: dict | None = None,
) -> dict:
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO notifications (
                    recipient_id, sender_id, type, title, body,
                    entity_type, entity_id, route_name, route_params
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    recipient_id,
                    sender_id,
                    notification_type,
                    title,
                    body,
                    entity_type,
                    entity_id,
                    route_name,
                    _normalize_route_params(route_params),
                ),
            )
            notification_id = cursor.lastrowid
            await conn.commit()

            await cursor.execute(
                """
                SELECT id, recipient_id, sender_id, type, title, body,
                       entity_type, entity_id, route_name, route_params,
                       is_read, created_at, read_at
                FROM notifications
                WHERE id = %s
                """,
                (notification_id,),
            )
            row = await cursor.fetchone()
    finally:
        conn.close()

    payload = {
        "id": row["id"],
        "recipient_id": row["recipient_id"],
        "sender_id": row.get("sender_id"),
        "type": row["type"],
        "title": row["title"],
        "body": row["body"],
        "entity_type": row.get("entity_type"),
        "entity_id": row.get("entity_id"),
        "route_name": row.get("route_name"),
        "route_params": json.loads(row["route_params"])
        if row.get("route_params")
        else None,
        "is_read": bool(row.get("is_read")),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "read_at": row["read_at"].isoformat() if row.get("read_at") else None,
    }
    await ws_manager.send_to_user(recipient_id, {"type": "notification_new", "data": payload})
    return payload


async def notify_many(
    recipient_ids: Iterable[int],
    title: str,
    body: str,
    *,
    notification_type: str = "system",
    sender_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    route_name: str | None = None,
    route_params: dict | None = None,
) -> None:
    seen: set[int] = set()
    for recipient_id in recipient_ids:
        if recipient_id in seen:
            continue
        seen.add(recipient_id)
        await create_notification(
            recipient_id,
            title,
            body,
            notification_type=notification_type,
            sender_id=sender_id,
            entity_type=entity_type,
            entity_id=entity_id,
            route_name=route_name,
            route_params=route_params,
        )
