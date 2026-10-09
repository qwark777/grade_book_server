from typing import Any, Optional

from fastapi import HTTPException

from app.api.parents import _get_parent_children
from app.core.entitlements import get_school_id_for_user
from app.db.connection import get_db_connection


OWNER_ROLES = {"owner", "superadmin", "root"}


def _value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _role(current_user: Any) -> str:
    return str(_value(current_user, "role", "") or "")


def _user_id(current_user: Any) -> Optional[int]:
    uid = _value(current_user, "id")
    try:
        return int(uid) if uid is not None else None
    except (TypeError, ValueError):
        return None


async def _student_in_school(cursor: Any, student_id: int, school_id: int) -> bool:
    await cursor.execute(
        """
        SELECT 1
        FROM class_students cs
        JOIN classes c ON c.id = cs.class_id
        WHERE cs.student_id = %s AND c.school_id = %s
        LIMIT 1
        """,
        (student_id, school_id),
    )
    return await cursor.fetchone() is not None


async def _teacher_has_student(cursor: Any, teacher_id: int, student_id: int) -> bool:
    await cursor.execute(
        """
        SELECT 1
        FROM class_students cs
        JOIN class_subject_teachers cst ON cst.class_id = cs.class_id
        WHERE cs.student_id = %s AND cst.teacher_id = %s
        LIMIT 1
        """,
        (student_id, teacher_id),
    )
    if await cursor.fetchone() is not None:
        return True

    await cursor.execute(
        """
        SELECT 1
        FROM class_students cs
        JOIN class_teachers ct ON ct.class_id = cs.class_id
        WHERE cs.student_id = %s AND ct.teacher_id = %s
        LIMIT 1
        """,
        (student_id, teacher_id),
    )
    return await cursor.fetchone() is not None


async def ensure_can_access_student(current_user: Any, student_id: int) -> None:
    role = _role(current_user)
    uid = _user_id(current_user)

    if role in OWNER_ROLES:
        return
    if uid is not None and uid == student_id:
        return

    if role == "parent" and uid is not None:
        children = await _get_parent_children(uid)
        if any(child.get("student_id") == student_id for child in children):
            return
        raise HTTPException(status_code=403, detail="Access denied")

    if role not in {"admin", "teacher"} or uid is None:
        raise HTTPException(status_code=403, detail="Access denied")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            if role == "admin":
                school_id = await get_school_id_for_user(current_user)
                if school_id is None:
                    raise HTTPException(status_code=403, detail="Access denied")
                allowed = await _student_in_school(cursor, student_id, school_id)
            else:
                allowed = await _teacher_has_student(cursor, uid, student_id)

        if not allowed:
            raise HTTPException(status_code=403, detail="Access denied")
    finally:
        conn.close()
