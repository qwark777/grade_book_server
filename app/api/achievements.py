from typing import List, Optional
import os
import aiomysql
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel

from app.core.security import get_current_user
from app.core.config import settings
from app.db.connection import get_db_connection

router = APIRouter()


class CreateAchievementRequest(BaseModel):
    code: str
    title: str
    description: Optional[str] = None
    image_url: Optional[str] = None


class AwardAchievementRequest(BaseModel):
    user_id: int
    achievement_code: str


class AchievementResponse(BaseModel):
    id: int
    name: str
    description: str
    photo_url: Optional[str] = None
    obtained_at: Optional[str] = None
    user_id: Optional[int] = None

    class Config:
        from_attributes = True


@router.get("/achievements/me", response_model=List[AchievementResponse])
async def get_my_achievements(current_user=Depends(get_current_user)):
    """Get all achievements for the current user"""
    conn = await get_db_connection()
    try:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # Проверяем, есть ли колонка description
            await cursor.execute("""
                SELECT COLUMN_NAME 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_SCHEMA = DATABASE() 
                AND TABLE_NAME = 'achievements' 
                AND COLUMN_NAME = 'description'
            """)
            has_description = await cursor.fetchone() is not None
            
            # Используем существующую структуру: code, title, image_url
            if has_description:
                query = """
                    SELECT 
                        a.id,
                        COALESCE(a.title, a.code) as name,
                        COALESCE(a.description, a.title, a.code) as description,
                        a.image_url as photo_url,
                        ua.earned_at as obtained_at,
                        ua.user_id
                    FROM achievements a
                    INNER JOIN user_achievements ua ON a.id = ua.achievement_id AND ua.user_id = %s
                    ORDER BY ua.earned_at DESC
                """
            else:
                query = """
                    SELECT 
                        a.id,
                        COALESCE(a.title, a.code) as name,
                        COALESCE(a.title, a.code) as description,
                        a.image_url as photo_url,
                        ua.earned_at as obtained_at,
                        ua.user_id
                    FROM achievements a
                    INNER JOIN user_achievements ua ON a.id = ua.achievement_id AND ua.user_id = %s
                    ORDER BY ua.earned_at DESC
                """
            
            await cursor.execute(query, (current_user.id,))
            
            results = await cursor.fetchall()
            
            achievements = []
            for row in results:
                achievements.append({
                    'id': row['id'],
                    'name': row.get('name') or '',
                    'description': row.get('description') or row.get('name') or '',
                    'photo_url': row.get('photo_url'),
                    'obtained_at': row['obtained_at'].isoformat() if row.get('obtained_at') else None,
                    'user_id': row.get('user_id'),
                })
            
            return achievements
    finally:
        conn.close()


@router.get("/achievements/all", response_model=List[AchievementResponse])
async def get_all_achievements(current_user=Depends(get_current_user)):
    """Get all available achievements (both obtained and not obtained)"""
    conn = await get_db_connection()
    try:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # Проверяем, есть ли колонка description
            await cursor.execute("""
                SELECT COLUMN_NAME 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_SCHEMA = DATABASE() 
                AND TABLE_NAME = 'achievements' 
                AND COLUMN_NAME = 'description'
            """)
            has_description = await cursor.fetchone() is not None
            
            if has_description:
                query = """
                    SELECT 
                        a.id,
                        COALESCE(a.title, a.code) as name,
                        COALESCE(a.description, a.title, a.code) as description,
                        a.image_url as photo_url,
                        ua.earned_at as obtained_at,
                        ua.user_id
                    FROM achievements a
                    LEFT JOIN user_achievements ua ON a.id = ua.achievement_id AND ua.user_id = %s
                    ORDER BY 
                        CASE WHEN ua.earned_at IS NOT NULL THEN 0 ELSE 1 END,
                        ua.earned_at DESC,
                        a.title ASC, a.code ASC
                """
            else:
                query = """
                    SELECT 
                        a.id,
                        COALESCE(a.title, a.code) as name,
                        COALESCE(a.title, a.code) as description,
                        a.image_url as photo_url,
                        ua.earned_at as obtained_at,
                        ua.user_id
                    FROM achievements a
                    LEFT JOIN user_achievements ua ON a.id = ua.achievement_id AND ua.user_id = %s
                    ORDER BY 
                        CASE WHEN ua.earned_at IS NOT NULL THEN 0 ELSE 1 END,
                        ua.earned_at DESC,
                        a.title ASC, a.code ASC
                """
            
            await cursor.execute(query, (current_user.id,))
            
            results = await cursor.fetchall()
            
            achievements = []
            for row in results:
                achievements.append({
                    'id': row['id'],
                    'name': row.get('name') or '',
                    'description': row.get('description') or row.get('name') or '',
                    'photo_url': row.get('photo_url'),
                    'obtained_at': row['obtained_at'].isoformat() if row.get('obtained_at') else None,
                    'user_id': row.get('user_id'),
                })
            
            return achievements
    finally:
        conn.close()


def _check_admin_or_owner(current_user):
    role = current_user.get("role", "") if isinstance(current_user, dict) else getattr(current_user, "role", "")
    if role not in ("admin", "owner", "superadmin"):
        raise HTTPException(status_code=403, detail="Только админ или владелец может выполнить это действие")


@router.post("/achievements/create")
async def create_achievement(
    request: CreateAchievementRequest,
    current_user=Depends(get_current_user)
):
    """Создать новое достижение в каталоге (только для админов/владельцев)"""
    _check_admin_or_owner(current_user)
    conn = await get_db_connection()
    try:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # Если image_url не указан, пытаемся найти файл по коду
            image_url = request.image_url
            if not image_url:
                # Проверяем, есть ли файл с таким кодом в папке achievements
                for ext in ["png", "jpg", "jpeg", "webp", "gif"]:
                    file_path = os.path.join(settings.ACHIEVEMENTS_DIR, f"{request.code}.{ext}")
                    if os.path.exists(file_path):
                        image_url = f"/static/achievements/{request.code}.{ext}"
                        break
            
            await cursor.execute("""
                INSERT INTO achievements (code, title, description, image_url)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE 
                    title = VALUES(title),
                    description = COALESCE(VALUES(description), description),
                    image_url = COALESCE(VALUES(image_url), image_url)
            """, (request.code, request.title, request.description, image_url))
            
            await conn.commit()
            
            return {
                "status": "success", 
                "message": f"Достижение '{request.title}' добавлено/обновлено",
                "image_url": image_url
            }
    finally:
        conn.close()


@router.post("/achievements/award-to-user")
async def award_achievement_to_user(
    request: AwardAchievementRequest,
    current_user=Depends(get_current_user)
):
    """Выдать достижение пользователю (только для админов/владельцев)"""
    _check_admin_or_owner(current_user)
    conn = await get_db_connection()
    try:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # Находим ID достижения по коду
            await cursor.execute("SELECT id FROM achievements WHERE code = %s", (request.achievement_code,))
            row = await cursor.fetchone()
            
            if not row:
                raise HTTPException(status_code=404, detail=f"Достижение с кодом '{request.achievement_code}' не найдено")
            
            achievement_id = row['id']
            
            # Выдаем достижение пользователю
            await cursor.execute("""
                INSERT IGNORE INTO user_achievements (user_id, achievement_id, earned_at)
                VALUES (%s, %s, NOW())
            """, (request.user_id, achievement_id))
            
            await conn.commit()
            
            if cursor.rowcount > 0:
                return {"status": "success", "message": f"Достижение '{request.achievement_code}' выдано пользователю {request.user_id}"}
            else:
                return {"status": "already_exists", "message": f"Пользователь {request.user_id} уже имеет достижение '{request.achievement_code}'"}
    finally:
        conn.close()


@router.post("/achievements/upload-photo")
async def upload_achievement_photo(
    file: UploadFile = File(...),
    achievement_code: str = None,
    current_user=Depends(get_current_user)
):
    """Загрузить фото для достижения (только админы/владельцы)"""
    _check_admin_or_owner(current_user)
    
    ext = file.filename.split(".")[-1].lower() if file.filename else "png"
    if ext not in ["jpg", "jpeg", "png", "webp", "gif"]:
        raise HTTPException(status_code=400, detail="Неподдерживаемый тип файла. Используйте jpg, png, webp или gif")
    
    # Если передан код достижения, используем его, иначе генерируем имя из имени файла
    if achievement_code:
        filename = f"{achievement_code}.{ext}"
    else:
        # Используем имя файла без расширения как код
        base_name = os.path.splitext(file.filename)[0] if file.filename else "achievement"
        filename = f"{base_name}.{ext}"
    
    # Сохраняем файл
    file_path = os.path.join(settings.ACHIEVEMENTS_DIR, filename)
    with open(file_path, "wb") as buffer:
        content = await file.read()
        buffer.write(content)
    
    # Формируем URL
    image_url = f"/static/achievements/{filename}"
    
    return {
        "status": "success",
        "image_url": image_url,
        "filename": filename,
        "message": f"Фото сохранено: {image_url}"
    }
