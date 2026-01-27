from datetime import date, datetime, timedelta
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.security import get_current_user
from app.db.connection import get_db_connection
from app.db.timetable_operations import get_week_schedule, get_rooms, create_timetable_change, create_holiday, get_teacher_week_schedule
from app.models.timetable import WeekSchedule, Room, CreateTimetableChangeRequest, CreateHolidayRequest, TimetableTemplateRequest

router = APIRouter()


@router.get("/timetable/week", response_model=WeekSchedule)
async def get_timetable_week(
    class_id: int = Query(..., description="ID класса"),
    week_start_date: str = Query(..., description="Дата начала недели (YYYY-MM-DD)"),
    current_user=Depends(get_current_user)
):
    """Получить расписание на неделю для класса"""
    try:
        week_date = datetime.strptime(week_start_date, "%Y-%m-%d").date()
        return await get_week_schedule(class_id, week_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверный формат даты. Используйте YYYY-MM-DD")


@router.get("/timetable/current-week", response_model=WeekSchedule)
async def get_current_week_timetable(
    class_id: int = Query(..., description="ID класса"),
    current_user=Depends(get_current_user)
):
    """Получить расписание на текущую неделю"""
    # Получаем понедельник текущей недели
    today = date.today()
    days_since_monday = today.weekday()
    monday = today - timedelta(days=days_since_monday)
    
    return await get_week_schedule(class_id, monday)


@router.get("/timetable/next-week", response_model=WeekSchedule)
async def get_next_week_timetable(
    class_id: int = Query(..., description="ID класса"),
    current_user=Depends(get_current_user)
):
    """Получить расписание на следующую неделю"""
    today = date.today()
    days_since_monday = today.weekday()
    monday = today - timedelta(days=days_since_monday)
    next_monday = monday + timedelta(days=7)
    
    return await get_week_schedule(class_id, next_monday)


@router.get("/timetable/previous-week", response_model=WeekSchedule)
async def get_previous_week_timetable(
    class_id: int = Query(..., description="ID класса"),
    current_user=Depends(get_current_user)
):
    """Получить расписание на предыдущую неделю"""
    today = date.today()
    days_since_monday = today.weekday()
    monday = today - timedelta(days=days_since_monday)
    prev_monday = monday - timedelta(days=7)
    
    return await get_week_schedule(class_id, prev_monday)


@router.get("/timetable/teacher/week", response_model=WeekSchedule)
async def get_teacher_timetable_week(
    week_start_date: str = Query(..., description="Дата начала недели (YYYY-MM-DD)"),
    current_user=Depends(get_current_user)
):
    """Получить расписание учителя (классы вместо предметов)"""
    try:
        week_date = datetime.strptime(week_start_date, "%Y-%m-%d").date()
        teacher_id = current_user.id if hasattr(current_user, 'id') else current_user["id"]
        return await get_teacher_week_schedule(teacher_id, week_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверный формат даты. Используйте YYYY-MM-DD")


@router.get("/rooms", response_model=List[Room])
async def get_rooms_list(current_user=Depends(get_current_user)):
    """Получить список всех комнат/кабинетов"""
    return await get_rooms()


@router.get("/timetable/template")
async def get_timetable_template(
    class_id: int = Query(..., description="ID класса"),
    current_user=Depends(get_current_user)
):
    """Получить шаблон расписания для класса"""
    import logging
    logger = logging.getLogger(__name__)
    try:
        conn = await get_db_connection()
        try:
            async with conn.cursor() as cursor:
                # Получаем активный учебный период
                await cursor.execute("""
                    SELECT id FROM academic_periods 
                    WHERE is_active = TRUE 
                    LIMIT 1
                """)
                period_row = await cursor.fetchone()
                if not period_row:
                    await cursor.execute("""
                        SELECT id FROM academic_periods 
                        ORDER BY id DESC 
                        LIMIT 1
                    """)
                    period_row = await cursor.fetchone()
                
                period_id = None
                if period_row:
                    period_id = period_row[0] if isinstance(period_row, (list, tuple)) else period_row.get("id")
                
                # Получаем шаблон расписания для класса
                if period_id:
                    await cursor.execute("""
                        SELECT 
                            t.day_of_week, t.lesson_number, t.start_time, t.end_time,
                            s.name AS subject_name,
                            p.full_name AS teacher_name,
                            r.name AS room_name
                        FROM timetable_templates t
                        LEFT JOIN subjects s ON s.id = t.subject_id
                        LEFT JOIN profiles p ON p.user_id = t.teacher_id
                        LEFT JOIN rooms r ON r.id = t.room_id
                        WHERE t.class_id = %s AND t.academic_period_id = %s
                        ORDER BY t.day_of_week, t.lesson_number
                    """, (class_id, period_id))
                else:
                    await cursor.execute("""
                        SELECT 
                            t.day_of_week, t.lesson_number, t.start_time, t.end_time,
                            s.name AS subject_name,
                            p.full_name AS teacher_name,
                            r.name AS room_name
                        FROM timetable_templates t
                        LEFT JOIN subjects s ON s.id = t.subject_id
                        LEFT JOIN profiles p ON p.user_id = t.teacher_id
                        LEFT JOIN rooms r ON r.id = t.room_id
                        WHERE t.class_id = %s
                        ORDER BY t.day_of_week, t.lesson_number
                    """, (class_id,))
                
                rows = await cursor.fetchall()
                
                # Преобразуем в формат LessonTemplateData
                lessons = []
                for row in rows:
                    lessons.append({
                        "day_of_week": row['day_of_week'],
                        "lesson_number": row['lesson_number'],
                        "start_time": str(row['start_time']),
                        "end_time": str(row['end_time']),
                        "subject": row['subject_name'] or "",
                        "teacher": row['teacher_name'] or "",
                        "room": row['room_name'] or ""
                    })
                
                return {
                    "class_id": class_id,
                    "lessons": lessons
                }
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Error getting timetable template: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при получении шаблона: {str(e)}")


@router.post("/timetable/template")
async def save_timetable_template(
    template_data: TimetableTemplateRequest,
    current_user=Depends(get_current_user)
):
    """Сохранить шаблон расписания для класса"""
    import logging
    logger = logging.getLogger(__name__)
    try:
        logger.info(f"Saving timetable template for class_id={template_data.class_id}, lessons={len(template_data.lessons)}")
        conn = await get_db_connection()
        
        try:
            async with conn.cursor() as cursor:
                # Получаем активный учебный период
                await cursor.execute("""
                    SELECT id FROM academic_periods 
                    WHERE is_active = TRUE 
                    LIMIT 1
                """)
                period_row = await cursor.fetchone()
                if not period_row:
                    # Если нет активного периода, создаем или используем первый
                    await cursor.execute("""
                        SELECT id FROM academic_periods 
                        ORDER BY id DESC 
                        LIMIT 1
                    """)
                    period_row = await cursor.fetchone()
                    if not period_row:
                        # Создаем дефолтный период
                        today = date.today()
                        end_date = date(today.year + 1, 6, 30)
                        await cursor.execute("""
                            INSERT INTO academic_periods (name, start_date, end_date, is_active)
                            VALUES (%s, %s, %s, %s)
                        """, ("Учебный год", today, end_date, True))
                        await conn.commit()
                        period_id = cursor.lastrowid
                    else:
                        period_id = period_row[0] if isinstance(period_row, (list, tuple)) else period_row.get("id")
                else:
                    period_id = period_row[0] if isinstance(period_row, (list, tuple)) else period_row.get("id")
                
                # Сначала удаляем существующий шаблон для этого класса
                await cursor.execute("""
                    DELETE FROM timetable_templates 
                    WHERE class_id = %s AND academic_period_id = %s
                """, (template_data.class_id, period_id))
                
                # Добавляем новые уроки
                for lesson in template_data.lessons:
                    # Находим ID предмета по имени
                    await cursor.execute("SELECT id FROM subjects WHERE name = %s LIMIT 1", (lesson.subject,))
                    subject_row = await cursor.fetchone()
                    if not subject_row:
                        raise HTTPException(status_code=400, detail=f"Предмет '{lesson.subject}' не найден")
                    subject_id = subject_row[0] if isinstance(subject_row, (list, tuple)) else subject_row.get("id")
                    
                    # Находим ID учителя по имени (из профиля)
                    await cursor.execute("""
                        SELECT u.id FROM users u
                        JOIN profiles p ON p.user_id = u.id
                        WHERE p.full_name = %s AND u.role = 'teacher'
                        LIMIT 1
                    """, (lesson.teacher,))
                    teacher_row = await cursor.fetchone()
                    if not teacher_row:
                        raise HTTPException(status_code=400, detail=f"Учитель '{lesson.teacher}' не найден")
                    teacher_id = teacher_row[0] if isinstance(teacher_row, (list, tuple)) else teacher_row.get("id")
                    
                    # Находим ID кабинета по имени (если указан)
                    room_id = None
                    if lesson.room and lesson.room.strip():
                        await cursor.execute("SELECT id FROM rooms WHERE name = %s LIMIT 1", (lesson.room,))
                        room_row = await cursor.fetchone()
                        if room_row:
                            room_id = room_row[0] if isinstance(room_row, (list, tuple)) else room_row.get("id")
                    
                    # Вставляем урок
                    await cursor.execute("""
                        INSERT INTO timetable_templates 
                        (class_id, subject_id, teacher_id, room_id, day_of_week, lesson_number, 
                         start_time, end_time, week_type, academic_period_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'BOTH', %s)
                    """, (
                        template_data.class_id,
                        subject_id,
                        teacher_id,
                        room_id,
                        lesson.day_of_week,
                        lesson.lesson_number,
                        lesson.start_time,
                        lesson.end_time,
                        period_id
                    ))
                
                await conn.commit()
                logger.info(f"Successfully saved timetable template for class_id={template_data.class_id}")
                return {"message": "Шаблон расписания сохранен успешно"}
                
        finally:
            conn.close()
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving timetable template: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при сохранении шаблона: {str(e)}")


@router.post("/timetable/changes")
async def create_timetable_change_endpoint(
    change_data: CreateTimetableChangeRequest,
    current_user=Depends(get_current_user)
):
    """Создать замену в расписании"""
    success = await create_timetable_change(
        date=change_data.date,
        class_id=change_data.class_id,
        lesson_number=change_data.lesson_number,
        change_type=change_data.change_type.value,
        created_by=current_user.id,
        original_teacher_id=change_data.original_teacher_id,
        new_teacher_id=change_data.new_teacher_id,
        original_subject_id=change_data.original_subject_id,
        new_subject_id=change_data.new_subject_id,
        original_room_id=change_data.original_room_id,
        new_room_id=change_data.new_room_id,
        reason=change_data.reason
    )
    
    if success:
        return {"message": "Замена создана успешно"}
    else:
        raise HTTPException(status_code=500, detail="Ошибка при создании замены")


@router.post("/holidays")
async def create_holiday_endpoint(
    holiday_data: CreateHolidayRequest,
    current_user=Depends(get_current_user)
):
    """Создать праздник/выходной"""
    success = await create_holiday(
        date=holiday_data.date,
        name=holiday_data.name,
        holiday_type=holiday_data.type.value,
        affects_classes=holiday_data.affects_classes,
        description=holiday_data.description
    )
    
    if success:
        return {"message": "Праздник создан успешно"}
    else:
        raise HTTPException(status_code=500, detail="Ошибка при создании праздника")


@router.get("/timetable/week-info")
async def get_week_info(
    week_start_date: str = Query(..., description="Дата начала недели (YYYY-MM-DD)"),
    current_user=Depends(get_current_user)
):
    """Получить информацию о неделе (номер, тип A/B)"""
    try:
        week_date = datetime.strptime(week_start_date, "%Y-%m-%d").date()
        conn = await get_db_connection()
        
        try:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    SELECT week_number, week_type, is_holiday_week
                    FROM academic_weeks 
                    WHERE week_start_date = %s
                """, (week_date,))
                week_info = await cursor.fetchone()
                
                if week_info:
                    return {
                        "week_number": week_info['week_number'],
                        "week_type": week_info['week_type'],
                        "is_holiday_week": week_info['is_holiday_week']
                    }
                else:
                    # Если неделя не найдена в БД, вычисляем базовую информацию
                    week_number = ((week_date - date(2024, 1, 1)).days // 7) + 1
                    week_type = 'A' if week_number % 2 == 1 else 'B'
                    return {
                        "week_number": week_number,
                        "week_type": week_type,
                        "is_holiday_week": False
                    }
        finally:
            conn.close()
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверный формат даты. Используйте YYYY-MM-DD")
