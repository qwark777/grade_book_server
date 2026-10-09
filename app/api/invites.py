import re
import secrets
from datetime import datetime, timedelta, UTC

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.entitlements import get_school_id_for_user
from app.core.security import (
    create_access_token,
    get_current_user,
    get_password_hash,
    validate_password_strength,
)
from app.core.subscription_limits import ensure_school_capacity, register_school_user
from app.db.connection import get_db_connection

router = APIRouter()


TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class InviteResponse(BaseModel):
    token: str
    invite_type: str
    url: str
    school_id: int
    school_name: str | None = None
    class_id: int | None = None
    class_name: str | None = None
    student_id: int | None = None
    student_name: str | None = None
    expires_at: str


class InviteJoinRequest(BaseModel):
    token: str = Field(..., min_length=8, max_length=128)
    full_name: str = Field(..., min_length=2, max_length=255)
    email: str = Field(..., min_length=3, max_length=320)
    password: str = Field(..., min_length=8, max_length=256)
    username: str | None = Field(default=None, max_length=255)


def _role(current_user) -> str:
    return current_user.get("role", "") if isinstance(current_user, dict) else getattr(current_user, "role", "")


def _user_id(current_user) -> int | None:
    return current_user.get("id") if isinstance(current_user, dict) else getattr(current_user, "id", None)


def _require_admin(current_user) -> None:
    if _role(current_user) not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")


def _clean_email(value: str | None) -> str:
    email = (value or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email обязателен")
    if len(email) > 320 or not EMAIL_RE.fullmatch(email):
        raise HTTPException(status_code=400, detail="Некорректный email")
    return email


def _clean_name(value: str | None) -> str:
    name = (value or "").strip().strip('"“”«»').strip()
    if len(name) < 2:
        raise HTTPException(status_code=400, detail="Укажите имя")
    return name


def _clean_username(value: str | None, email: str) -> str:
    username = (value or "").strip()
    if username:
        return username
    return email.split("@")[0].lower().replace(".", "_")


def _new_token() -> str:
    return secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:16]


def _invite_url(token: str) -> str:
    base = settings.PASSWORD_RESET_URL or "https://gradebook.su/app/"
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}invite={token}"


async def _ensure_admin_can_manage_school(cursor, current_user, school_id: int) -> None:
    role = _role(current_user)
    if role in ("owner", "superadmin", "root"):
        return
    admin_school_id = await get_school_id_for_user(current_user)
    if admin_school_id != school_id:
        raise HTTPException(status_code=403, detail="Нет доступа к этой школе")


async def _fetch_class(cursor, class_id: int) -> dict:
    await cursor.execute(
        """
        SELECT c.id, c.name, c.school_id, s.name AS school_name
        FROM classes c
        JOIN schools s ON s.id = c.school_id
        WHERE c.id = %s AND COALESCE(c.is_archived, 0) = 0
        LIMIT 1
        """,
        (class_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Класс не найден")
    return row


async def _active_invite(cursor, *, invite_type: str, class_id: int | None = None, student_id: int | None = None) -> dict | None:
    if invite_type == "student":
        await cursor.execute(
            """
            SELECT ci.*, c.name AS class_name, s.name AS school_name, NULL AS student_name
            FROM class_invites ci
            JOIN classes c ON c.id = ci.class_id
            JOIN schools s ON s.id = ci.school_id
            WHERE ci.invite_type = 'student'
              AND ci.class_id = %s
              AND ci.revoked_at IS NULL
              AND ci.expires_at > NOW()
            ORDER BY ci.id DESC
            LIMIT 1
            """,
            (class_id,),
        )
    else:
        await cursor.execute(
            """
            SELECT ci.*, c.name AS class_name, s.name AS school_name,
                   COALESCE(NULLIF(TRIM(BOTH '"' FROM p.full_name), ''), u.username) AS student_name
            FROM class_invites ci
            JOIN classes c ON c.id = ci.class_id
            JOIN schools s ON s.id = ci.school_id
            JOIN users u ON u.id = ci.student_id
            LEFT JOIN profiles p ON p.user_id = u.id
            WHERE ci.invite_type = 'parent'
              AND ci.student_id = %s
              AND ci.revoked_at IS NULL
              AND ci.expires_at > NOW()
            ORDER BY ci.id DESC
            LIMIT 1
            """,
            (student_id,),
        )
    return await cursor.fetchone()


def _to_response(row: dict) -> InviteResponse:
    expires_at = row["expires_at"]
    if hasattr(expires_at, "isoformat"):
        expires_at = expires_at.isoformat()
    return InviteResponse(
        token=row["token"],
        invite_type=row["invite_type"],
        url=_invite_url(row["token"]),
        school_id=int(row["school_id"]),
        school_name=row.get("school_name"),
        class_id=row.get("class_id"),
        class_name=row.get("class_name"),
        student_id=row.get("student_id"),
        student_name=row.get("student_name"),
        expires_at=str(expires_at),
    )


async def _create_invite(
    cursor,
    *,
    invite_type: str,
    school_id: int,
    class_id: int,
    created_by: int | None,
    student_id: int | None = None,
) -> dict:
    await cursor.execute(
        """
        UPDATE class_invites
        SET revoked_at = NOW()
        WHERE invite_type = %s
          AND revoked_at IS NULL
          AND ((%s IS NULL AND student_id IS NULL) OR student_id = %s)
          AND class_id = %s
        """,
        (invite_type, student_id, student_id, class_id),
    )
    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=30)
    for _ in range(5):
        token = _new_token()
        try:
            await cursor.execute(
                """
                INSERT INTO class_invites
                    (token, invite_type, school_id, class_id, student_id, created_by, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (token, invite_type, school_id, class_id, student_id, created_by, expires_at),
            )
            break
        except Exception as exc:
            if getattr(exc, "args", [None])[0] != 1062:
                raise
    else:
        raise HTTPException(status_code=500, detail="Не удалось создать invite")
    return await _active_invite(
        cursor,
        invite_type=invite_type,
        class_id=class_id,
        student_id=student_id,
    )


@router.get("/classes/{class_id}/student", response_model=InviteResponse | None)
async def get_class_student_invite(class_id: int, current_user=Depends(get_current_user)):
    _require_admin(current_user)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            class_row = await _fetch_class(cursor, class_id)
            await _ensure_admin_can_manage_school(cursor, current_user, int(class_row["school_id"]))
            row = await _active_invite(cursor, invite_type="student", class_id=class_id)
            return _to_response(row) if row else None
    finally:
        conn.close()


@router.post("/classes/{class_id}/student", response_model=InviteResponse)
async def create_class_student_invite(class_id: int, current_user=Depends(get_current_user)):
    _require_admin(current_user)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            class_row = await _fetch_class(cursor, class_id)
            school_id = int(class_row["school_id"])
            await _ensure_admin_can_manage_school(cursor, current_user, school_id)
            row = await _create_invite(
                cursor,
                invite_type="student",
                school_id=school_id,
                class_id=class_id,
                created_by=_user_id(current_user),
            )
            await conn.commit()
            return _to_response(row)
    finally:
        conn.close()


@router.post("/students/{student_id}/parent", response_model=InviteResponse)
async def create_parent_invite(student_id: int, current_user=Depends(get_current_user)):
    _require_admin(current_user)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT u.id AS student_id, cs.class_id, c.school_id,
                       COALESCE(NULLIF(TRIM(BOTH '"' FROM p.full_name), ''), u.username) AS student_name
                FROM users u
                JOIN class_students cs ON cs.student_id = u.id
                JOIN classes c ON c.id = cs.class_id
                LEFT JOIN profiles p ON p.user_id = u.id
                WHERE u.id = %s AND u.role = 'student'
                  AND COALESCE(c.is_archived, 0) = 0
                ORDER BY cs.class_id DESC
                LIMIT 1
                """,
                (student_id,),
            )
            student = await cursor.fetchone()
            if not student:
                raise HTTPException(status_code=404, detail="Ученик не найден")
            school_id = int(student["school_id"])
            class_id = int(student["class_id"])
            await _ensure_admin_can_manage_school(cursor, current_user, school_id)
            row = await _create_invite(
                cursor,
                invite_type="parent",
                school_id=school_id,
                class_id=class_id,
                student_id=student_id,
                created_by=_user_id(current_user),
            )
            await conn.commit()
            row["student_name"] = student["student_name"]
            return _to_response(row)
    finally:
        conn.close()


@router.delete("/{token}")
async def revoke_invite(token: str, current_user=Depends(get_current_user)):
    _require_admin(current_user)
    token = token.strip()
    if not TOKEN_RE.fullmatch(token):
        raise HTTPException(status_code=400, detail="Некорректный код")
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT * FROM class_invites WHERE token = %s", (token,))
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Invite не найден")
            await _ensure_admin_can_manage_school(cursor, current_user, int(row["school_id"]))
            await cursor.execute(
                "UPDATE class_invites SET revoked_at = NOW() WHERE token = %s",
                (token,),
            )
            await conn.commit()
        return {"status": "revoked"}
    finally:
        conn.close()


@router.get("/{token}", response_model=InviteResponse)
async def get_invite_public(token: str):
    token = token.strip()
    if not TOKEN_RE.fullmatch(token):
        raise HTTPException(status_code=400, detail="Некорректный код")
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT ci.*, c.name AS class_name, s.name AS school_name,
                       COALESCE(NULLIF(TRIM(BOTH '"' FROM p.full_name), ''), u.username) AS student_name
                FROM class_invites ci
                LEFT JOIN classes c ON c.id = ci.class_id
                JOIN schools s ON s.id = ci.school_id
                LEFT JOIN users u ON u.id = ci.student_id
                LEFT JOIN profiles p ON p.user_id = u.id
                WHERE ci.token = %s
                  AND ci.revoked_at IS NULL
                  AND ci.expires_at > NOW()
                LIMIT 1
                """,
                (token,),
            )
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Ссылка недействительна или истекла")
            return _to_response(row)
    finally:
        conn.close()


@router.post("/join")
async def join_by_invite(payload: InviteJoinRequest):
    token = payload.token.strip()
    if not TOKEN_RE.fullmatch(token):
        raise HTTPException(status_code=400, detail="Некорректный код")
    email = _clean_email(payload.email)
    full_name = _clean_name(payload.full_name)
    username = _clean_username(payload.username, email)

    try:
        validate_password_strength(payload.password, username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT ci.*, c.name AS class_name, s.name AS school_name
                FROM class_invites ci
                LEFT JOIN classes c ON c.id = ci.class_id
                JOIN schools s ON s.id = ci.school_id
                WHERE ci.token = %s
                  AND ci.revoked_at IS NULL
                  AND ci.expires_at > NOW()
                LIMIT 1
                FOR UPDATE
                """,
                (token,),
            )
            invite = await cursor.fetchone()
            if not invite:
                raise HTTPException(status_code=404, detail="Ссылка недействительна или истекла")

            role = "parent" if invite["invite_type"] == "parent" else "student"
            school_id = int(invite["school_id"])
            class_id = int(invite["class_id"]) if invite.get("class_id") else None
            student_id = int(invite["student_id"]) if invite.get("student_id") else None

            if role == "student":
                await ensure_school_capacity(cursor, school_id=school_id, resource="students")

            await cursor.execute(
                "SELECT id FROM users WHERE username = %s OR email = %s LIMIT 1",
                (username, email),
            )
            if await cursor.fetchone():
                raise HTTPException(status_code=400, detail="Пользователь с таким логином или email уже существует")

            hashed = get_password_hash(payload.password)
            await cursor.execute(
                "INSERT INTO users (username, hashed_password, role, email) VALUES (%s, %s, %s, %s)",
                (username, hashed, role, email),
            )
            new_user_id = cursor.lastrowid
            await cursor.execute(
                """
                INSERT INTO profiles (user_id, full_name, work_place, location, bio)
                VALUES (%s, %s, '', '', '')
                """,
                (new_user_id, full_name),
            )

            if role == "student":
                await register_school_user(
                    cursor,
                    school_id=school_id,
                    user_id=new_user_id,
                    role="student",
                )
                await cursor.execute(
                    "INSERT IGNORE INTO class_students (class_id, student_id) VALUES (%s, %s)",
                    (class_id, new_user_id),
                )
            else:
                if student_id is None:
                    raise HTTPException(status_code=400, detail="Invite родителя поврежден")
                await register_school_user(
                    cursor,
                    school_id=school_id,
                    user_id=new_user_id,
                    role="parent",
                )
                await cursor.execute(
                    "INSERT IGNORE INTO parent_students (parent_id, student_id) VALUES (%s, %s)",
                    (new_user_id, student_id),
                )

            await cursor.execute(
                "UPDATE class_invites SET used_count = used_count + 1 WHERE token = %s",
                (token,),
            )
            await conn.commit()

        access_token = create_access_token(data={"sub": username})
        return {
            "status": "created",
            "user_id": new_user_id,
            "username": username,
            "role": role,
            "access_token": access_token,
            "token_type": "bearer",
        }
    finally:
        conn.close()
