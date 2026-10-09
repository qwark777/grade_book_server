import json

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.security import get_current_user
from app.db.connection import get_db_connection

router = APIRouter()


def _serialize_notification(row: dict) -> dict:
    return {
        "id": row["id"],
        "recipient_id": row["recipient_id"],
        "sender_id": row.get("sender_id"),
        "type": row["type"],
        "title": row["title"],
        "body": row["body"],
        "entity_type": row.get("entity_type"),
        "entity_id": row.get("entity_id"),
        "route_name": row.get("route_name"),
        "route_params": json.loads(row["route_params"]) if row.get("route_params") else None,
        "is_read": bool(row.get("is_read")),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "read_at": row["read_at"].isoformat() if row.get("read_at") else None,
    }


@router.get("/notifications")
async def list_notifications(
    limit: int = Query(50, ge=1, le=200),
    unread_only: bool = Query(False),
    current_user=Depends(get_current_user),
):
    user_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            where = "WHERE n.recipient_id = %s"
            params: list[object] = [user_id]
            if unread_only:
                where += " AND n.is_read = 0"
            await cursor.execute(
                f"""
                SELECT n.id, n.recipient_id, n.sender_id, n.type, n.title, n.body,
                       n.entity_type, n.entity_id, n.route_name, n.route_params,
                       n.is_read, n.created_at, n.read_at
                FROM notifications n
                {where}
                ORDER BY n.created_at DESC, n.id DESC
                LIMIT %s
                """,
                (*params, limit),
            )
            rows = await cursor.fetchall()
            return [_serialize_notification(row) for row in rows]
    finally:
        conn.close()


@router.get("/notifications/unread-count")
async def get_notifications_unread_count(current_user=Depends(get_current_user)):
    user_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT COUNT(*) AS unread_count FROM notifications WHERE recipient_id = %s AND is_read = 0",
                (user_id,),
            )
            row = await cursor.fetchone()
            return {"unread_count": int(row["unread_count"] if row else 0)}
    finally:
        conn.close()


@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: int, current_user=Depends(get_current_user)):
    user_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE notifications
                SET is_read = 1, read_at = COALESCE(read_at, CURRENT_TIMESTAMP)
                WHERE id = %s AND recipient_id = %s
                """,
                (notification_id, user_id),
            )
            await conn.commit()
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Notification not found")
            return {"status": "ok"}
    finally:
        conn.close()


@router.post("/notifications/read-all")
async def mark_all_notifications_read(current_user=Depends(get_current_user)):
    user_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE notifications
                SET is_read = 1, read_at = COALESCE(read_at, CURRENT_TIMESTAMP)
                WHERE recipient_id = %s AND is_read = 0
                """,
                (user_id,),
            )
            await conn.commit()
            return {"status": "ok", "updated": cursor.rowcount}
    finally:
        conn.close()
