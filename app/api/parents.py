"""API для роли родитель: привязка к ученикам, просмотр расписания и аналитики детей."""

import secrets
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException

from app.core.security import get_current_user, get_password_hash
from app.db.connection import get_db_connection
from app.models.user import User
from pydantic import BaseModel

router = APIRouter()


class CreateParentRequest(BaseModel):
    username: str
    password: str
    full_name: str
    student_ids: List[int] = []


@router.post("/create")
async def create_parent(
    payload: CreateParentRequest,
    current_user: User = Depends(get_current_user),
):
    """Создать родителя и привязать к ученикам. Только admin/owner."""
    role = getattr(current_user, "role", None) or current_user.get("role")
    if role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT id FROM users WHERE username = %s", (payload.username,))
            if await cursor.fetchone():
                raise HTTPException(status_code=400, detail="Username already exists")

            hashed = get_password_hash(payload.password)
            await cursor.execute(
                "INSERT INTO users (username, hashed_password, role) VALUES (%s, %s, 'parent')",
                (payload.username, hashed),
            )
            parent_id = cursor.lastrowid
            await cursor.execute(
                "INSERT INTO profiles (user_id, full_name, work_place, location, bio) VALUES (%s, %s, '', '', '')",
                (parent_id, payload.full_name),
            )

            for sid in payload.student_ids:
                await cursor.execute(
                    "SELECT 1 FROM users WHERE id = %s AND role = 'student'",
                    (sid,),
                )
                if await cursor.fetchone():
                    await cursor.execute(
                        "INSERT IGNORE INTO parent_students (parent_id, student_id) VALUES (%s, %s)",
                        (parent_id, sid),
                    )

            await conn.commit()
        return {"status": "ok", "parent_id": parent_id}
    finally:
        conn.close()


async def _get_parent_children(parent_id: int) -> List[dict]:
    """Получить список детей родителя с классами."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT ps.student_id, cs.class_id,
                       COALESCE(TRIM(BOTH '"' FROM p.full_name), u.username) as student_name,
                       c.name as class_name
                FROM parent_students ps
                JOIN users u ON u.id = ps.student_id AND u.role = 'student'
                LEFT JOIN profiles p ON p.user_id = u.id
                LEFT JOIN class_students cs ON cs.student_id = ps.student_id
                LEFT JOIN classes c ON c.id = cs.class_id AND COALESCE(c.is_archived, 0) = 0
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

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT id, role FROM users WHERE id IN (%s, %s)",
                (payload.parent_id, payload.student_id),
            )
            rows = await cursor.fetchall()
            users = {r["id"]: r for r in rows}
            if payload.parent_id not in users or users[payload.parent_id]["role"] != "parent":
                raise HTTPException(status_code=400, detail="Invalid parent")
            if payload.student_id not in users or users[payload.student_id]["role"] != "student":
                raise HTTPException(status_code=400, detail="Invalid student")

            await cursor.execute(
                "INSERT IGNORE INTO parent_students (parent_id, student_id) VALUES (%s, %s)",
                (payload.parent_id, payload.student_id),
            )
            await conn.commit()
        return {"status": "ok", "parent_id": payload.parent_id, "student_id": payload.student_id}
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

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM parent_students WHERE parent_id = %s AND student_id = %s",
                (parent_id, student_id),
            )
            await conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()
