from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from sqlalchemy import text
from app.core.security import get_current_user
from app.models.user import UserInDB
from app.models.lesson import (
    Lesson, LessonCreate, LessonUpdate, LessonWithDetails,
    LessonEnrollment, LessonEnrollmentCreate,
    LessonSchedule, LessonScheduleCreate,
    LessonReview, LessonReviewCreate
)
from app.db.connection import get_db_connection
import aiomysql

router = APIRouter()


@router.get("/lessons", response_model=List[LessonWithDetails])
async def get_lessons(
    subject: Optional[str] = Query(None),
    tutor_id: Optional[int] = Query(None),
    school_id: Optional[int] = Query(None),
    is_online: Optional[bool] = Query(None),
    current_user: UserInDB = Depends(get_current_user)
):
    """Получить список дополнительных занятий с фильтрацией"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Базовый запрос
            query = """
                SELECT 
                    l.*,
                    p.full_name as tutor_name,
                    s.name as school_name,
                    COUNT(e.id) as enrolled_count,
                    AVG(r.rating) as average_rating,
                    COUNT(r.id) as reviews_count
                FROM additional_lessons l
                LEFT JOIN profiles p ON l.tutor_id = p.user_id
                LEFT JOIN schools s ON l.school_id = s.id
                LEFT JOIN lesson_enrollments e ON l.id = e.lesson_id AND e.status = 'enrolled'
                LEFT JOIN lesson_reviews r ON l.id = r.lesson_id
                WHERE 1=1
            """
            params = []
            
            # Добавляем фильтры
            if subject:
                query += " AND l.subject = %s"
                params.append(subject)
            if tutor_id:
                query += " AND l.tutor_id = %s"
                params.append(tutor_id)
            if school_id:
                query += " AND l.school_id = %s"
                params.append(school_id)
            if is_online is not None:
                query += " AND l.is_online = %s"
                params.append(is_online)
            
            query += " GROUP BY l.id ORDER BY l.created_at DESC"
            
            await cursor.execute(query, params)
            lessons = await cursor.fetchall()
            
            return [LessonWithDetails(**lesson) for lesson in lessons]
    finally:
        conn.close()


@router.post("/lessons", response_model=Lesson)
async def create_lesson(
    lesson: LessonCreate,
    current_user: UserInDB = Depends(get_current_user)
):
    """Создать новое дополнительное занятие (только для репетиторов)"""
    if current_user.role not in ["tutor", "teacher", "admin"]:
        raise HTTPException(status_code=403, detail="Only tutors can create lessons")
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Создаем занятие
            await cursor.execute("""
                INSERT INTO additional_lessons 
                (title, description, subject, tutor_id, school_id, price, max_students, 
                 duration_minutes, is_online, location, online_link)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                lesson.title, lesson.description, lesson.subject, current_user.id,
                lesson.school_id, lesson.price, lesson.max_students,
                lesson.duration_minutes, lesson.is_online, lesson.location, lesson.online_link
            ))
            
            lesson_id = cursor.lastrowid
            await conn.commit()
            
            # Получаем созданное занятие
            await cursor.execute("SELECT * FROM additional_lessons WHERE id = %s", (lesson_id,))
            result = await cursor.fetchone()
            
            return Lesson(**result)
    finally:
        conn.close()


@router.get("/lessons/{lesson_id}", response_model=LessonWithDetails)
async def get_lesson(
    lesson_id: int,
    current_user: UserInDB = Depends(get_current_user)
):
    """Получить детали конкретного занятия"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT 
                    l.*,
                    p.full_name as tutor_name,
                    s.name as school_name,
                    COUNT(e.id) as enrolled_count,
                    AVG(r.rating) as average_rating,
                    COUNT(r.id) as reviews_count
                FROM additional_lessons l
                LEFT JOIN profiles p ON l.tutor_id = p.user_id
                LEFT JOIN schools s ON l.school_id = s.id
                LEFT JOIN lesson_enrollments e ON l.id = e.lesson_id AND e.status = 'enrolled'
                LEFT JOIN lesson_reviews r ON l.id = r.lesson_id
                WHERE l.id = %s
                GROUP BY l.id
            """, (lesson_id,))
            
            lesson = await cursor.fetchone()
            if not lesson:
                raise HTTPException(status_code=404, detail="Lesson not found")
            
            return LessonWithDetails(**lesson)
    finally:
        conn.close()


@router.put("/lessons/{lesson_id}", response_model=Lesson)
async def update_lesson(
    lesson_id: int,
    lesson_update: LessonUpdate,
    current_user: UserInDB = Depends(get_current_user)
):
    """Обновить занятие (только создатель или админ)"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем права доступа
            await cursor.execute("""
                SELECT tutor_id FROM additional_lessons WHERE id = %s
            """, (lesson_id,))
            lesson = await cursor.fetchone()
            
            if not lesson:
                raise HTTPException(status_code=404, detail="Lesson not found")
            
            if lesson["tutor_id"] != current_user.id and current_user.role != "admin":
                raise HTTPException(status_code=403, detail="Access denied")
            
            # Обновляем занятие
            update_fields = []
            params = []
            
            for field, value in lesson_update.dict(exclude_unset=True).items():
                update_fields.append(f"{field} = %s")
                params.append(value)
            
            if update_fields:
                params.append(lesson_id)
                await cursor.execute(f"""
                    UPDATE additional_lessons 
                    SET {', '.join(update_fields)}
                    WHERE id = %s
                """, params)
                await conn.commit()
            
            # Получаем обновленное занятие
            await cursor.execute("SELECT * FROM additional_lessons WHERE id = %s", (lesson_id,))
            result = await cursor.fetchone()
            
            return Lesson(**result)
    finally:
        conn.close()


@router.delete("/lessons/{lesson_id}")
async def delete_lesson(
    lesson_id: int,
    current_user: UserInDB = Depends(get_current_user)
):
    """Удалить занятие (только создатель или админ)"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем права доступа
            await cursor.execute("""
                SELECT tutor_id FROM additional_lessons WHERE id = %s
            """, (lesson_id,))
            lesson = await cursor.fetchone()
            
            if not lesson:
                raise HTTPException(status_code=404, detail="Lesson not found")
            
            if lesson["tutor_id"] != current_user.id and current_user.role != "admin":
                raise HTTPException(status_code=403, detail="Access denied")
            
            # Удаляем занятие
            await cursor.execute("DELETE FROM additional_lessons WHERE id = %s", (lesson_id,))
            await conn.commit()
            
            return {"message": "Lesson deleted successfully"}
    finally:
        conn.close()


@router.post("/lessons/{lesson_id}/enroll", response_model=LessonEnrollment)
async def enroll_in_lesson(
    lesson_id: int,
    current_user: UserInDB = Depends(get_current_user)
):
    """Записаться на занятие (только студенты)"""
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Only students can enroll in lessons")
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем, что занятие существует и есть свободные места
            await cursor.execute("""
                SELECT max_students, COUNT(e.id) as enrolled_count
                FROM additional_lessons l
                LEFT JOIN lesson_enrollments e ON l.id = e.lesson_id AND e.status = 'enrolled'
                WHERE l.id = %s
                GROUP BY l.id
            """, (lesson_id,))
            
            lesson_info = await cursor.fetchone()
            if not lesson_info:
                raise HTTPException(status_code=404, detail="Lesson not found")
            
            if lesson_info["enrolled_count"] >= lesson_info["max_students"]:
                raise HTTPException(status_code=400, detail="Lesson is full")
            
            # Проверяем, не записан ли уже студент
            await cursor.execute("""
                SELECT id FROM lesson_enrollments 
                WHERE lesson_id = %s AND student_id = %s
            """, (lesson_id, current_user.id))
            
            if await cursor.fetchone():
                raise HTTPException(status_code=400, detail="Already enrolled")
            
            # Записываем студента
            await cursor.execute("""
                INSERT INTO lesson_enrollments (lesson_id, student_id)
                VALUES (%s, %s)
            """, (lesson_id, current_user.id))
            
            enrollment_id = cursor.lastrowid
            await conn.commit()
            
            # Получаем запись
            await cursor.execute("""
                SELECT * FROM lesson_enrollments WHERE id = %s
            """, (enrollment_id,))
            result = await cursor.fetchone()
            
            return LessonEnrollment(**result)
    finally:
        conn.close()


@router.get("/lessons/{lesson_id}/enrollments", response_model=List[LessonEnrollment])
async def get_lesson_enrollments(
    lesson_id: int,
    current_user: UserInDB = Depends(get_current_user)
):
    """Получить список записавшихся на занятие (только создатель или админ)"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем права доступа
            await cursor.execute("""
                SELECT tutor_id FROM additional_lessons WHERE id = %s
            """, (lesson_id,))
            lesson = await cursor.fetchone()
            
            if not lesson:
                raise HTTPException(status_code=404, detail="Lesson not found")
            
            if lesson["tutor_id"] != current_user.id and current_user.role != "admin":
                raise HTTPException(status_code=403, detail="Access denied")
            
            # Получаем записи
            await cursor.execute("""
                SELECT * FROM lesson_enrollments 
                WHERE lesson_id = %s 
                ORDER BY enrolled_at DESC
            """, (lesson_id,))
            
            enrollments = await cursor.fetchall()
            return [LessonEnrollment(**enrollment) for enrollment in enrollments]
    finally:
        conn.close()


@router.get("/my-lessons", response_model=List[LessonWithDetails])
async def get_my_lessons(
    current_user: UserInDB = Depends(get_current_user)
):
    """Получить мои занятия (созданные мной или на которые я записан)"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            if current_user.role in ["tutor", "teacher"]:
                # Для репетиторов - занятия, которые они создали
                await cursor.execute("""
                    SELECT 
                        l.*,
                        p.full_name as tutor_name,
                        s.name as school_name,
                        COUNT(e.id) as enrolled_count,
                        AVG(r.rating) as average_rating,
                        COUNT(r.id) as reviews_count
                    FROM additional_lessons l
                    LEFT JOIN profiles p ON l.tutor_id = p.user_id
                    LEFT JOIN schools s ON l.school_id = s.id
                    LEFT JOIN lesson_enrollments e ON l.id = e.lesson_id AND e.status = 'enrolled'
                    LEFT JOIN lesson_reviews r ON l.id = r.lesson_id
                    WHERE l.tutor_id = %s
                    GROUP BY l.id
                    ORDER BY l.created_at DESC
                """, (current_user.id,))
            else:
                # Для студентов - занятия, на которые они записаны
                await cursor.execute("""
                    SELECT 
                        l.*,
                        p.full_name as tutor_name,
                        s.name as school_name,
                        COUNT(e.id) as enrolled_count,
                        AVG(r.rating) as average_rating,
                        COUNT(r.id) as reviews_count
                    FROM additional_lessons l
                    LEFT JOIN profiles p ON l.tutor_id = p.user_id
                    LEFT JOIN schools s ON l.school_id = s.id
                    LEFT JOIN lesson_enrollments e ON l.id = e.lesson_id AND e.status = 'enrolled'
                    LEFT JOIN lesson_reviews r ON l.id = r.lesson_id
                    INNER JOIN lesson_enrollments my_enrollment ON l.id = my_enrollment.lesson_id
                    WHERE my_enrollment.student_id = %s
                    GROUP BY l.id
                    ORDER BY l.created_at DESC
                """, (current_user.id,))
            
            lessons = await cursor.fetchall()
            return [LessonWithDetails(**lesson) for lesson in lessons]
    finally:
        conn.close()
