from typing import List
import aiomysql
from datetime import UTC
import json
import os
import re
import secrets
import uuid
from fastapi import APIRouter, Depends, HTTPException, Form, UploadFile, File

from app.core.security import get_current_user
from app.core.config import settings
from app.core.file_uploads import (
    IMAGE_EXTS,
    MAX_IMAGE_BYTES,
    save_upload_limited,
    validate_avatar_dimensions,
    validate_upload_metadata,
)
from app.db.connection import get_db_connection
from app.websocket.manager import ws_manager
from app.models.user import UserResponse, UserRole
from app.core.security import get_password_hash, validate_password_strength
from app.core.entitlements import get_school_id_for_user, get_admin_info
from app.core.subscription_limits import ensure_school_capacity, register_school_user

router = APIRouter()


def _user_id_and_role(current_user) -> tuple:
    """Extract id and role from current_user (dict or object)."""
    uid = current_user.get("id") if isinstance(current_user, dict) else getattr(current_user, "id", None)
    r = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
    return uid, r


def _clean_profile_text(value: str | None) -> str:
    return (value or "").strip().strip('"“”«»').strip()


def _validate_admin_password(password: str, username: str) -> None:
    try:
        validate_password_strength(password, username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _clean_email(value: str | None) -> str | None:
    email = (value or "").strip().lower()
    if not email:
        return None
    if len(email) > 320 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=400, detail="Некорректный email")
    return email


def _required_email(value: str | None) -> str:
    email = _clean_email(value)
    if not email:
        raise HTTPException(status_code=400, detail="Email обязателен")
    return email


def _is_application_owner(role: str | None) -> bool:
    return role in ("owner", "superadmin", "root")


async def _get_user_school_ids(cursor, user_id: int) -> set[int]:
    await cursor.execute(
        """
        SELECT DISTINCT school_id
        FROM (
            SELECT school_id
            FROM school_admins
            WHERE admin_user_id = %s
            UNION
            SELECT c.school_id
            FROM class_students cs
            JOIN classes c ON c.id = cs.class_id
            WHERE cs.student_id = %s AND c.school_id IS NOT NULL
            UNION
            SELECT c.school_id
            FROM class_teachers ct
            JOIN classes c ON c.id = ct.class_id
            WHERE ct.teacher_id = %s AND c.school_id IS NOT NULL
            UNION
            SELECT c.school_id
            FROM class_subject_teachers cst
            JOIN classes c ON c.id = cst.class_id
            WHERE cst.teacher_id = %s AND c.school_id IS NOT NULL
            UNION
            SELECT school_id
            FROM school_users
            WHERE user_id = %s AND school_id IS NOT NULL
            UNION
            SELECT c.school_id
            FROM parent_students ps
            JOIN class_students cs ON cs.student_id = ps.student_id
            JOIN classes c ON c.id = cs.class_id
            WHERE ps.parent_id = %s AND c.school_id IS NOT NULL
        ) scoped_schools
        WHERE school_id IS NOT NULL
        """,
        (user_id, user_id, user_id, user_id, user_id, user_id),
    )
    rows = await cursor.fetchall()
    result = set()
    for row in rows:
        school_id = row.get("school_id") if isinstance(row, dict) else row[0]
        if school_id is not None:
            result.add(int(school_id))
    return result


async def _ensure_can_read_user(
    cursor,
    current_user,
    target_user_id: int,
    target_role: str | None = None,
) -> None:
    current_user_id, current_role = _user_id_and_role(current_user)
    if (
        current_user_id == target_user_id
        or _is_application_owner(current_role)
        or _is_application_owner(target_role)
    ):
        return

    if current_user_id is None or current_role is None:
        raise HTTPException(status_code=403, detail="Forbidden")

    class _User:
        def __init__(self, id_, role_):
            self.id = id_
            self.role = role_

    current_school_id = await get_school_id_for_user(_User(current_user_id, current_role))
    if current_school_id is None:
        raise HTTPException(status_code=403, detail="No school access")

    target_school_ids = await _get_user_school_ids(cursor, target_user_id)
    if int(current_school_id) not in target_school_ids:
        raise HTTPException(status_code=403, detail="Forbidden")


async def _ensure_can_manage_user(
    cursor,
    current_user,
    target_user_id: int,
    target_role: str,
) -> None:
    _, current_role = _user_id_and_role(current_user)
    if _is_application_owner(current_role):
        return
    if current_role != "admin" or _is_application_owner(target_role):
        raise HTTPException(status_code=403, detail="Forbidden")

    current_school_id = await get_school_id_for_user(current_user)
    if current_school_id is None:
        raise HTTPException(status_code=403, detail="No school access")
    target_school_ids = await _get_user_school_ids(cursor, target_user_id)
    if int(current_school_id) not in target_school_ids:
        raise HTTPException(status_code=403, detail="Forbidden")


def _numeric_suffix(digits: int) -> str:
    lower = 10 ** (digits - 1)
    upper = 10**digits
    return str(lower + secrets.randbelow(upper - lower))


@router.get("/users/me/school-id")
async def get_my_school_id(current_user=Depends(get_current_user)):
    """Return school_id for current user (admin/teacher/student). Used by CRM and user lists."""
    class _User:
        def __init__(self, id_, role_):
            self.id = id_
            self.role = role_
    uid, urole = _user_id_and_role(current_user)
    if not uid or not urole:
        return {"school_id": None}
    school_id = await get_school_id_for_user(_User(uid, urole))
    return {"school_id": school_id}


@router.get("/users/me/admin-info")
async def get_my_admin_info(current_user=Depends(get_current_user)):
    """Return admin info for current user: school_id, is_main_admin, permissions. Only for admins."""
    class _User:
        def __init__(self, id_, role_):
            self.id = id_
            self.role = role_
    uid, urole = _user_id_and_role(current_user)
    if not uid or urole not in ("admin", "owner", "superadmin", "root"):
        return {"school_id": None, "is_main_admin": False, "permissions": []}
    user = _User(uid, urole)
    school_id, is_main, perms = await get_admin_info(user)
    return {"school_id": school_id, "is_main_admin": is_main, "permissions": perms}


@router.get("/users/chat-contacts")
async def get_chat_contacts(current_user=Depends(get_current_user)):
    """Users available for a direct chat, including owner/admin contacts."""
    uid, urole = _user_id_and_role(current_user)
    if uid is None or urole is None:
        raise HTTPException(status_code=401, detail="Требуется авторизация")

    class _User:
        def __init__(self, id_, role_):
            self.id = id_
            self.role = role_

    school_id = await get_school_id_for_user(_User(uid, urole))
    conn = await get_db_connection()
    try:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            if _is_application_owner(urole):
                await cursor.execute(
                    """
                    SELECT u.id,
                           COALESCE(NULLIF(TRIM(BOTH '"' FROM p.full_name), ''), u.username) AS name,
                           u.role,
                           COALESCE(p.photo_url, CONCAT('/api/v1/profile_photo/', u.id)) AS photo_url
                    FROM users u
                    LEFT JOIN profiles p ON p.user_id = u.id
                    WHERE u.id <> %s
                    ORDER BY FIELD(u.role, 'admin', 'teacher', 'student', 'parent', 'tutor'), name
                    """,
                    (uid,),
                )
            elif school_id is not None:
                await cursor.execute(
                    """
                    SELECT DISTINCT u.id,
                           COALESCE(NULLIF(TRIM(BOTH '"' FROM p.full_name), ''), u.username) AS name,
                           u.role,
                           COALESCE(p.photo_url, CONCAT('/api/v1/profile_photo/', u.id)) AS photo_url
                    FROM users u
                    LEFT JOIN profiles p ON p.user_id = u.id
                    LEFT JOIN school_users su ON su.user_id = u.id
                    LEFT JOIN school_admins sa ON sa.admin_user_id = u.id
                    LEFT JOIN class_students cs ON cs.student_id = u.id
                    LEFT JOIN classes c_student ON c_student.id = cs.class_id
                    LEFT JOIN class_teachers ct ON ct.teacher_id = u.id
                    LEFT JOIN classes c_teacher ON c_teacher.id = ct.class_id
                    LEFT JOIN parent_students ps_contact ON ps_contact.parent_id = u.id
                    LEFT JOIN class_students cs_parent ON cs_parent.student_id = ps_contact.student_id
                    LEFT JOIN classes c_parent ON c_parent.id = cs_parent.class_id
                    WHERE u.id <> %s
                      AND (
                        u.role IN ('owner', 'superadmin', 'root')
                        OR su.school_id = %s
                        OR sa.school_id = %s
                        OR c_student.school_id = %s
                        OR c_teacher.school_id = %s
                        OR c_parent.school_id = %s
                      )
                    ORDER BY FIELD(u.role, 'owner', 'admin', 'teacher', 'student', 'parent', 'tutor'), name
                    """,
                    (
                        uid,
                        school_id,
                        school_id,
                        school_id,
                        school_id,
                        school_id,
                    ),
                )
            else:
                return []
            rows = await cursor.fetchall()
            role_labels = {
                "owner": "Владелец приложения",
                "admin": "Администратор школы",
                "teacher": "Учитель",
                "student": "Ученик",
                "parent": "Родитель",
                "tutor": "Репетитор",
            }
            return [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "subline": role_labels.get(row["role"], row["role"]),
                    "role": row["role"],
                    "photo_url": row["photo_url"],
                }
                for row in rows
            ]
    finally:
        conn.close()


@router.get("/users/all", response_model=List[UserResponse])
async def get_all_users(
    include: str,
    role: UserRole,
    page: int = 1,
    per_page: int = 20,
    current_user=Depends(get_current_user)
):
    """Get all users with pagination and filtering. Returns only users from the same school."""
    class _User:
        def __init__(self, id_, role_):
            self.id = id_
            self.role = role_

    uid, urole = _user_id_and_role(current_user)
    school_id = await get_school_id_for_user(_User(uid, urole)) if uid and urole else None
    if school_id is None and not _is_application_owner(urole):
        raise HTTPException(status_code=403, detail="No school access")

    conn = await get_db_connection()
    try:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            offset = (page - 1) * per_page

            if role == UserRole.student:
                if include:
                    if school_id is not None:
                        await cursor.execute("""
                            SELECT DISTINCT u.id, TRIM(BOTH '"' FROM p.full_name) AS full_name,
                                   (SELECT c.name FROM classes c
                                    JOIN class_students cs ON c.id = cs.class_id
                                    WHERE cs.student_id = u.id LIMIT 1) as class_name,
                                   COALESCE(p.photo_url, CONCAT('/api/v1/profile_photo/', u.id)) AS photo_url
                            FROM users u
                            JOIN profiles p ON u.id = p.user_id
                            JOIN class_students cs ON cs.student_id = u.id
                            JOIN classes c ON c.id = cs.class_id AND c.school_id = %s
                            WHERE u.role = 'student' AND LOCATE(%s, p.full_name) != 0
                            LIMIT %s OFFSET %s
                        """, (school_id, include, per_page, offset))
                    else:
                        await cursor.execute("""
                            SELECT u.id, TRIM(BOTH '"' FROM p.full_name) AS full_name,
                                   (SELECT c.name FROM classes c
                                    JOIN class_students cs ON c.id = cs.class_id
                                    WHERE cs.student_id = u.id LIMIT 1) as class_name,
                                   COALESCE(p.photo_url, CONCAT('/api/v1/profile_photo/', u.id)) AS photo_url
                            FROM users u
                            JOIN profiles p ON u.id = p.user_id
                            WHERE u.role = 'student' and LOCATE(%s, p.full_name) != 0
                            LIMIT %s OFFSET %s
                        """, (include, per_page, offset))
                else:
                    if school_id is not None:
                        await cursor.execute("""
                            SELECT DISTINCT u.id, TRIM(BOTH '"' FROM p.full_name) AS full_name,
                                   (SELECT c.name FROM classes c
                                    JOIN class_students cs ON c.id = cs.class_id
                                    WHERE cs.student_id = u.id LIMIT 1) as class_name,
                                   COALESCE(p.photo_url, CONCAT('/api/v1/profile_photo/', u.id)) AS photo_url
                            FROM users u
                            JOIN profiles p ON u.id = p.user_id
                            JOIN class_students cs ON cs.student_id = u.id
                            JOIN classes c ON c.id = cs.class_id AND c.school_id = %s
                            WHERE u.role = 'student'
                            LIMIT %s OFFSET %s
                        """, (school_id, per_page, offset))
                    else:
                        await cursor.execute("""
                            SELECT u.id, TRIM(BOTH '"' FROM p.full_name) AS full_name,
                                   (SELECT c.name FROM classes c
                                    JOIN class_students cs ON c.id = cs.class_id
                                    WHERE cs.student_id = u.id LIMIT 1) as class_name,
                                   COALESCE(p.photo_url, CONCAT('/api/v1/profile_photo/', u.id)) AS photo_url
                            FROM users u
                            JOIN profiles p ON u.id = p.user_id
                            WHERE u.role = 'student'
                            LIMIT %s OFFSET %s
                        """, (per_page, offset))
            else:
                if include:
                    if school_id is not None:
                        await cursor.execute("""
                            SELECT DISTINCT u.id, TRIM(BOTH '"' FROM p.full_name) AS full_name,
                                   NULL as class_name,
                                   (SELECT GROUP_CONCAT(DISTINCT s.name ORDER BY s.name SEPARATOR ', ')
                                    FROM subjects s
                                    WHERE EXISTS (
                                        SELECT 1 FROM teacher_subjects ts
                                        WHERE ts.teacher_id = u.id AND ts.subject_id = s.id
                                    ) OR EXISTS (
                                        SELECT 1 FROM class_subject_teachers cst
                                        JOIN classes c ON c.id = cst.class_id AND c.school_id = su.school_id
                                        WHERE cst.teacher_id = u.id AND cst.subject_id = s.id
                                    )) as subject,
                                   (SELECT GROUP_CONCAT(DISTINCT c.name ORDER BY c.name SEPARATOR ', ')
                                    FROM classes c
                                    WHERE c.school_id = su.school_id
                                      AND (EXISTS (
                                          SELECT 1 FROM class_teachers ct
                                          WHERE ct.class_id = c.id AND ct.teacher_id = u.id
                                      ) OR EXISTS (
                                          SELECT 1 FROM class_subject_teachers cst
                                          WHERE cst.class_id = c.id AND cst.teacher_id = u.id
                                      ))) as classes,
                                   COALESCE(p.photo_url, CONCAT('/api/v1/profile_photo/', u.id)) AS photo_url
                            FROM users u
                            JOIN profiles p ON u.id = p.user_id
                            JOIN school_users su
                              ON su.user_id = u.id AND su.school_id = %s AND su.role = 'teacher'
                            WHERE u.role = 'teacher' AND LOCATE(%s, p.full_name) != 0
                            LIMIT %s OFFSET %s
                        """, (school_id, include, per_page, offset))
                    else:
                        await cursor.execute("""
                            SELECT u.id, TRIM(BOTH '"' FROM p.full_name) AS full_name,
                                   NULL as class_name,
                                   (SELECT GROUP_CONCAT(DISTINCT s.name ORDER BY s.name SEPARATOR ', ')
                                    FROM subjects s
                                    WHERE EXISTS (
                                        SELECT 1 FROM teacher_subjects ts
                                        WHERE ts.teacher_id = u.id AND ts.subject_id = s.id
                                    ) OR EXISTS (
                                        SELECT 1 FROM class_subject_teachers cst
                                        WHERE cst.teacher_id = u.id AND cst.subject_id = s.id
                                    )) as subject,
                                   (SELECT GROUP_CONCAT(DISTINCT c.name ORDER BY c.name SEPARATOR ', ')
                                    FROM classes c
                                    WHERE EXISTS (
                                        SELECT 1 FROM class_teachers ct
                                        WHERE ct.class_id = c.id AND ct.teacher_id = u.id
                                    ) OR EXISTS (
                                        SELECT 1 FROM class_subject_teachers cst
                                        WHERE cst.class_id = c.id AND cst.teacher_id = u.id
                                    )) as classes,
                                   COALESCE(p.photo_url, CONCAT('/api/v1/profile_photo/', u.id)) AS photo_url
                            FROM users u
                            JOIN profiles p ON u.id = p.user_id
                            WHERE u.role = 'teacher' and LOCATE(%s, p.full_name) != 0
                            LIMIT %s OFFSET %s
                        """, (include, per_page, offset))
                else:
                    if school_id is not None:
                        await cursor.execute("""
                            SELECT DISTINCT u.id, TRIM(BOTH '"' FROM p.full_name) AS full_name,
                                   NULL as class_name,
                                   (SELECT GROUP_CONCAT(DISTINCT s.name ORDER BY s.name SEPARATOR ', ')
                                    FROM subjects s
                                    WHERE EXISTS (
                                        SELECT 1 FROM teacher_subjects ts
                                        WHERE ts.teacher_id = u.id AND ts.subject_id = s.id
                                    ) OR EXISTS (
                                        SELECT 1 FROM class_subject_teachers cst
                                        JOIN classes c ON c.id = cst.class_id AND c.school_id = su.school_id
                                        WHERE cst.teacher_id = u.id AND cst.subject_id = s.id
                                    )) as subject,
                                   (SELECT GROUP_CONCAT(DISTINCT c.name ORDER BY c.name SEPARATOR ', ')
                                    FROM classes c
                                    WHERE c.school_id = su.school_id
                                      AND (EXISTS (
                                          SELECT 1 FROM class_teachers ct
                                          WHERE ct.class_id = c.id AND ct.teacher_id = u.id
                                      ) OR EXISTS (
                                          SELECT 1 FROM class_subject_teachers cst
                                          WHERE cst.class_id = c.id AND cst.teacher_id = u.id
                                      ))) as classes,
                                   COALESCE(p.photo_url, CONCAT('/api/v1/profile_photo/', u.id)) AS photo_url
                            FROM users u
                            JOIN profiles p ON u.id = p.user_id
                            JOIN school_users su
                              ON su.user_id = u.id AND su.school_id = %s AND su.role = 'teacher'
                            WHERE u.role = 'teacher'
                            LIMIT %s OFFSET %s
                        """, (school_id, per_page, offset))
                    else:
                        await cursor.execute("""
                            SELECT u.id, TRIM(BOTH '"' FROM p.full_name) AS full_name,
                                   NULL as class_name,
                                   (SELECT GROUP_CONCAT(DISTINCT s.name ORDER BY s.name SEPARATOR ', ')
                                    FROM subjects s
                                    WHERE EXISTS (
                                        SELECT 1 FROM teacher_subjects ts
                                        WHERE ts.teacher_id = u.id AND ts.subject_id = s.id
                                    ) OR EXISTS (
                                        SELECT 1 FROM class_subject_teachers cst
                                        WHERE cst.teacher_id = u.id AND cst.subject_id = s.id
                                    )) as subject,
                                   (SELECT GROUP_CONCAT(DISTINCT c.name ORDER BY c.name SEPARATOR ', ')
                                    FROM classes c
                                    WHERE EXISTS (
                                        SELECT 1 FROM class_teachers ct
                                        WHERE ct.class_id = c.id AND ct.teacher_id = u.id
                                    ) OR EXISTS (
                                        SELECT 1 FROM class_subject_teachers cst
                                        WHERE cst.class_id = c.id AND cst.teacher_id = u.id
                                    )) as classes,
                                   COALESCE(p.photo_url, CONCAT('/api/v1/profile_photo/', u.id)) AS photo_url
                            FROM users u
                            JOIN profiles p ON u.id = p.user_id
                            WHERE u.role = 'teacher'
                            LIMIT %s OFFSET %s
                        """, (per_page, offset))
            return await cursor.fetchall()
    finally:
        conn.close()


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user_info(user_id: int, current_user=Depends(get_current_user)):
    """Get user information by ID"""
    conn = await get_db_connection()
    try:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("SELECT role FROM users WHERE id = %s", (user_id,))
            role_row = await cursor.fetchone()
            if not role_row:
                raise HTTPException(status_code=404, detail="User not found")
            role = role_row["role"]
            await _ensure_can_read_user(
                cursor,
                current_user,
                user_id,
                target_role=role,
            )

            if role == "student":
                await cursor.execute("""
                    SELECT u.id,
                           COALESCE(NULLIF(TRIM(BOTH '"' FROM p.full_name), ''), u.username) AS full_name,
                           (SELECT c.name FROM classes c 
                            JOIN class_students cs ON c.id = cs.class_id 
                            WHERE cs.student_id = u.id LIMIT 1) as class_name,
                           (SELECT s.name FROM schools s
                            JOIN classes c ON c.school_id = s.id
                            JOIN class_students cs ON cs.class_id = c.id
                            WHERE cs.student_id = u.id LIMIT 1) as school_name,
                           COALESCE(p.photo_url, CONCAT('/api/v1/profile_photo/', u.id)) AS photo_url,
                           u.last_seen_at
                    FROM users u
                    LEFT JOIN profiles p ON u.id = p.user_id
                    WHERE u.id = %s
                """, (user_id,))
            else:
                await cursor.execute("""
                    SELECT u.id,
                           COALESCE(NULLIF(TRIM(BOTH '"' FROM p.full_name), ''), u.username) AS full_name,
                           p.work_place, p.work_place AS school_name,
                           p.location, p.location AS class_name,
                           NULL as subject,
                           (SELECT GROUP_CONCAT(c.name SEPARATOR ', ')
                            FROM class_teachers ct 
                            JOIN classes c ON ct.class_id = c.id 
                            WHERE ct.teacher_id = u.id) as classes,
                           u.username, NULL as password,
                           COALESCE(p.photo_url, CONCAT('/api/v1/profile_photo/', u.id)) AS photo_url,
                           u.last_seen_at
                    FROM users u
                    LEFT JOIN profiles p ON u.id = p.user_id
                    WHERE u.id = %s
                """, (user_id,))
            user = await cursor.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="Profile not found")
            user["role"] = role
            user["is_online"] = (
                await ws_manager.connection_count(user_id) > 0
            )
            if user.get("last_seen_at") is not None:
                user["last_seen_at"] = user["last_seen_at"].replace(
                    tzinfo=UTC,
                )
            return user
    finally:
        conn.close()


@router.post("/owners/create-admin")
async def owner_create_admin(
    username: str = Form(...),
    password: str = Form(...),
    school_id: int = Form(...),
    is_main_admin: bool = Form(False),
    current_user=Depends(get_current_user)
):
    """Owner creates an admin user and assigns to a school. is_main_admin: главный админ (все права)."""
    role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")

    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")
    _validate_admin_password(password, username)

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            hashed = get_password_hash(password)
            try:
                await cursor.execute(
                    "INSERT INTO users (username, hashed_password, role) VALUES (%s, %s, 'admin')",
                    (username, hashed)
                )
                await conn.commit()
            except Exception:
                raise HTTPException(status_code=400, detail="User already exists")

            await cursor.execute("SELECT id FROM users WHERE username=%s", (username,))
            row = await cursor.fetchone()
            admin_id = row[0] if isinstance(row, (list, tuple)) else row.get("id")

            # Check if school_admins has is_main_admin column
            await cursor.execute("""
                SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'school_admins' AND COLUMN_NAME = 'is_main_admin'
            """)
            has_col = await cursor.fetchone()
            if has_col:
                await cursor.execute(
                    "INSERT INTO school_admins (school_id, admin_user_id, is_main_admin) VALUES (%s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE is_main_admin = VALUES(is_main_admin)",
                    (school_id, admin_id, is_main_admin)
                )
            else:
                await cursor.execute(
                    "INSERT IGNORE INTO school_admins (school_id, admin_user_id) VALUES (%s, %s)",
                    (school_id, admin_id)
                )
            await register_school_user(
                cursor,
                school_id=school_id,
                user_id=admin_id,
                role="admin",
            )
            await conn.commit()
            return {"status": "created", "admin_user_id": admin_id, "is_main_admin": is_main_admin}
    finally:
        conn.close()


@router.post("/admin/create-teacher")
async def admin_create_teacher(
    full_name: str = Form(...),
    username: str = Form(None),
    password: str = Form(None),
    email: str = Form(None),
    phone: str = Form(None),
    position: str = Form(None),
    subjects: str = Form(None),  # Comma-separated list of subject names
    note: str = Form(None),
    school_id: int = Form(None),
    current_user=Depends(get_current_user)
):
    """Admin creates a teacher user with profile information."""
    # Check if user is admin or owner
    role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")

    if not full_name or not full_name.strip():
        raise HTTPException(status_code=400, detail="Full name is required")
    full_name = _clean_profile_text(full_name)
    resolved_school_id = await get_school_id_for_user(current_user)
    if resolved_school_id is None:
        resolved_school_id = school_id
    if resolved_school_id is None:
        raise HTTPException(status_code=400, detail="Укажите школу для учителя")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await ensure_school_capacity(
                cursor,
                school_id=resolved_school_id,
                resource="teachers",
            )
            # Use provided username or generate from email or full name
            final_username = None
            if username and username.strip():
                final_username = username.strip()
            elif email and email.strip():
                final_username = email.split("@")[0].lower().replace(".", "_")
            else:
                # Generate username from full name
                final_username = full_name.lower().replace(" ", "_").replace(".", "_")
                # Add random suffix to avoid conflicts
                final_username = f"{final_username}_{_numeric_suffix(4)}"

            # Check if username already exists
            await cursor.execute("SELECT id FROM users WHERE username=%s", (final_username,))
            if await cursor.fetchone():
                if username and username.strip():
                    # If username was provided, raise error
                    raise HTTPException(status_code=400, detail="Username already exists")
                else:
                    # If auto-generated, add more random suffix
                    final_username = f"{final_username}_{_numeric_suffix(5)}"

            # Use provided password or generate a temporary one
            final_password = None
            if password and password.strip():
                final_password = password.strip()
                _validate_admin_password(final_password, final_username)
            else:
                # Generate a temporary password (should be changed on first login)
                final_password = secrets.token_urlsafe(12)

            # Create user with role 'teacher'
            hashed = get_password_hash(final_password)
            clean_email = _required_email(email)
            try:
                await cursor.execute(
                    "INSERT INTO users (username, hashed_password, role, email) VALUES (%s, %s, 'teacher', %s)",
                    (final_username, hashed, clean_email)
                )
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to create user: {str(e)}")

            # Get the created user ID
            await cursor.execute("SELECT id FROM users WHERE username=%s", (final_username,))
            row = await cursor.fetchone()
            teacher_id = row[0] if isinstance(row, (list, tuple)) else row.get("id")

            # Create profile
            work_place = _clean_profile_text(position)
            location = ""  # Can be added later
            bio = _clean_profile_text(note)
            
            await cursor.execute("""
                INSERT INTO profiles (user_id, full_name, work_place, location, bio)
                VALUES (%s, %s, %s, %s, %s)
            """, (teacher_id, full_name, work_place, location, bio))
            await register_school_user(
                cursor,
                school_id=resolved_school_id,
                user_id=teacher_id,
                role="teacher",
            )

            # Link subjects if provided
            if subjects and subjects.strip():
                subject_names = [s.strip() for s in subjects.split(",") if s.strip()]
                for subject_name in subject_names:
                    # Get subject ID by name
                    await cursor.execute(
                        """
                        SELECT s.id
                        FROM school_subjects ss
                        JOIN subjects s ON s.id = ss.subject_id
                        WHERE ss.school_id = %s AND s.name = %s
                        """,
                        (resolved_school_id, subject_name),
                    )
                    subject_row = await cursor.fetchone()
                    if subject_row:
                        subject_id = subject_row[0] if isinstance(subject_row, (list, tuple)) else subject_row.get("id")
                        # Link teacher to subject
                        await cursor.execute("""
                            INSERT IGNORE INTO teacher_subjects (teacher_id, subject_id)
                            VALUES (%s, %s)
                        """, (teacher_id, subject_id))
            await conn.commit()

            return {
                "status": "created",
                "teacher_id": teacher_id,
                "username": final_username,
                "password": final_password if not (password and password.strip()) else None  # Only return if auto-generated
            }
    finally:
        conn.close()


@router.post("/admin/create-student")
async def admin_create_student(
    full_name: str = Form(...),
    username: str = Form(None),
    password: str = Form(None),
    email: str = Form(None),
    phone: str = Form(None),
    class_id: int = Form(None),
    note: str = Form(None),
    school_id: int = Form(None),
    current_user=Depends(get_current_user)
):
    """Admin creates a student user with profile information."""
    # Check if user is admin or owner
    role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")

    if not full_name or not full_name.strip():
        raise HTTPException(status_code=400, detail="Full name is required")
    full_name = _clean_profile_text(full_name)

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            resolved_school_id = await get_school_id_for_user(current_user)
            if resolved_school_id is None and class_id:
                await cursor.execute(
                    "SELECT school_id FROM classes WHERE id = %s",
                    (class_id,),
                )
                class_row = await cursor.fetchone()
                resolved_school_id = (
                    class_row.get("school_id")
                    if isinstance(class_row, dict)
                    else class_row[0] if class_row else None
                )
            if resolved_school_id is None:
                resolved_school_id = school_id
            if resolved_school_id is None:
                raise HTTPException(status_code=400, detail="Укажите школу для ученика")
            if class_id:
                await cursor.execute(
                    "SELECT 1 FROM classes WHERE id = %s AND school_id = %s",
                    (class_id, resolved_school_id),
                )
                if not await cursor.fetchone():
                    raise HTTPException(
                        status_code=400,
                        detail="Класс не относится к выбранной школе",
                    )
            await ensure_school_capacity(
                cursor,
                school_id=resolved_school_id,
                resource="students",
            )
            # Use provided username or generate from email or full name
            final_username = None
            if username and username.strip():
                final_username = username.strip()
            elif email and email.strip():
                final_username = email.split("@")[0].lower().replace(".", "_")
            else:
                # Generate username from full name
                final_username = full_name.lower().replace(" ", "_").replace(".", "_")
                # Add random suffix to avoid conflicts
                final_username = f"{final_username}_{_numeric_suffix(4)}"

            # Check if username already exists
            await cursor.execute("SELECT id FROM users WHERE username=%s", (final_username,))
            if await cursor.fetchone():
                if username and username.strip():
                    # If username was provided, raise error
                    raise HTTPException(status_code=400, detail="Username already exists")
                else:
                    # If auto-generated, add more random suffix
                    final_username = f"{final_username}_{_numeric_suffix(5)}"

            # Use provided password or generate a temporary one
            final_password = None
            if password and password.strip():
                final_password = password.strip()
                _validate_admin_password(final_password, final_username)
            else:
                # Generate a temporary password (should be changed on first login)
                final_password = secrets.token_urlsafe(12)

            # Create user with role 'student'
            hashed = get_password_hash(final_password)
            clean_email = _required_email(email)
            try:
                await cursor.execute(
                    "INSERT INTO users (username, hashed_password, role, email) VALUES (%s, %s, 'student', %s)",
                    (final_username, hashed, clean_email)
                )
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to create user: {str(e)}")

            # Get the created user ID
            await cursor.execute("SELECT id FROM users WHERE username=%s", (final_username,))
            row = await cursor.fetchone()
            student_id = row[0] if isinstance(row, (list, tuple)) else row.get("id")

            # Create profile
            work_place = ""  # Students don't have work place
            location = ""  # Can be added later
            bio = _clean_profile_text(note)
            
            await cursor.execute("""
                INSERT INTO profiles (user_id, full_name, work_place, location, bio)
                VALUES (%s, %s, %s, %s, %s)
            """, (student_id, full_name, work_place, location, bio))
            await register_school_user(
                cursor,
                school_id=resolved_school_id,
                user_id=student_id,
                role="student",
            )

            # Link to class if provided
            if class_id:
                await cursor.execute("""
                    INSERT IGNORE INTO class_students (class_id, student_id)
                    VALUES (%s, %s)
                """, (class_id, student_id))
            await conn.commit()

            return {
                "status": "created",
                "student_id": student_id,
                "username": final_username,
                "password": final_password if not (password and password.strip()) else None  # Only return if auto-generated
            }
    finally:
        conn.close()


@router.get("/admin/count-admins")
async def count_admins(current_user=Depends(get_current_user)):
    """Get count of admin users (for owner analytics)."""
    # Check if user is owner
    role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT COUNT(*) as count FROM users WHERE role='admin'")
            result = await cursor.fetchone()
            count = result[0] if isinstance(result, (list, tuple)) else result.get("count", 0)
            return {"count": count}
    finally:
        conn.close()


@router.delete("/admin/schools/{school_id}/users/{user_id}")
async def remove_user_from_school(
    school_id: int,
    user_id: int,
    current_user=Depends(get_current_user),
):
    """Detach a user from a school and release the licensed seat."""
    _, role = _user_id_and_role(current_user)
    if role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")
    current_school_id = await get_school_id_for_user(current_user)
    if not _is_application_owner(role) and current_school_id != school_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    actor_id, _ = _user_id_and_role(current_user)
    if actor_id == user_id:
        raise HTTPException(
            status_code=400,
            detail="Нельзя убрать собственную учетную запись из школы",
        )

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT u.role
                FROM school_users su
                JOIN users u ON u.id = su.user_id
                WHERE su.school_id = %s AND su.user_id = %s
                FOR UPDATE
                """,
                (school_id, user_id),
            )
            member = await cursor.fetchone()
            if not member:
                raise HTTPException(
                    status_code=404,
                    detail="Пользователь не относится к этой школе",
                )
            member_role = (
                member.get("role") if isinstance(member, dict) else member[0]
            )

            if member_role == "student":
                await cursor.execute(
                    """
                    DELETE cs
                    FROM class_students cs
                    JOIN classes c ON c.id = cs.class_id
                    WHERE cs.student_id = %s AND c.school_id = %s
                    """,
                    (user_id, school_id),
                )
            elif member_role == "teacher":
                await cursor.execute(
                    """
                    DELETE ct
                    FROM class_teachers ct
                    JOIN classes c ON c.id = ct.class_id
                    WHERE ct.teacher_id = %s AND c.school_id = %s
                    """,
                    (user_id, school_id),
                )
                await cursor.execute(
                    """
                    DELETE cst
                    FROM class_subject_teachers cst
                    JOIN classes c ON c.id = cst.class_id
                    WHERE cst.teacher_id = %s AND c.school_id = %s
                    """,
                    (user_id, school_id),
                )
            elif member_role == "admin":
                if not _is_application_owner(role):
                    await cursor.execute(
                        """
                        SELECT COALESCE(is_main_admin, 0) AS is_main_admin
                        FROM school_admins
                        WHERE school_id = %s AND admin_user_id = %s
                        """,
                        (school_id, user_id),
                    )
                    admin_row = await cursor.fetchone()
                    is_main_admin = bool(
                        admin_row.get("is_main_admin")
                        if isinstance(admin_row, dict)
                        else admin_row[0] if admin_row else False
                    )
                    if is_main_admin:
                        raise HTTPException(
                            status_code=403,
                            detail="Главного администратора может убрать только владелец приложения",
                        )
                await cursor.execute(
                    """
                    DELETE FROM school_admins
                    WHERE school_id = %s AND admin_user_id = %s
                    """,
                    (school_id, user_id),
                )

            await cursor.execute(
                "DELETE FROM school_users WHERE school_id = %s AND user_id = %s",
                (school_id, user_id),
            )
            await cursor.execute(
                """
                INSERT INTO audit_log
                (school_id, user_id, action, entity_type, entity_id, details)
                VALUES (%s, %s, 'school_user_removed', 'user', %s, %s)
                """,
                (
                    school_id,
                    actor_id,
                    user_id,
                    json.dumps({"removed_role": member_role}),
                ),
            )
            await conn.commit()
            return {"status": "removed", "released_role": member_role}
    except HTTPException:
        await conn.rollback()
        raise
    finally:
        conn.close()


@router.put("/admin/users/{user_id}")
async def admin_update_user(
    user_id: int,
    full_name: str = Form(None),
    work_place: str = Form(None),
    location: str = Form(None),
    bio: str = Form(None),
    username: str = Form(None),
    password: str = Form(None),
    email: str = Form(None),
    phone: str = Form(None),
    file: UploadFile = File(None),
    current_user=Depends(get_current_user)
):
    """Admin updates a user (student or teacher) with profile information."""
    # Check if user is admin or owner
    role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Check if user exists
            await cursor.execute("SELECT role, username FROM users WHERE id = %s", (user_id,))
            user_row = await cursor.fetchone()
            if not user_row:
                raise HTTPException(status_code=404, detail="User not found")
            
            user_role = user_row[0] if isinstance(user_row, (list, tuple)) else user_row.get("role")
            existing_username = user_row[1] if isinstance(user_row, (list, tuple)) else user_row.get("username")
            await _ensure_can_manage_user(
                cursor,
                current_user,
                user_id,
                user_role,
            )

            # Update username if provided
            if username and username.strip():
                await cursor.execute(
                    "UPDATE users SET username = %s WHERE id = %s",
                    (username.strip(), user_id)
                )

            # Update password if provided
            if password and password.strip():
                next_username = username.strip() if username and username.strip() else existing_username
                _validate_admin_password(password.strip(), next_username)
                hashed = get_password_hash(password.strip())
                await cursor.execute(
                    "UPDATE users SET hashed_password = %s WHERE id = %s",
                    (hashed, user_id)
                )

            if email is not None:
                clean_email = _clean_email(email)
                await cursor.execute(
                    "UPDATE users SET email = %s WHERE id = %s",
                    (clean_email, user_id),
                )

            # Update profile fields
            updates = []
            params = []
            
            if full_name is not None and full_name.strip():
                updates.append("full_name = %s")
                params.append(_clean_profile_text(full_name))
            
            if work_place is not None:
                updates.append("work_place = %s")
                params.append(_clean_profile_text(work_place))
            
            if location is not None:
                updates.append("location = %s")
                params.append(_clean_profile_text(location))
            
            if bio is not None:
                updates.append("bio = %s")
                params.append(_clean_profile_text(bio))

            if updates:
                params.append(user_id)
                await cursor.execute(
                    f"UPDATE profiles SET {', '.join(updates)} WHERE user_id = %s",
                    params
                )

            # Handle photo upload if provided
            if file and file.filename:
                _, ext = validate_upload_metadata(
                    file,
                    allowed_exts=IMAGE_EXTS - {"gif", "webp"},
                    allowed_mime_prefixes=("image/",),
                )

                temp_path = os.path.join(
                    settings.PROFILE_PHOTOS_DIR,
                    f".{user_id}_{uuid.uuid4().hex}.upload",
                )
                path = os.path.join(
                    settings.PROFILE_PHOTOS_DIR,
                    f"{user_id}.{ext}",
                )
                await save_upload_limited(file, temp_path, max_bytes=MAX_IMAGE_BYTES)
                try:
                    validate_avatar_dimensions(temp_path)
                    for old_ext in ["jpg", "jpeg", "png"]:
                        old_path = os.path.join(
                            settings.PROFILE_PHOTOS_DIR,
                            f"{user_id}.{old_ext}",
                        )
                        if os.path.exists(old_path):
                            os.remove(old_path)
                    os.replace(temp_path, path)
                except Exception:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    raise

                # Update photo_url in profiles
                photo_url = f"/api/v1/profile_photo/{user_id}"
                await cursor.execute(
                    "UPDATE profiles SET photo_url = %s WHERE user_id = %s",
                    (photo_url, user_id)
                )

            await conn.commit()
            return {"message": "User updated successfully", "user_id": user_id}
    except Exception:
        await conn.rollback()
        raise
    finally:
        conn.close()
