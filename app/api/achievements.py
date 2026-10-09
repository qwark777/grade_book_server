from typing import List, Optional
import os
import uuid
import aiomysql
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel

from app.core.security import get_current_user
from app.core.config import settings
from app.core.entitlements import get_school_id_for_user
from app.core.file_uploads import IMAGE_EXTS, MAX_IMAGE_BYTES, safe_original_filename, save_upload_limited, validate_upload_metadata
from app.db.connection import get_db_connection
from app.services.achievement_service import (
    can_manually_award_achievement,
    evaluate_and_award_achievements,
    notify_achievement_awarded,
)

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
    code: Optional[str] = None
    name: str
    description: str
    photo_url: Optional[str] = None
    obtained_at: Optional[str] = None
    user_id: Optional[int] = None

    class Config:
        from_attributes = True


class AchievementProgressResponse(BaseModel):
    achievement_id: int
    code: str
    current: int
    target: int
    label: str
    unit: Optional[str] = None
    is_completed: bool = False


def _current_user_id(current_user) -> int:
    if isinstance(current_user, dict):
        return int(current_user.get("id") or current_user.get("user_id") or 0)
    return int(getattr(current_user, "id", 0) or 0)


async def _scalar(cursor, query: str, params: tuple = ()) -> int:
    await cursor.execute(query, params)
    row = await cursor.fetchone()
    if not row:
        return 0
    value = next(iter(row.values())) if isinstance(row, dict) else row[0]
    return int(value or 0)


async def _has_table(cursor, table_name: str) -> bool:
    await cursor.execute(
        """
        SELECT 1
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
        LIMIT 1
        """,
        (table_name,),
    )
    return await cursor.fetchone() is not None


def _progress_item(
    achievement_id: int,
    code: str,
    current: int,
    target: int,
    label: str,
    unit: Optional[str] = None,
    is_completed: bool = False,
) -> dict:
    safe_target = max(1, int(target))
    safe_current = max(0, min(int(current), safe_target))
    return {
        "achievement_id": int(achievement_id),
        "code": code,
        "current": safe_current,
        "target": safe_target,
        "label": label,
        "unit": unit,
        "is_completed": bool(is_completed or safe_current >= safe_target),
    }


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
                        a.code,
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
                        a.code,
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
                    'code': row.get('code'),
                    'name': row.get('name') or '',
                    'description': row.get('description') or row.get('name') or '',
                    'photo_url': row.get('photo_url'),
                    'obtained_at': row['obtained_at'].isoformat() if row.get('obtained_at') else None,
                    'user_id': row.get('user_id'),
                })
            
            return achievements
    finally:
        conn.close()


@router.get("/achievements/progress/me", response_model=List[AchievementProgressResponse])
async def get_my_achievement_progress(current_user=Depends(get_current_user)):
    """Progress counters for achievements that can be measured from app data."""
    user_id = _current_user_id(current_user)
    if user_id <= 0:
        raise HTTPException(status_code=401, detail="Invalid user")
    return await evaluate_and_award_achievements(user_id)

    conn = await get_db_connection()
    try:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("SELECT id, code FROM achievements")
            achievement_rows = await cursor.fetchall()
            achievements = {
                (row.get("code") or ""): int(row.get("id") or 0)
                for row in achievement_rows
                if row.get("code")
            }
            if not achievements:
                return []

            completed_codes = set()
            await cursor.execute(
                """
                SELECT a.code
                FROM user_achievements ua
                JOIN achievements a ON a.id = ua.achievement_id
                WHERE ua.user_id = %s
                """,
                (user_id,),
            )
            for row in await cursor.fetchall():
                if row.get("code"):
                    completed_codes.add(row["code"])

            has_grades = await _has_table(cursor, "grades")
            has_homework_submissions = await _has_table(cursor, "homework_submissions")
            has_homework_done_marks = await _has_table(cursor, "homework_done_marks")
            has_messages = await _has_table(cursor, "messages")
            has_attendance = await _has_table(cursor, "attendance")

            grade_count = (
                await _scalar(
                    cursor,
                    "SELECT COUNT(*) AS value FROM grades WHERE student_id = %s",
                    (user_id,),
                )
                if has_grades
                else 0
            )
            five_count = (
                await _scalar(
                    cursor,
                    "SELECT COUNT(*) AS value FROM grades WHERE student_id = %s AND value = 5",
                    (user_id,),
                )
                if has_grades
                else 0
            )
            excellent_percent = round((five_count / grade_count) * 100) if grade_count else 0

            homework_completed = 0
            homework_queries = []
            if has_homework_submissions:
                homework_queries.append(
                    "SELECT homework_id FROM homework_submissions WHERE student_id = %s"
                )
            if has_homework_done_marks:
                homework_queries.append(
                    "SELECT homework_id FROM homework_done_marks WHERE student_id = %s"
                )
            if homework_queries:
                union_sql = " UNION ".join(homework_queries)
                homework_completed = await _scalar(
                    cursor,
                    f"SELECT COUNT(DISTINCT homework_id) AS value FROM ({union_sql}) done_homeworks",
                    tuple([user_id] * len(homework_queries)),
                )

            sent_messages = (
                await _scalar(
                    cursor,
                    "SELECT COUNT(*) AS value FROM messages WHERE sender_id = %s",
                    (user_id,),
                )
                if has_messages
                else 0
            )

            attendance_total = attendance_present = 0
            if has_attendance:
                attendance_total = await _scalar(
                    cursor,
                    """
                    SELECT COUNT(*) AS value
                    FROM attendance
                    WHERE student_id = %s
                      AND date >= DATE_FORMAT(CURDATE(), '%%Y-%%m-01')
                    """,
                    (user_id,),
                )
                attendance_present = await _scalar(
                    cursor,
                    """
                    SELECT COUNT(*) AS value
                    FROM attendance
                    WHERE student_id = %s
                      AND date >= DATE_FORMAT(CURDATE(), '%%Y-%%m-01')
                      AND status = 'present'
                    """,
                    (user_id,),
                )
            attendance_percent = (
                round((attendance_present / attendance_total) * 100)
                if attendance_total
                else 0
            )

            activity_days = 0
            activity_queries = []
            if has_grades:
                activity_queries.append(
                    "SELECT date AS day FROM grades WHERE student_id = %s AND date >= DATE_SUB(CURDATE(), INTERVAL 6 DAY)"
                )
            if has_homework_submissions:
                activity_queries.append(
                    "SELECT DATE(created_at) AS day FROM homework_submissions WHERE student_id = %s AND created_at >= DATE_SUB(NOW(), INTERVAL 6 DAY)"
                )
            if has_messages:
                activity_queries.append(
                    "SELECT DATE(created_at) AS day FROM messages WHERE sender_id = %s AND created_at >= DATE_SUB(NOW(), INTERVAL 6 DAY)"
                )
            if has_attendance:
                activity_queries.append(
                    "SELECT date AS day FROM attendance WHERE student_id = %s AND date >= DATE_SUB(CURDATE(), INTERVAL 6 DAY)"
                )
            if activity_queries:
                union_sql = " UNION ".join(activity_queries)
                activity_days = await _scalar(
                    cursor,
                    f"SELECT COUNT(DISTINCT day) AS value FROM ({union_sql}) activity",
                    tuple([user_id] * len(activity_queries)),
                )

            user_points = 0
            first_place_points = 0
            second_place_points = 0
            third_place_points = 0
            user_rank = 0
            if await _has_table(cursor, "user_points"):
                user_points = await _scalar(
                    cursor,
                    "SELECT COALESCE(SUM(points), 0) AS value FROM user_points WHERE user_id = %s",
                    (user_id,),
                )
                await cursor.execute(
                    """
                    SELECT user_id, COALESCE(SUM(points), 0) AS total_points
                    FROM user_points
                    GROUP BY user_id
                    ORDER BY total_points DESC, user_id ASC
                    LIMIT 3
                    """
                )
                leaders = await cursor.fetchall()
                leader_points = [int(row.get("total_points") or 0) for row in leaders]
                first_place_points = leader_points[0] if len(leader_points) > 0 else user_points
                second_place_points = leader_points[1] if len(leader_points) > 1 else first_place_points
                third_place_points = leader_points[2] if len(leader_points) > 2 else second_place_points
                user_rank = await _scalar(
                    cursor,
                    """
                    SELECT COUNT(*) + 1 AS value
                    FROM (
                        SELECT user_id, COALESCE(SUM(points), 0) AS total_points
                        FROM user_points
                        GROUP BY user_id
                    ) ranked
                    WHERE total_points > %s
                    """,
                    (user_points,),
                )

            good_feedback_count = (
                await _scalar(
                    cursor,
                    """
                    SELECT COUNT(*) AS value
                    FROM teacher_lesson_feedback
                    WHERE teacher_id = %s AND rating >= 4
                    """,
                    (user_id,),
                )
                if await _has_table(cursor, "teacher_lesson_feedback")
                else 0
            )
            created_homeworks = (
                await _scalar(
                    cursor,
                    "SELECT COUNT(*) AS value FROM homeworks WHERE teacher_id = %s",
                    (user_id,),
                )
                if await _has_table(cursor, "homeworks")
                else 0
            )
            teacher_fives = (
                await _scalar(
                    cursor,
                    "SELECT COUNT(*) AS value FROM grades WHERE teacher_id = %s AND value = 5",
                    (user_id,),
                )
                if has_grades
                else 0
            )
            olympiad_activity = max(created_homeworks, teacher_fives, five_count)

            progress = []
            if "class_president" in achievements:
                progress.append(
                    _progress_item(
                        achievements["class_president"],
                        "class_president",
                        activity_days,
                        7,
                        "Активных дней за неделю",
                        "дней",
                        "class_president" in completed_codes,
                    )
                )
            if "first_place" in achievements:
                progress.append(
                    _progress_item(
                        achievements["first_place"],
                        "first_place",
                        user_points,
                        max(1, first_place_points),
                        "Баллы до 1 места",
                        "баллов",
                        "first_place" in completed_codes or (user_rank == 1 and user_points > 0),
                    )
                )
            if "second_place" in achievements:
                progress.append(
                    _progress_item(
                        achievements["second_place"],
                        "second_place",
                        user_points,
                        max(1, second_place_points),
                        "Баллы до 2 места",
                        "баллов",
                        "second_place" in completed_codes or (0 < user_rank <= 2 and user_points > 0),
                    )
                )
            if "third_place" in achievements:
                progress.append(
                    _progress_item(
                        achievements["third_place"],
                        "third_place",
                        user_points,
                        max(1, third_place_points),
                        "Баллы до 3 места",
                        "баллов",
                        "third_place" in completed_codes or (0 < user_rank <= 3 and user_points > 0),
                    )
                )
            if "heart_of_class" in achievements:
                progress.append(
                    _progress_item(
                        achievements["heart_of_class"],
                        "heart_of_class",
                        sent_messages,
                        100,
                        "Сообщений в чатах",
                        "сообщений",
                        "heart_of_class" in completed_codes,
                    )
                )
            if "politeness" in achievements:
                progress.append(
                    _progress_item(
                        achievements["politeness"],
                        "politeness",
                        good_feedback_count,
                        20,
                        "Хороших отзывов",
                        "отзывов",
                        "politeness" in completed_codes,
                    )
                )
            if "olimpiad_coach" in achievements:
                progress.append(
                    _progress_item(
                        achievements["olimpiad_coach"],
                        "olimpiad_coach",
                        olympiad_activity,
                        30,
                        "Учебных побед",
                        "побед",
                        "olimpiad_coach" in completed_codes,
                    )
                )
            if "olympiad_coach" in achievements:
                progress.append(
                    _progress_item(
                        achievements["olympiad_coach"],
                        "olympiad_coach",
                        olympiad_activity,
                        30,
                        "Учебных побед",
                        "побед",
                        "olympiad_coach" in completed_codes,
                    )
                )
            if "first_grade" in achievements:
                progress.append(
                    _progress_item(
                        achievements["first_grade"],
                        "first_grade",
                        grade_count,
                        1,
                        "Первая оценка",
                        "оценка",
                        "first_grade" in completed_codes,
                    )
                )
            if "homework_master" in achievements:
                progress.append(
                    _progress_item(
                        achievements["homework_master"],
                        "homework_master",
                        homework_completed,
                        50,
                        "Выполнено ДЗ",
                        "ДЗ",
                        "homework_master" in completed_codes,
                    )
                )
            if "social_butterfly" in achievements:
                progress.append(
                    _progress_item(
                        achievements["social_butterfly"],
                        "social_butterfly",
                        sent_messages,
                        100,
                        "Отправлено сообщений",
                        "сообщений",
                        "social_butterfly" in completed_codes,
                    )
                )
            if "week_warrior" in achievements:
                progress.append(
                    _progress_item(
                        achievements["week_warrior"],
                        "week_warrior",
                        activity_days,
                        7,
                        "Активных дней",
                        "дней",
                        "week_warrior" in completed_codes,
                    )
                )
            if "perfect_attendance" in achievements:
                progress.append(
                    _progress_item(
                        achievements["perfect_attendance"],
                        "perfect_attendance",
                        attendance_percent,
                        100,
                        "Посещаемость за месяц",
                        "%",
                        "perfect_attendance" in completed_codes,
                    )
                )
            if "excellent_student" in achievements:
                progress.append(
                    _progress_item(
                        achievements["excellent_student"],
                        "excellent_student",
                        excellent_percent,
                        100,
                        "Оценок «5»",
                        "%",
                        "excellent_student" in completed_codes,
                    )
                )
            newly_completed = [
                item
                for item in progress
                if item["is_completed"] and item["code"] not in completed_codes
            ]
            if newly_completed:
                await cursor.executemany(
                    """
                    INSERT IGNORE INTO user_achievements
                    (user_id, achievement_id, earned_at)
                    VALUES (%s, %s, NOW())
                    """,
                    [
                        (user_id, item["achievement_id"])
                        for item in newly_completed
                    ],
                )
                await conn.commit()
            return progress
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
                        a.code,
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
                        a.code,
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
                    'code': row.get('code'),
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
    if role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Только админ или владелец может выполнить это действие")


def _check_owner(current_user):
    role = current_user.get("role", "") if isinstance(current_user, dict) else getattr(current_user, "role", "")
    if role not in ("owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Только владелец может выполнить это действие")


async def _ensure_target_user_scope(cursor, current_user, user_id: int) -> dict:
    role = current_user.get("role", "") if isinstance(current_user, dict) else getattr(current_user, "role", "")
    await cursor.execute("SELECT id, role FROM users WHERE id = %s", (user_id,))
    target_user = await cursor.fetchone()
    if not target_user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if role in ("owner", "superadmin", "root"):
        return target_user

    school_id = await get_school_id_for_user(current_user)
    if school_id is None:
        raise HTTPException(status_code=403, detail="Нет доступа к школе")
    await cursor.execute(
        """
        SELECT 1
        FROM users u
        LEFT JOIN school_admins sa ON sa.admin_user_id = u.id
        LEFT JOIN class_students cs ON cs.student_id = u.id
        LEFT JOIN classes student_class ON student_class.id = cs.class_id
        LEFT JOIN class_teachers ct ON ct.teacher_id = u.id
        LEFT JOIN classes teacher_class ON teacher_class.id = ct.class_id
        LEFT JOIN class_subject_teachers cst ON cst.teacher_id = u.id
        LEFT JOIN classes subject_teacher_class ON subject_teacher_class.id = cst.class_id
        WHERE u.id = %s
          AND (
              sa.school_id = %s
              OR student_class.school_id = %s
              OR teacher_class.school_id = %s
              OR subject_teacher_class.school_id = %s
          )
        LIMIT 1
        """,
        (user_id, school_id, school_id, school_id, school_id),
    )
    if not await cursor.fetchone():
        raise HTTPException(status_code=403, detail="Нет доступа к пользователю")
    return target_user


@router.post("/achievements/create")
async def create_achievement(
    request: CreateAchievementRequest,
    current_user=Depends(get_current_user)
):
    """Создать новое глобальное достижение в каталоге."""
    _check_owner(current_user)
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
                AS incoming
                ON DUPLICATE KEY UPDATE 
                    title = incoming.title,
                    description = COALESCE(incoming.description, description),
                    image_url = COALESCE(incoming.image_url, image_url)
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
            target_user = await _ensure_target_user_scope(
                cursor,
                current_user,
                request.user_id,
            )
            # Находим ID достижения по коду
            await cursor.execute(
                "SELECT id, title FROM achievements WHERE code = %s",
                (request.achievement_code,),
            )
            row = await cursor.fetchone()
            
            if not row:
                raise HTTPException(status_code=404, detail=f"Достижение с кодом '{request.achievement_code}' не найдено")
            if not can_manually_award_achievement(
                request.achievement_code,
                target_user["role"],
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Это достижение выдаётся автоматически или недоступно для роли пользователя",
                )
            
            achievement_id = row['id']
            achievement_title = row.get("title") or request.achievement_code
            
            # Выдаем достижение пользователю
            await cursor.execute("""
                INSERT IGNORE INTO user_achievements (user_id, achievement_id, earned_at)
                VALUES (%s, %s, NOW())
            """, (request.user_id, achievement_id))
            inserted = cursor.rowcount > 0
            
            await conn.commit()
            
            if inserted:
                await notify_achievement_awarded(
                    request.user_id,
                    achievement_id,
                    achievement_title,
                )
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
    """Загрузить фото для глобального достижения."""
    _check_owner(current_user)
    
    safe_name, ext = validate_upload_metadata(
        file,
        allowed_exts=IMAGE_EXTS,
        allowed_mime_prefixes=("image/",),
    )
    
    # Если передан код достижения, используем его, иначе генерируем имя из имени файла
    if achievement_code:
        filename = safe_original_filename(f"{achievement_code}.{ext}", f"achievement.{ext}")
    else:
        # Используем имя файла без расширения как код
        base_name = os.path.splitext(safe_original_filename(safe_name, "achievement"))[0]
        filename = f"{base_name}-{uuid.uuid4().hex[:10]}.{ext}"
    
    # Сохраняем файл
    file_path = os.path.join(settings.ACHIEVEMENTS_DIR, filename)
    await save_upload_limited(file, file_path, max_bytes=MAX_IMAGE_BYTES)
    
    # Формируем URL
    image_url = f"/static/achievements/{filename}"
    
    return {
        "status": "success",
        "image_url": image_url,
        "filename": filename,
        "message": f"Фото сохранено: {image_url}"
    }
