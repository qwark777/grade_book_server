from datetime import date, datetime, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.security import get_current_user
from app.core.entitlements import get_school_id_for_user
from app.db.connection import get_db_connection
from app.db.timetable_operations import get_week_schedule, get_rooms, create_timetable_change, create_holiday, get_holidays, delete_holiday, get_teacher_week_schedule
from app.db.timetable_generator import CurriculumItem, generate_timetable, PlacedLesson
from app.models.timetable import WeekSchedule, Room, CreateTimetableChangeRequest, CreateHolidayRequest, TimetableTemplateRequest

router = APIRouter()


class CurriculumEntry(BaseModel):
    class_id: int
    subject_id: int
    hours_per_week: int


class CurriculumSaveRequest(BaseModel):
    entries: List[CurriculumEntry]


class GenerateRequest(BaseModel):
    academic_period_id: Optional[int] = None


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
                from app.db.timetable_operations import _format_time
                for row in rows:
                    lessons.append({
                        "day_of_week": row['day_of_week'],
                        "lesson_number": row['lesson_number'],
                        "start_time": _format_time(row['start_time']),
                        "end_time": _format_time(row['end_time']),
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


@router.get("/holidays")
async def list_holidays(
    date_from: str = Query(None, description="Начало периода (YYYY-MM-DD)"),
    date_to: str = Query(None, description="Конец периода (YYYY-MM-DD)"),
    current_user=Depends(get_current_user),
):
    """Список праздников и каникул."""
    d_from = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else None
    d_to = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else None
    return await get_holidays(d_from, d_to)


@router.delete("/holidays/{holiday_id}")
async def delete_holiday_endpoint(holiday_id: int, current_user=Depends(get_current_user)):
    """Удалить праздник."""
    ok = await delete_holiday(holiday_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Праздник не найден")
    return {"message": "Праздник удалён"}


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


@router.get("/timetable/curriculum")
async def get_curriculum(
    school_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user)
):
    """Получить учебный план (часы в неделю по классам и предметам) для школы."""
    role = current_user.get("role", "") if isinstance(current_user, dict) else getattr(current_user, "role", "")
    if role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(403, "Forbidden")

    class _U:
        def __init__(self, id_, role_):
            self.id = id_
            self.role = role_
    uid = current_user.get("id") if isinstance(current_user, dict) else getattr(current_user, "id", None)
    sid = await get_school_id_for_user(_U(uid, role))
    if sid is None:
        sid = school_id
    if sid is None:
        raise HTTPException(400, "Укажите school_id")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT c.class_id, c.subject_id, c.hours_per_week, s.name as subject_name,
                       cl.name as class_name, cst.teacher_id,
                       COALESCE(p.full_name, u.username) as teacher_name
                FROM curriculum c
                JOIN classes cl ON cl.id = c.class_id AND cl.school_id = %s
                LEFT JOIN subjects s ON s.id = c.subject_id
                LEFT JOIN class_subject_teachers cst ON cst.class_id = c.class_id AND cst.subject_id = c.subject_id
                LEFT JOIN users u ON u.id = cst.teacher_id
                LEFT JOIN profiles p ON p.user_id = u.id
                ORDER BY cl.name, s.name
            """, (sid,))
            rows = await cursor.fetchall()
            return [
                {
                    "class_id": r["class_id"], "subject_id": r["subject_id"],
                    "hours_per_week": r["hours_per_week"], "subject_name": r.get("subject_name"),
                    "class_name": r.get("class_name"), "teacher_id": r.get("teacher_id"),
                    "teacher_name": r.get("teacher_name"),
                }
                for r in rows
            ]
    finally:
        conn.close()


@router.post("/timetable/curriculum")
async def save_curriculum(
    payload: CurriculumSaveRequest,
    school_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user)
):
    """Сохранить учебный план. Перезаписывает часы для указанных пар (class_id, subject_id)."""
    role = current_user.get("role", "") if isinstance(current_user, dict) else getattr(current_user, "role", "")
    if role not in ("admin", "owner", "superadmin"):
        raise HTTPException(403, "Forbidden")
    from app.models.user import User
    class _U:
        def __init__(self, id_, role_):
            self.id = id_
            self.role = role_
    uid = current_user.get("id") if isinstance(current_user, dict) else getattr(current_user, "id", None)
    sid = await get_school_id_for_user(_U(uid, role))
    if sid is None:
        sid = school_id
    if sid is None:
        raise HTTPException(400, "Укажите school_id")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Полная замена: удаляем старые записи школы, затем вставляем новые
            await cursor.execute("""
                DELETE c FROM curriculum c
                INNER JOIN classes cl ON cl.id = c.class_id AND cl.school_id = %s
            """, (sid,))
            for e in payload.entries:
                await cursor.execute("""
                    INSERT INTO curriculum (class_id, subject_id, hours_per_week, school_id)
                    VALUES (%s, %s, %s, %s)
                """, (e.class_id, e.subject_id, e.hours_per_week, sid))
            await conn.commit()
            return {"message": "Учебный план сохранён", "count": len(payload.entries)}
    finally:
        conn.close()


@router.post("/timetable/generate")
async def generate_timetable_endpoint(
    payload: GenerateRequest,
    school_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user)
):
    """Автогенерация расписания по учебному плану для всех классов школы."""
    role = current_user.get("role", "") if isinstance(current_user, dict) else getattr(current_user, "role", "")
    if role not in ("admin", "owner", "superadmin"):
        raise HTTPException(403, "Forbidden")
    from app.models.user import User
    class _U:
        def __init__(self, id_, role_):
            self.id = id_
            self.role = role_
    uid = current_user.get("id") if isinstance(current_user, dict) else getattr(current_user, "id", None)
    sid = await get_school_id_for_user(_U(uid, role))
    if sid is None:
        sid = school_id
    if sid is None:
        raise HTTPException(400, "Укажите school_id")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            period_id = payload.academic_period_id
            if not period_id:
                await cursor.execute("SELECT id FROM academic_periods WHERE is_active = TRUE LIMIT 1")
                pr = await cursor.fetchone()
                if not pr:
                    await cursor.execute("SELECT id FROM academic_periods ORDER BY id DESC LIMIT 1")
                    pr = await cursor.fetchone()
                period_id = pr["id"] if pr and isinstance(pr, dict) else (pr[0] if pr else None)
            if not period_id:
                raise HTTPException(400, "Нет учебного периода. Создайте период в разделе «Учебные периоды».")

            await cursor.execute("""
                SELECT c.class_id, c.subject_id, c.hours_per_week, cst.teacher_id
                FROM curriculum c
                JOIN classes cl ON cl.id = c.class_id AND cl.school_id = %s AND COALESCE(cl.is_archived, 0) = 0
                LEFT JOIN class_subject_teachers cst ON cst.class_id = c.class_id AND cst.subject_id = c.subject_id
                WHERE c.hours_per_week > 0
            """, (sid,))
            rows = await cursor.fetchall()
            items = []
            for r in rows:
                tid = r.get("teacher_id") if isinstance(r, dict) else r[3]
                if not tid:
                    continue
                items.append(CurriculumItem(
                    class_id=r["class_id"] if isinstance(r, dict) else r[0],
                    subject_id=r["subject_id"] if isinstance(r, dict) else r[1],
                    teacher_id=tid,
                    hours_per_week=r["hours_per_week"] if isinstance(r, dict) else r[2],
                ))

            if not items:
                raise HTTPException(400, "Нет данных в учебном плане с назначенными учителями. Заполните план и назначьте учителей на предметы.")

            lessons = generate_timetable(items)

            await cursor.execute("SELECT id FROM rooms WHERE is_active = TRUE LIMIT 1")
            room_row = await cursor.fetchone()
            default_room = room_row["id"] if room_row and isinstance(room_row, dict) else (room_row[0] if room_row else None)

            for cls in set(l.class_id for l in lessons):
                await cursor.execute(
                    "DELETE FROM timetable_templates WHERE class_id = %s AND academic_period_id = %s",
                    (cls, period_id)
                )

            for L in lessons:
                await cursor.execute("""
                    INSERT INTO timetable_templates
                    (class_id, subject_id, teacher_id, room_id, day_of_week, lesson_number, start_time, end_time, week_type, academic_period_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'BOTH', %s)
                """, (
                    L.class_id, L.subject_id, L.teacher_id,
                    L.room_id or default_room,
                    L.day_of_week, L.lesson_number, L.start_time, L.end_time,
                    period_id
                ))
            await conn.commit()
            return {"message": "Расписание сгенерировано", "lessons_created": len(lessons)}
    finally:
        conn.close()


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
