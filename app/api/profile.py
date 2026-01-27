import os
from typing import Optional
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends
from starlette.responses import FileResponse

from app.core.security import get_current_user
from app.core.config import settings
from app.db.connection import get_db_connection

router = APIRouter()


@router.post("/profile/photo")
async def upload_photo(
    file: UploadFile = File(...),
    current_user=Depends(get_current_user)
):
    """Upload profile photo"""
    ext = file.filename.split(".")[-1].lower()
    if ext not in ["jpg", "jpeg", "png"]:
        raise HTTPException(status_code=400, detail="Unsupported file type")

    # Remove old photos
    for e in ["jpg", "jpeg", "png"]:
        old = os.path.join(settings.PROFILE_PHOTOS_DIR, f"{current_user.id}.{e}")
        if os.path.exists(old):
            os.remove(old)

    path = os.path.join(settings.PROFILE_PHOTOS_DIR, f"{current_user.id}.{ext}")
    with open(path, "wb") as buffer:
        buffer.write(await file.read())

    return {"photo_url": f"/profile_photo/{current_user.id}"}


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
                    TRIM(BOTH '"' FROM full_name) AS full_name,
                    TRIM(BOTH '"' FROM work_place) AS work_place,
                    TRIM(BOTH '"' FROM location) AS location,
                    TRIM(BOTH '"' FROM bio) AS bio
                FROM profiles 
                WHERE user_id = %s
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
    work_place: str = Form(...),
    location: str = Form(...),
    bio: str = Form(...),
    file: Optional[UploadFile] = File(None),
    current_user=Depends(get_current_user)
):
    """Update full profile with optional photo"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT 1 FROM profiles WHERE user_id = %s", (current_user.id,))
            exists = await cursor.fetchone()
            if exists:
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

        if file:
            ext = file.filename.split('.')[-1].lower()
            if ext not in ["jpg", "jpeg", "png"]:
                raise HTTPException(status_code=400, detail="Unsupported file type")
            for e in ["jpg", "jpeg", "png"]:
                old_path = os.path.join(settings.PROFILE_PHOTOS_DIR, f"{current_user.id}.{e}")
                if os.path.exists(old_path):
                    os.remove(old_path)
            path = os.path.join(settings.PROFILE_PHOTOS_DIR, f"{current_user.id}.{ext}")
            with open(path, "wb") as f:
                f.write(await file.read())

        return {"message": "Profile and photo updated"}
    finally:
        conn.close()


