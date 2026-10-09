from datetime import date, datetime, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.security import get_current_user
from app.core.entitlements import get_school_id_for_user, require_school_feature
from app.db.connection import get_db_connection
from app.db.timetable_operations import get_week_schedule, get_rooms, create_timetable_change, create_holiday, get_holidays, delete_holiday, get_teacher_week_schedule
from app.db.timetable_generator import CurriculumItem, generate_timetable, PlacedLesson
from app.models.timetable import (
    WeekSchedule,
    Room,
    RoomType,
    CreateTimetableChangeRequest,
    CreateHolidayRequest,
    TimetableTemplateRequest,
)

router = APIRouter()

OWNER_ROLES = {"owner", "superadmin", "root"}


def _current_role(current_user) -> str:
    return current_user.get("role", "") if isinstance(current_user, dict) else getattr(current_user, "role", "")


def _current_user_id(current_user):
    return current_user.get("id") if isinstance(current_user, dict) else getattr(current_user, "id", None)


def _row_value(row, key: str, index: int, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    return row[index] if index < len(row) else default


async def _resolve_timetable_school_id(current_user, requested_school_id: Optional[int]) -> int:
    role = _current_role(current_user)
    if role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(403, "Forbidden")

    class _U:
        def __init__(self, id_, role_):
            self.id = id_
            self.role = role_

    uid = _current_user_id(current_user)
    user_school_id = await get_school_id_for_user(_U(uid, role))
    if role == "admin":
        if user_school_id is None:
            raise HTTPException(403, "Forbidden")
        if requested_school_id is not None and int(requested_school_id) != int(user_school_id):
            raise HTTPException(403, "Forbidden")
        return int(user_school_id)

    school_id = requested_school_id if requested_school_id is not None else user_school_id
    if school_id is None:
        raise HTTPException(400, "Укажите school_id")
    return int(school_id)


async def _resolve_read_school_id(
    current_user,
    requested_school_id: Optional[int],
) -> int:
    role = _current_role(current_user)
    if role in ("admin", "owner", "superadmin", "root"):
        return await _resolve_timetable_school_id(
            current_user,
            requested_school_id,
        )
    school_id = await get_school_id_for_user(current_user)
    if school_id is None:
        raise HTTPException(403, "Пользователь не привязан к школе")
    if requested_school_id is not None and int(requested_school_id) != int(school_id):
        raise HTTPException(403, "Нет доступа к этой школе")
    return int(school_id)


async def _ensure_curriculum_classes_in_school(cursor, entries: List["CurriculumEntry"], school_id: int) -> None:
    class_ids = sorted({entry.class_id for entry in entries})
    if not class_ids:
        return

    placeholders = ",".join(["%s"] * len(class_ids))
    await cursor.execute(
        f"""
        SELECT id
        FROM classes
        WHERE school_id = %s AND id IN ({placeholders})
        """,
        tuple([school_id, *class_ids]),
    )
    rows = await cursor.fetchall()
    allowed = {
        int(row["id"] if isinstance(row, dict) else row[0])
        for row in rows
    }
    missing = [class_id for class_id in class_ids if class_id not in allowed]
    if missing:
        raise HTTPException(403, f"Классы не принадлежат выбранной школе: {missing}")


async def _ensure_curriculum_subjects_in_school(
    cursor,
    entries: List["CurriculumEntry"],
    school_id: int,
) -> None:
    subject_ids = sorted({entry.subject_id for entry in entries})
    if not subject_ids:
        return

    placeholders = ",".join(["%s"] * len(subject_ids))
    await cursor.execute(
        f"""
        SELECT subject_id
        FROM school_subjects
        WHERE school_id = %s AND subject_id IN ({placeholders})
        """,
        tuple([school_id, *subject_ids]),
    )
    rows = await cursor.fetchall()
    allowed = {
        int(_row_value(row, "subject_id", 0))
        for row in rows
    }
    missing = [subject_id for subject_id in subject_ids if subject_id not in allowed]
    if missing:
        raise HTTPException(
            403,
            f"Предметы не принадлежат выбранной школе: {missing}",
        )


async def _get_class_school_id(cursor, class_id: int) -> int:
    await cursor.execute(
        "SELECT school_id FROM classes WHERE id = %s LIMIT 1",
        (class_id,),
    )
    row = await cursor.fetchone()
    school_id = _row_value(row, "school_id", 0)
    if school_id is None:
        raise HTTPException(404, "Класс или его школа не найдены")
    return int(school_id)


async def _get_school_period_id(
    cursor,
    school_id: int,
    *,
    create_default: bool = False,
) -> Optional[int]:
    await cursor.execute(
        """
        SELECT id FROM academic_periods
        WHERE school_id = %s AND is_active = TRUE
        ORDER BY end_date DESC
        LIMIT 1
        """,
        (school_id,),
    )
    row = await cursor.fetchone()
    if not row:
        await cursor.execute(
            """
            SELECT id FROM academic_periods
            WHERE school_id = %s
            ORDER BY end_date DESC
            LIMIT 1
            """,
            (school_id,),
        )
        row = await cursor.fetchone()
    if row:
        return int(_row_value(row, "id", 0))
    if not create_default:
        return None

    today = date.today()
    end_date = date(today.year + 1, 6, 30)
    await cursor.execute(
        """
        INSERT INTO academic_periods
            (school_id, name, start_date, end_date, is_active)
        VALUES (%s, %s, %s, %s, TRUE)
        """,
        (school_id, "Учебный год", today, end_date),
    )
    return int(cursor.lastrowid)


async def _ensure_can_read_class_timetable(current_user, class_id: int) -> None:
    role = _current_role(current_user)
    uid = _current_user_id(current_user)
    if uid is None:
        raise HTTPException(403, "Forbidden")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            if role in OWNER_ROLES:
                await cursor.execute(
                    "SELECT 1 FROM classes WHERE id = %s LIMIT 1",
                    (class_id,),
                )
            elif role == "admin":
                school_id = await _resolve_timetable_school_id(current_user, None)
                await cursor.execute(
                    "SELECT 1 FROM classes WHERE id = %s AND school_id = %s LIMIT 1",
                    (class_id, school_id),
                )
            elif role == "teacher":
                await cursor.execute(
                    """
                    SELECT 1
                    FROM class_teachers
                    WHERE class_id = %s AND teacher_id = %s
                    UNION
                    SELECT 1
                    FROM class_subject_teachers
                    WHERE class_id = %s AND teacher_id = %s
                    LIMIT 1
                    """,
                    (class_id, uid, class_id, uid),
                )
            elif role == "student":
                await cursor.execute(
                    "SELECT 1 FROM class_students WHERE class_id = %s AND student_id = %s LIMIT 1",
                    (class_id, uid),
                )
            elif role == "parent":
                await cursor.execute(
                    """
                    SELECT 1
                    FROM parent_students ps
                    JOIN class_students cs ON cs.student_id = ps.student_id
                    WHERE ps.parent_id = %s AND cs.class_id = %s
                    LIMIT 1
                    """,
                    (uid, class_id),
                )
            else:
                raise HTTPException(403, "Forbidden")

            if await cursor.fetchone() is None:
                if role in OWNER_ROLES:
                    raise HTTPException(404, "Класс не найден")
                raise HTTPException(403, "Forbidden")
    finally:
        conn.close()


class CurriculumEntry(BaseModel):
    class_id: int
    subject_id: int
    hours_per_week: int


class CurriculumSaveRequest(BaseModel):
    entries: List[CurriculumEntry]


class GenerateRequest(BaseModel):
    academic_period_id: Optional[int] = None


class RoomCreate(BaseModel):
    name: str
    building: Optional[str] = None
    capacity: Optional[int] = None
    room_type: RoomType = RoomType.CLASSROOM
    class_id: Optional[int] = None


@router.get("/timetable/week", response_model=WeekSchedule)
async def get_timetable_week(
    class_id: int = Query(..., description="ID класса"),
    week_start_date: str = Query(..., description="Дата начала недели (YYYY-MM-DD)"),
    student_id: Optional[int] = Query(None, description="ID ученика для персональных платных занятий"),
    current_user=Depends(get_current_user)
):
    """Получить расписание на неделю для класса"""
    await require_school_feature(current_user, "schedule_enabled", detail="Расписание отключено в настройках школы")
    try:
        week_date = datetime.strptime(week_start_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверный формат даты. Используйте YYYY-MM-DD")
    await _ensure_can_read_class_timetable(current_user, class_id)
    resolved_student_id: Optional[int] = None
    current_role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
    current_id = current_user.get("id") if isinstance(current_user, dict) else getattr(current_user, "id", None)

    if current_role == "student":
        resolved_student_id = current_id
    elif current_role == "parent" and student_id is not None:
        conn = await get_db_connection()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    SELECT 1
                    FROM parent_students
                    WHERE parent_id = %s AND student_id = %s
                    LIMIT 1
                """, (current_id, student_id))
                if not await cursor.fetchone():
                    raise HTTPException(status_code=403, detail="Ребенок не привязан к родителю")
                resolved_student_id = student_id
        finally:
            conn.close()

    return await get_week_schedule(class_id, week_date, resolved_student_id)


@router.get("/timetable/current-week", response_model=WeekSchedule)
async def get_current_week_timetable(
    class_id: int = Query(..., description="ID класса"),
    current_user=Depends(get_current_user)
):
    """Получить расписание на текущую неделю"""
    await require_school_feature(current_user, "schedule_enabled", detail="Расписание отключено в настройках школы")
    await _ensure_can_read_class_timetable(current_user, class_id)
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
    await require_school_feature(current_user, "schedule_enabled", detail="Расписание отключено в настройках школы")
    await _ensure_can_read_class_timetable(current_user, class_id)
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
    await require_school_feature(current_user, "schedule_enabled", detail="Расписание отключено в настройках школы")
    await _ensure_can_read_class_timetable(current_user, class_id)
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
    await require_school_feature(current_user, "schedule_enabled", detail="Расписание отключено в настройках школы")
    try:
        week_date = datetime.strptime(week_start_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверный формат даты. Используйте YYYY-MM-DD")
    teacher_id = current_user.id if hasattr(current_user, 'id') else current_user["id"]
    return await get_teacher_week_schedule(teacher_id, week_date)


@router.get("/rooms", response_model=List[Room])
async def get_rooms_list(
    school_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user),
):
    """Получить список кабинетов школы."""
    sid = await _resolve_read_school_id(current_user, school_id)
    await require_school_feature(
        current_user,
        "schedule_enabled",
        school_id=sid,
        detail="Расписание отключено в настройках школы",
    )
    return await get_rooms(sid)


@router.post("/rooms", response_model=Room, status_code=201)
async def create_room_endpoint(
    payload: RoomCreate,
    school_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user),
):
    """Создать кабинет или подключить существующий кабинет к школе."""
    sid = await _resolve_timetable_school_id(current_user, school_id)
    await require_school_feature(
        current_user,
        "schedule_enabled",
        school_id=sid,
        detail="Расписание отключено в настройках школы",
    )
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Название кабинета обязательно")
    if payload.capacity is not None and payload.capacity <= 0:
        raise HTTPException(400, "Вместимость должна быть больше нуля")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id FROM rooms
                WHERE name = %s AND COALESCE(building, '') = %s
                LIMIT 1
                """,
                (name, (payload.building or "").strip()),
            )
            row = await cursor.fetchone()
            if row:
                room_id = int(_row_value(row, "id", 0))
            else:
                await cursor.execute(
                    """
                    INSERT INTO rooms
                        (name, building, capacity, room_type, is_active)
                    VALUES (%s, %s, %s, %s, TRUE)
                    """,
                    (
                        name,
                        (payload.building or "").strip() or None,
                        payload.capacity,
                        payload.room_type.value,
                    ),
                )
                room_id = int(cursor.lastrowid)
            try:
                await cursor.execute(
                    """
                    INSERT INTO school_rooms (school_id, room_id)
                    VALUES (%s, %s)
                    """,
                    (sid, room_id),
                )
            except Exception as exc:
                if getattr(exc, "args", [None])[0] == 1062:
                    raise HTTPException(400, "Кабинет уже добавлен в школу")
                raise
            await conn.commit()
            await cursor.execute(
                """
                SELECT id, name, building, capacity, room_type, is_active
                FROM rooms WHERE id = %s
                """,
                (room_id,),
            )
            room = await cursor.fetchone()
            return Room(
                id=_row_value(room, "id", 0),
                name=_row_value(room, "name", 1),
                building=_row_value(room, "building", 2),
                capacity=_row_value(room, "capacity", 3),
                room_type=_row_value(room, "room_type", 4),
                is_active=bool(_row_value(room, "is_active", 5, True)),
            )
    except HTTPException:
        await conn.rollback()
        raise
    except Exception:
        await conn.rollback()
        raise
    finally:
        conn.close()


@router.get("/timetable/template")
async def get_timetable_template(
    class_id: int = Query(..., description="ID класса"),
    current_user=Depends(get_current_user)
):
    """Получить шаблон расписания для класса"""
    await require_school_feature(current_user, "schedule_enabled", detail="Расписание отключено в настройках школы")
    await _ensure_can_read_class_timetable(current_user, class_id)
    import logging
    logger = logging.getLogger(__name__)
    try:
        conn = await get_db_connection()
        try:
            async with conn.cursor() as cursor:
                school_id = await _get_class_school_id(cursor, class_id)
                period_id = await _get_school_period_id(cursor, school_id)
                
                # Получаем шаблон расписания для класса
                if period_id:
                    await cursor.execute("""
                        SELECT 
                            t.day_of_week, t.lesson_number, t.start_time, t.end_time,
                            s.name AS subject_name,
                            TRIM(BOTH '"' FROM p.full_name) AS teacher_name,
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
                            TRIM(BOTH '"' FROM p.full_name) AS teacher_name,
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
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting timetable template: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при получении шаблона: {str(e)}")


@router.post("/timetable/template")
async def save_timetable_template(
    template_data: TimetableTemplateRequest,
    current_user=Depends(get_current_user)
):
    """Сохранить шаблон расписания для класса"""
    await require_school_feature(current_user, "schedule_enabled", detail="Расписание отключено в настройках школы")
    role = _current_role(current_user)
    if role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(403, "Только администратор может менять расписание")
    await _ensure_can_read_class_timetable(current_user, template_data.class_id)
    import logging
    logger = logging.getLogger(__name__)
    try:
        logger.info(f"Saving timetable template for class_id={template_data.class_id}, lessons={len(template_data.lessons)}")
        conn = await get_db_connection()
        
        try:
            async with conn.cursor() as cursor:
                school_id = await _get_class_school_id(
                    cursor,
                    template_data.class_id,
                )
                period_id = await _get_school_period_id(
                    cursor,
                    school_id,
                    create_default=True,
                )
                
                # Сначала удаляем существующий шаблон для этого класса
                await cursor.execute("""
                    DELETE FROM timetable_templates 
                    WHERE class_id = %s AND academic_period_id = %s
                """, (template_data.class_id, period_id))
                
                # Добавляем новые уроки
                for lesson in template_data.lessons:
                    # Находим ID предмета по имени
                    await cursor.execute(
                        """
                        SELECT s.id
                        FROM school_subjects ss
                        JOIN subjects s ON s.id = ss.subject_id
                        WHERE ss.school_id = %s AND s.name = %s
                        LIMIT 1
                        """,
                        (school_id, lesson.subject),
                    )
                    subject_row = await cursor.fetchone()
                    if not subject_row:
                        raise HTTPException(status_code=400, detail=f"Предмет '{lesson.subject}' не найден")
                    subject_id = subject_row[0] if isinstance(subject_row, (list, tuple)) else subject_row.get("id")
                    
                    # Находим ID учителя по имени (из профиля)
                    await cursor.execute("""
                        SELECT u.id FROM users u
                        JOIN profiles p ON p.user_id = u.id
                        JOIN school_users su
                          ON su.user_id = u.id AND su.school_id = %s
                        WHERE p.full_name = %s AND u.role = 'teacher'
                        LIMIT 1
                    """, (school_id, lesson.teacher))
                    teacher_row = await cursor.fetchone()
                    if not teacher_row:
                        raise HTTPException(status_code=400, detail=f"Учитель '{lesson.teacher}' не найден")
                    teacher_id = teacher_row[0] if isinstance(teacher_row, (list, tuple)) else teacher_row.get("id")
                    
                    # Находим ID кабинета по имени (если указан)
                    room_id = None
                    if lesson.room and lesson.room.strip():
                        await cursor.execute(
                            """
                            SELECT r.id
                            FROM school_rooms sr
                            JOIN rooms r ON r.id = sr.room_id
                            WHERE sr.school_id = %s AND r.name = %s
                              AND r.is_active = TRUE
                            LIMIT 1
                            """,
                            (school_id, lesson.room),
                        )
                        room_row = await cursor.fetchone()
                        if not room_row:
                            raise HTTPException(
                                400,
                                f"Кабинет '{lesson.room}' не найден в школе",
                            )
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
    await require_school_feature(current_user, "schedule_enabled", detail="Расписание отключено в настройках школы")
    if _current_role(current_user) not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(403, "Только администратор может создавать замены")
    await _ensure_can_read_class_timetable(current_user, change_data.class_id)
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
    school_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user),
):
    """Список праздников и каникул."""
    await require_school_feature(current_user, "schedule_enabled", detail="Расписание отключено в настройках школы")
    sid = await _resolve_read_school_id(current_user, school_id)
    try:
        d_from = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else None
        d_to = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else None
    except ValueError:
        raise HTTPException(400, "Неверный формат даты. Используйте YYYY-MM-DD")
    if d_from and d_to and d_from > d_to:
        raise HTTPException(400, "Дата начала не может быть позже даты окончания")
    return await get_holidays(sid, d_from, d_to)


@router.delete("/holidays/{holiday_id}")
async def delete_holiday_endpoint(
    holiday_id: int,
    school_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user),
):
    """Удалить праздник."""
    await require_school_feature(current_user, "schedule_enabled", detail="Расписание отключено в настройках школы")
    if _current_role(current_user) not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(403, "Только администратор может удалять праздники")
    sid = await _resolve_timetable_school_id(current_user, school_id)
    ok = await delete_holiday(holiday_id, sid)
    if not ok:
        raise HTTPException(status_code=404, detail="Праздник не найден")
    return {"message": "Праздник удалён"}


@router.post("/holidays")
async def create_holiday_endpoint(
    holiday_data: CreateHolidayRequest,
    school_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user)
):
    """Создать праздник/выходной"""
    await require_school_feature(current_user, "schedule_enabled", detail="Расписание отключено в настройках школы")
    if _current_role(current_user) not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(403, "Только администратор может создавать праздники")
    sid = await _resolve_timetable_school_id(current_user, school_id)
    success = await create_holiday(
        school_id=sid,
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
    await require_school_feature(current_user, "schedule_enabled", detail="Расписание отключено в настройках школы")
    role = current_user.get("role", "") if isinstance(current_user, dict) else getattr(current_user, "role", "")
    if role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(403, "Forbidden")

    sid = await _resolve_timetable_school_id(current_user, school_id)

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
                    "class_id": _row_value(r, "class_id", 0),
                    "subject_id": _row_value(r, "subject_id", 1),
                    "hours_per_week": _row_value(r, "hours_per_week", 2),
                    "subject_name": _row_value(r, "subject_name", 3),
                    "class_name": _row_value(r, "class_name", 4),
                    "teacher_id": _row_value(r, "teacher_id", 5),
                    "teacher_name": _row_value(r, "teacher_name", 6),
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
    await require_school_feature(current_user, "schedule_enabled", detail="Расписание отключено в настройках школы")
    role = current_user.get("role", "") if isinstance(current_user, dict) else getattr(current_user, "role", "")
    if role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(403, "Forbidden")
    sid = await _resolve_timetable_school_id(current_user, school_id)

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await _ensure_curriculum_classes_in_school(cursor, payload.entries, sid)
            await _ensure_curriculum_subjects_in_school(cursor, payload.entries, sid)
            # Полная замена: удаляем старые записи школы, затем вставляем новые
            await cursor.execute("""
                DELETE c FROM curriculum c
                INNER JOIN classes cl ON cl.id = c.class_id AND cl.school_id = %s
            """, (sid,))
            await cursor.execute("""
                DELETE cs FROM class_subjects cs
                INNER JOIN classes cl ON cl.id = cs.class_id AND cl.school_id = %s
            """, (sid,))
            for e in payload.entries:
                await cursor.execute("""
                    INSERT INTO curriculum (class_id, subject_id, hours_per_week, school_id)
                    VALUES (%s, %s, %s, %s)
                """, (e.class_id, e.subject_id, e.hours_per_week, sid))
                await cursor.execute("""
                    INSERT IGNORE INTO class_subjects (class_id, subject_id)
                    VALUES (%s, %s)
                """, (e.class_id, e.subject_id))
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
    """Автогенерация расписания по учебному плану для школы или выбранного класса."""
    await require_school_feature(current_user, "schedule_enabled", detail="Расписание отключено в настройках школы")
    role = current_user.get("role", "") if isinstance(current_user, dict) else getattr(current_user, "role", "")
    if role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(403, "Forbidden")
    sid = await _resolve_timetable_school_id(current_user, school_id)

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            period_id = payload.academic_period_id
            if period_id:
                await cursor.execute(
                    """
                    SELECT id FROM academic_periods
                    WHERE id = %s AND school_id = %s
                    """,
                    (period_id, sid),
                )
                if not await cursor.fetchone():
                    raise HTTPException(
                        400,
                        "Учебный период не относится к выбранной школе",
                    )
            else:
                period_id = await _get_school_period_id(cursor, sid)
            if not period_id:
                raise HTTPException(400, "Нет учебного периода. Создайте период в разделе «Учебные периоды».")

            class_filter = "AND c.class_id = %s" if payload.class_id else ""
            params = [sid]
            if payload.class_id:
                params.append(payload.class_id)
            await cursor.execute(f"""
                SELECT c.class_id, c.subject_id, c.hours_per_week, cst.teacher_id
                FROM curriculum c
                JOIN classes cl ON cl.id = c.class_id AND cl.school_id = %s AND COALESCE(cl.is_archived, 0) = 0
                LEFT JOIN class_subject_teachers cst ON cst.class_id = c.class_id AND cst.subject_id = c.subject_id
                WHERE c.hours_per_week > 0
                {class_filter}
            """, tuple(params))
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

            await cursor.execute(
                """
                SELECT r.id
                FROM school_rooms sr
                JOIN rooms r ON r.id = sr.room_id
                WHERE sr.school_id = %s AND r.is_active = TRUE
                ORDER BY r.id
                LIMIT 1
                """,
                (sid,),
            )
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
    await require_school_feature(current_user, "schedule_enabled", detail="Расписание отключено в настройках школы")
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
