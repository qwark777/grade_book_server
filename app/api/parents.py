"""API для роли родитель: привязка к ученикам, просмотр расписания и аналитики детей."""

import secrets
import re
from collections import defaultdict
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException

from app.core.security import get_current_user, get_password_hash, validate_password_strength
from app.core.entitlements import get_school_id_for_user
from app.core.subscription_limits import register_school_user
from app.db.connection import get_db_connection
from app.models.user import User
from pydantic import BaseModel, Field

router = APIRouter()


def _user_id_and_role(current_user) -> tuple[int | None, str]:
    if isinstance(current_user, dict):
        return current_user.get("id"), current_user.get("role", "")
    return getattr(current_user, "id", None), getattr(current_user, "role", "")


def _is_owner(role: str) -> bool:
    return role in ("owner", "superadmin", "root")


async def _admin_school_id(current_user) -> int | None:
    _, role = _user_id_and_role(current_user)
    if _is_owner(role):
        return None
    school_id = await get_school_id_for_user(current_user)
    if school_id is None:
        raise HTTPException(status_code=403, detail="No school access")
    return int(school_id)


async def _ensure_student_school(
    cursor,
    student_id: int,
    school_id: int | None,
) -> int | None:
    await cursor.execute(
        """
        SELECT c.school_id
        FROM users u
        JOIN class_students cs ON cs.student_id = u.id
        JOIN classes c ON c.id = cs.class_id
        WHERE u.id = %s AND u.role = 'student'
          AND (%s IS NULL OR c.school_id = %s)
        LIMIT 1
        """,
        (student_id, school_id, school_id),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="Нет доступа к ученику")
    return int(row["school_id"]) if row.get("school_id") is not None else None


async def _ensure_parent_school(
    cursor,
    parent_id: int,
    school_id: int | None,
) -> None:
    await cursor.execute(
        """
        SELECT 1
        FROM users u
        LEFT JOIN school_users su ON su.user_id = u.id
        LEFT JOIN parent_students ps ON ps.parent_id = u.id
        LEFT JOIN class_students cs ON cs.student_id = ps.student_id
        LEFT JOIN classes c ON c.id = cs.class_id
        WHERE u.id = %s AND u.role = 'parent'
          AND (%s IS NULL OR su.school_id = %s OR c.school_id = %s)
        LIMIT 1
        """,
        (parent_id, school_id, school_id, school_id),
    )
    if not await cursor.fetchone():
        raise HTTPException(status_code=403, detail="Нет доступа к родителю")


class CreateParentRequest(BaseModel):
    username: str
    password: str
    full_name: str
    email: Optional[str] = None
    student_ids: List[int] = Field(default_factory=list)


def _clean_email(value: str | None) -> str:
    email = (value or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email обязателен")
    if len(email) > 320 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=400, detail="Некорректный email")
    return email


@router.post("/create")
async def create_parent(
    payload: CreateParentRequest,
    current_user: User = Depends(get_current_user),
):
    """Создать родителя и привязать к ученикам. Только admin/owner."""
    role = getattr(current_user, "role", None) or current_user.get("role")
    if role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        validate_password_strength(payload.password, payload.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    clean_email = _clean_email(payload.email)
    school_id = await _admin_school_id(current_user)

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            student_school_ids = set()
            for student_id in set(payload.student_ids):
                student_school_id = await _ensure_student_school(
                    cursor,
                    student_id,
                    school_id,
                )
                if student_school_id is not None:
                    student_school_ids.add(student_school_id)
            if school_id is None and len(student_school_ids) > 1:
                raise HTTPException(
                    status_code=400,
                    detail="Нельзя привязать родителя к ученикам разных школ",
                )
            resolved_school_id = school_id or next(iter(student_school_ids), None)

            await cursor.execute(
                "SELECT id FROM users WHERE username = %s OR email = %s",
                (payload.username, clean_email),
            )
            if await cursor.fetchone():
                raise HTTPException(status_code=400, detail="Логин или email уже используется")

            hashed = get_password_hash(payload.password)
            await cursor.execute(
                "INSERT INTO users (username, hashed_password, role, email) VALUES (%s, %s, 'parent', %s)",
                (payload.username, hashed, clean_email),
            )
            parent_id = cursor.lastrowid
            await cursor.execute(
                "INSERT INTO profiles (user_id, full_name, work_place, location, bio) VALUES (%s, %s, '', '', '')",
                (parent_id, payload.full_name),
            )
            if resolved_school_id is not None:
                await register_school_user(
                    cursor,
                    school_id=resolved_school_id,
                    user_id=parent_id,
                    role="parent",
                )

            for sid in payload.student_ids:
                await cursor.execute(
                    "INSERT IGNORE INTO parent_students (parent_id, student_id) VALUES (%s, %s)",
                    (parent_id, sid),
                )

            await conn.commit()
        return {"status": "ok", "parent_id": parent_id}
    except Exception:
        await conn.rollback()
        raise
    finally:
        conn.close()


async def _get_parent_children(parent_id: int) -> List[dict]:
    """Получить список детей родителя с классами."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT ps.student_id, cc.class_id,
                       COALESCE(TRIM(BOTH '"' FROM p.full_name), u.username) as student_name,
                       c.name as class_name
                FROM parent_students ps
                JOIN users u ON u.id = ps.student_id AND u.role = 'student'
                LEFT JOIN profiles p ON p.user_id = u.id
                LEFT JOIN (
                    SELECT cs1.student_id, cs1.class_id
                    FROM class_students cs1
                    JOIN classes c1 ON c1.id = cs1.class_id
                    WHERE COALESCE(c1.is_archived, 0) = 0
                      AND cs1.class_id = (
                          SELECT MAX(cs2.class_id)
                          FROM class_students cs2
                          JOIN classes c2 ON c2.id = cs2.class_id
                          WHERE cs2.student_id = cs1.student_id
                            AND COALESCE(c2.is_archived, 0) = 0
                      )
                ) cc ON cc.student_id = ps.student_id
                LEFT JOIN classes c ON c.id = cc.class_id
                WHERE ps.parent_id = %s
            """, (parent_id,))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
    finally:
        conn.close()


def _is_parent_of(current_user: User, student_id: int, children: List[dict]) -> bool:
    return any(c.get("student_id") == student_id for c in children)


@router.get("/my-children")
async def get_my_children(current_user: User = Depends(get_current_user)):
    """Список детей родителя (student_id, class_id, student_name, class_name)."""
    if current_user.role != "parent":
        raise HTTPException(status_code=403, detail="Only parents can access")
    uid = current_user.id if hasattr(current_user, "id") else current_user["id"]
    children = await _get_parent_children(uid)
    return {"children": children}


@router.get("/child/{student_id}/class")
async def get_child_class(
    student_id: int,
    current_user: User = Depends(get_current_user),
):
    """Класс ребёнка (для расписания). Родитель должен быть привязан."""
    if current_user.role != "parent":
        raise HTTPException(status_code=403, detail="Only parents can access")
    uid = current_user.id if hasattr(current_user, "id") else current_user["id"]
    children = await _get_parent_children(uid)
    for c in children:
        if c.get("student_id") == student_id:
            return {
                "student_id": student_id,
                "class_id": c.get("class_id"),
                "student_name": c.get("student_name"),
                "class_name": c.get("class_name"),
            }
    raise HTTPException(status_code=403, detail="Not your child")


@router.get("/class/{class_id}/contacts")
async def get_class_parent_contacts(
    class_id: int,
    current_user: User = Depends(get_current_user),
):
    """Список родителей учеников класса. Teacher/admin/owner only."""
    role = getattr(current_user, "role", None) or current_user.get("role")
    uid = current_user.id if hasattr(current_user, "id") else current_user["id"]
    if role not in ("teacher", "admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            if role == "teacher":
                await cursor.execute(
                    """
                    SELECT 1
                    FROM class_subject_teachers
                    WHERE class_id = %s AND teacher_id = %s
                    LIMIT 1
                    """,
                    (class_id, uid),
                )
                allowed = await cursor.fetchone()
                if not allowed:
                    await cursor.execute(
                        """
                        SELECT 1
                        FROM class_teachers
                        WHERE class_id = %s AND teacher_id = %s
                        LIMIT 1
                        """,
                        (class_id, uid),
                    )
                    allowed = await cursor.fetchone()
                if not allowed:
                    raise HTTPException(status_code=403, detail="Forbidden")
            elif role == "admin":
                school_id = await _admin_school_id(current_user)
                await cursor.execute(
                    "SELECT 1 FROM classes WHERE id = %s AND school_id = %s",
                    (class_id, school_id),
                )
                if not await cursor.fetchone():
                    raise HTTPException(status_code=403, detail="Forbidden")

            await cursor.execute(
                """
                SELECT
                    pu.id AS parent_id,
                    COALESCE(NULLIF(TRIM(BOTH '"' FROM pp.full_name), ''), pu.username) AS parent_name,
                    su.id AS student_id,
                    COALESCE(NULLIF(TRIM(BOTH '"' FROM sp.full_name), ''), su.username) AS student_name
                FROM class_students cs
                JOIN parent_students ps ON ps.student_id = cs.student_id
                JOIN users pu ON pu.id = ps.parent_id AND pu.role = 'parent'
                LEFT JOIN profiles pp ON pp.user_id = pu.id
                JOIN users su ON su.id = cs.student_id AND su.role = 'student'
                LEFT JOIN profiles sp ON sp.user_id = su.id
                WHERE cs.class_id = %s
                ORDER BY parent_name, student_name
                """,
                (class_id,),
            )
            rows = await cursor.fetchall()

        grouped = defaultdict(lambda: {"student_names": []})
        for row in rows:
            parent_id = row["parent_id"]
            grouped[parent_id]["id"] = parent_id
            grouped[parent_id]["full_name"] = row["parent_name"]
            grouped[parent_id]["student_names"].append(row["student_name"])

        parents = list(grouped.values())
        for parent in parents:
            parent["student_names"] = sorted(set(parent["student_names"]))

        parents.sort(key=lambda item: item["full_name"])
        return {"parents": parents}
    finally:
        conn.close()


class BindParentRequest(BaseModel):
    parent_id: int
    student_id: int


@router.post("/bind")
async def bind_parent_to_student(
    payload: BindParentRequest,
    current_user: User = Depends(get_current_user),
):
    """Привязать родителя к ученику. Только admin/owner."""
    role = getattr(current_user, "role", None) or current_user.get("role")
    if role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")
    school_id = await _admin_school_id(current_user)

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await _ensure_student_school(
                cursor,
                payload.student_id,
                school_id,
            )
            await _ensure_parent_school(cursor, payload.parent_id, school_id)

            await cursor.execute(
                "INSERT IGNORE INTO parent_students (parent_id, student_id) VALUES (%s, %s)",
                (payload.parent_id, payload.student_id),
            )
            await conn.commit()
        return {"status": "ok", "parent_id": payload.parent_id, "student_id": payload.student_id}
    except Exception:
        await conn.rollback()
        raise
    finally:
        conn.close()


@router.delete("/bind")
async def unbind_parent_from_student(
    parent_id: int,
    student_id: int,
    current_user: User = Depends(get_current_user),
):
    """Отвязать родителя от ученика. Только admin/owner."""
    role = getattr(current_user, "role", None) or current_user.get("role")
    if role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")
    school_id = await _admin_school_id(current_user)

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await _ensure_student_school(cursor, student_id, school_id)
            await _ensure_parent_school(cursor, parent_id, school_id)
            await cursor.execute(
                "DELETE FROM parent_students WHERE parent_id = %s AND student_id = %s",
                (parent_id, student_id),
            )
            await conn.commit()
        return {"status": "ok"}
    except Exception:
        await conn.rollback()
        raise
    finally:
        conn.close()
