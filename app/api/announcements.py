from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.core.entitlements import get_school_id_for_user
from app.core.security import get_current_user
from app.db.connection import get_db_connection

router = APIRouter()


def _value(row, key: str, index: int, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    if index < len(row):
        return row[index]
    return default


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


async def _ensure_announcements_table(cur) -> None:
    await cur.execute("""
        CREATE TABLE IF NOT EXISTS announcements (
            id INT AUTO_INCREMENT PRIMARY KEY,
            school_id INT NULL,
            title VARCHAR(255) NOT NULL,
            content TEXT NOT NULL,
            target_type VARCHAR(50) DEFAULT 'all',
            target_class_id INT NULL,
            created_by INT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_school (school_id),
            INDEX idx_created (created_at)
        )
    """)


def _user_id(current_user) -> Optional[int]:
    return (
        current_user.get("id")
        if isinstance(current_user, dict)
        else getattr(current_user, "id", None)
    )


def _role(current_user) -> str:
    return (
        current_user.get("role", "")
        if isinstance(current_user, dict)
        else getattr(current_user, "role", "")
    )


async def _user_class_ids(cur, current_user) -> list[int]:
    uid = _user_id(current_user)
    if uid is None:
        return []

    role = _role(current_user)
    if role == "student":
        await cur.execute(
            "SELECT class_id FROM class_students WHERE student_id = %s",
            (uid,),
        )
    elif role == "teacher":
        await cur.execute(
            "SELECT class_id FROM class_teachers WHERE teacher_id = %s",
            (uid,),
        )
    else:
        return []

    rows = await cur.fetchall()
    return [int(_value(row, "class_id", 0)) for row in rows if _value(row, "class_id", 0)]


@router.get("/announcements")
async def list_visible_announcements(
    limit: int = Query(30, ge=1, le=100),
    current_user=Depends(get_current_user),
):
    school_id = await get_school_id_for_user(current_user)
    if school_id is None:
        return []

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await _ensure_announcements_table(cur)
            class_ids = await _user_class_ids(cur, current_user)
            params = [school_id]
            class_filter = ""
            if class_ids:
                placeholders = ",".join(["%s"] * len(class_ids))
                class_filter = f" OR a.target_class_id IN ({placeholders})"
                params.extend(class_ids)

            params.append(limit)
            await cur.execute(
                f"""
                SELECT a.id, a.title, a.content, a.target_type, a.target_class_id,
                       a.created_at, c.name AS class_name
                FROM announcements a
                LEFT JOIN classes c ON c.id = a.target_class_id
                WHERE a.school_id = %s
                  AND (a.target_type = 'all' OR a.target_class_id IS NULL{class_filter})
                ORDER BY a.created_at DESC
                LIMIT %s
                """,
                tuple(params),
            )
            rows = await cur.fetchall()
            return [
                {
                    "id": _value(row, "id", 0),
                    "title": _value(row, "title", 1),
                    "content": _value(row, "content", 2),
                    "target_type": _value(row, "target_type", 3),
                    "target_class_id": _value(row, "target_class_id", 4),
                    "class_name": _value(row, "class_name", 6),
                    "created_at": _iso(_value(row, "created_at", 5)),
                }
                for row in rows
            ]
    finally:
        conn.close()
