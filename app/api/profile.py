import os
import re
import uuid
from typing import Optional
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends
from starlette.responses import FileResponse

from app.core.security import get_current_user
from app.core.config import settings
from app.core.file_uploads import (
    IMAGE_EXTS,
    MAX_IMAGE_BYTES,
    image_dimensions,
    save_upload_limited,
    validate_avatar_dimensions,
    validate_upload_metadata,
)
from app.core.entitlements import get_school_id_for_user
from app.core.subscription_limits import change_school_storage
from app.db.connection import get_db_connection

router = APIRouter()


def _clean_profile_text(value: str | None) -> str:
    return (value or "").strip().strip('"“”«»').strip()


def _clean_email(value: str | None) -> str | None:
    email = (value or "").strip().lower()
    if not email:
        return None
    if len(email) > 320 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=400, detail="Некорректный email")
    return email


async def _replace_profile_photo(
    file: UploadFile,
    *,
    user_id: int,
    ext: str,
    school_id: int | None,
    conn,
) -> str:
    old_paths = [
        os.path.join(settings.PROFILE_PHOTOS_DIR, f"{user_id}.{item_ext}")
        for item_ext in ("jpg", "jpeg", "png")
    ]
    old_size = sum(
        os.path.getsize(path) for path in old_paths if os.path.isfile(path)
    )
    temp_path = os.path.join(
        settings.PROFILE_PHOTOS_DIR,
        f".{user_id}_{uuid.uuid4().hex}.upload",
    )
    final_path = os.path.join(settings.PROFILE_PHOTOS_DIR, f"{user_id}.{ext}")
    written = await save_upload_limited(file, temp_path, max_bytes=MAX_IMAGE_BYTES)
    try:
        validate_avatar_dimensions(temp_path)
        if school_id is not None:
            async with conn.cursor() as cursor:
                await change_school_storage(
                    cursor,
                    school_id=school_id,
                    delta_bytes=written - old_size,
                )
                await conn.commit()
        for path in old_paths:
            if os.path.isfile(path):
                os.remove(path)
        if os.path.isfile(temp_path):
            os.replace(temp_path, final_path)
    except Exception:
        if os.path.isfile(temp_path):
            os.remove(temp_path)
        await conn.rollback()
        raise
    return final_path


@router.post("/profile/photo")
async def upload_photo(
    file: UploadFile = File(...),
    current_user=Depends(get_current_user)
):
    """Upload profile photo"""
    _, ext = validate_upload_metadata(
        file,
        allowed_exts=IMAGE_EXTS - {"gif", "webp"},
        allowed_mime_prefixes=("image/",),
    )

    photo_url = f"{settings.API_V1_STR}/profile_photo/{current_user.id}"
    display_name = (
        current_user.get("username")
        if isinstance(current_user, dict)
        else getattr(current_user, "username", None)
    ) or str(current_user.id)
    conn = await get_db_connection()
    try:
        school_id = await get_school_id_for_user(current_user)
        await _replace_profile_photo(
            file,
            user_id=current_user.id,
            ext=ext,
            school_id=school_id,
            conn=conn,
        )
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO profiles (user_id, full_name, photo_url)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE photo_url = VALUES(photo_url)
                """,
                (current_user.id, display_name, photo_url),
            )
        await conn.commit()
    finally:
        conn.close()

    return {"photo_url": photo_url}


@router.get("/profile/photo")
async def get_profile_photo_me(current_user=Depends(get_current_user)):
    """Get current user's profile photo"""
    for ext in ["jpg", "jpeg", "png"]:
        file_path = os.path.join(settings.PROFILE_PHOTOS_DIR, f"{current_user.id}.{ext}")
        if os.path.exists(file_path):
            media_type = f"image/{'jpeg' if ext in ['jpg', 'jpeg'] else 'png'}"
            return FileResponse(file_path, media_type=media_type)
    raise HTTPException(status_code=404, detail="Profile photo not found")


@router.get("/profile_photo/{user_id}")
async def get_profile_photo_by_id(user_id: int):
    """Get profile photo by user ID"""
    for ext in ["jpg", "jpeg", "png"]:
        file_path = os.path.join(settings.PROFILE_PHOTOS_DIR, f"{user_id}.{ext}")
        if os.path.exists(file_path):
            dimensions = image_dimensions(file_path)
            if dimensions is None or min(dimensions) < 32:
                continue
            media_type = f"image/{'jpeg' if ext in ['jpg', 'jpeg'] else 'png'}"
            return FileResponse(file_path, media_type=media_type)
    raise HTTPException(status_code=404, detail="Profile photo not found")


@router.get("/profile/info")
async def get_profile_data(current_user=Depends(get_current_user)):
    """Get profile information"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT 
                    TRIM(BOTH '"' FROM p.full_name) AS full_name,
                    TRIM(BOTH '"' FROM p.work_place) AS work_place,
                    TRIM(BOTH '"' FROM p.location) AS location,
                    TRIM(BOTH '"' FROM p.bio) AS bio,
                    cs.class_id AS class_id,
                    CASE
                        WHEN u.role = 'student' THEN c.name
                        ELSE NULLIF(TRIM(BOTH '"' FROM p.location), '')
                    END AS class_name,
                    CASE
                        WHEN u.role = 'student' THEN s.name
                        ELSE NULLIF(TRIM(BOTH '"' FROM p.work_place), '')
                    END AS school_name,
                    u.role AS role
                    ,u.email AS email
                FROM profiles p
                JOIN users u ON u.id = p.user_id
                LEFT JOIN class_students cs ON cs.student_id = p.user_id
                LEFT JOIN classes c ON c.id = cs.class_id
                LEFT JOIN schools s ON s.id = c.school_id
                WHERE p.user_id = %s
                LIMIT 1
            """, (current_user.id,))
            profile = await cursor.fetchone()
            if profile:
                return profile
            raise HTTPException(status_code=404, detail="Profile not found")
    finally:
        conn.close()


@router.post("/profile/full-update")
async def update_full_profile(
    full_name: str = Form(...),
    work_place: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    bio: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    current_user=Depends(get_current_user)
):
    """Update full profile with optional photo"""
    full_name = _clean_profile_text(full_name)
    role = (
        current_user.get("role")
        if isinstance(current_user, dict)
        else getattr(current_user, "role", None)
    )
    # Школа и класс ученика определяются зачислением. Их нельзя менять
    # через редактирование профиля.
    work_place = None if role == "student" else _clean_profile_text(work_place)
    location = None if role == "student" else _clean_profile_text(location)
    bio = _clean_profile_text(bio)
    email_provided = email is not None
    email = _clean_email(email)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            if email_provided:
                if email is not None:
                    await cursor.execute(
                        "SELECT id FROM users WHERE email = %s AND id <> %s LIMIT 1",
                        (email, current_user.id),
                    )
                    if await cursor.fetchone():
                        raise HTTPException(
                            status_code=409,
                            detail="Этот email уже используется",
                        )
                await cursor.execute(
                    "UPDATE users SET email = %s WHERE id = %s",
                    (email, current_user.id),
                )
            await cursor.execute("SELECT 1 FROM profiles WHERE user_id = %s", (current_user.id,))
            exists = await cursor.fetchone()
            if exists:
                if role == "student":
                    await cursor.execute("""
                        UPDATE profiles
                        SET full_name = %s, bio = %s
                        WHERE user_id = %s
                    """, (full_name, bio, current_user.id))
                else:
                    await cursor.execute("""
                        UPDATE profiles
                        SET full_name = %s, work_place = %s, location = %s, bio = %s
                        WHERE user_id = %s
                    """, (full_name, work_place, location, bio, current_user.id))
            else:
                await cursor.execute("""
                    INSERT INTO profiles (user_id, full_name, work_place, location, bio)
                    VALUES (%s, %s, %s, %s, %s)
                """, (current_user.id, full_name, work_place, location, bio))
        await conn.commit()

        photo_url = None
        if file:
            _, ext = validate_upload_metadata(
                file,
                allowed_exts=IMAGE_EXTS - {"gif", "webp"},
                allowed_mime_prefixes=("image/",),
            )
            school_id = await get_school_id_for_user(current_user)
            await _replace_profile_photo(
                file,
                user_id=current_user.id,
                ext=ext,
                school_id=school_id,
                conn=conn,
            )
            photo_url = f"{settings.API_V1_STR}/profile_photo/{current_user.id}"
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "UPDATE profiles SET photo_url = %s WHERE user_id = %s",
                    (photo_url, current_user.id),
                )
            await conn.commit()

        return {"message": "Profile and photo updated", "photo_url": photo_url}
    finally:
        conn.close()
