from datetime import date, datetime, timedelta, time
from typing import List, Optional
import aiomysql

from app.db.connection import get_db_connection


def _format_time(val) -> str:
    """Конвертирует TIME из MySQL (time, timedelta или str) в "HH:MM"."""
    if val is None:
        return "00:00"
    if isinstance(val, timedelta):
        total_sec = int(val.total_seconds())
        h = total_sec // 3600
        m = (total_sec % 3600) // 60
        return f"{h:02d}:{m:02d}"
    if isinstance(val, time):
        return val.strftime("%H:%M")
    s = str(val).strip()
    if not s or s.isdigit():
        return "00:00"
    parts = s.split(":")
    if len(parts) >= 2:
        try:
            h, m = int(parts[0]), int(parts[1])
            return f"{h:02d}:{m:02d}"
        except (ValueError, IndexError):
            pass
    return s
from app.models.timetable import (
    DaySchedule, LessonItem, WeekSchedule, TimetableTemplate, 
    TimetableChange, Holiday, AcademicWeek, Room
)


async def get_week_schedule(
    class_id: int,
    week_start_date: date,
    student_id: Optional[int] = None,
) -> WeekSchedule:
    """Получить расписание на неделю для класса"""
    conn = await get_db_connection()
    try:
        week_end_date = week_start_date + timedelta(days=6)
        
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT
                    c.school_id,
                    COALESCE(
                        (
                            SELECT ap.id
                            FROM academic_periods ap
                            WHERE ap.school_id = c.school_id
                              AND ap.start_date <= %s
                              AND ap.end_date >= %s
                            ORDER BY ap.is_active DESC, ap.end_date DESC
                            LIMIT 1
                        ),
                        (
                            SELECT ap.id
                            FROM academic_periods ap
                            WHERE ap.school_id = c.school_id
                            ORDER BY ap.end_date DESC
                            LIMIT 1
                        )
                    ) AS academic_period_id
                FROM classes c
                WHERE c.id = %s
                LIMIT 1
                """,
                (week_end_date, week_start_date, class_id),
            )
            class_context = await cursor.fetchone()
            if not class_context:
                raise ValueError("Class not found")
            school_id = class_context["school_id"]
            academic_period_id = class_context.get("academic_period_id")

            # Получаем информацию о неделе
            await cursor.execute(
                """
                SELECT week_number, week_type, academic_period_id
                FROM academic_weeks
                WHERE week_start_date = %s AND academic_period_id = %s
                """,
                (week_start_date, academic_period_id),
            )
            week_info = await cursor.fetchone()
            
            if not week_info:
                # Если неделя не найдена, создаем базовую информацию
                week_number = 1
                week_type = 'A'
                academic_period_id = academic_period_id or 0
            else:
                week_number = week_info['week_number']
                week_type = week_info['week_type']
                academic_period_id = week_info['academic_period_id']
            
            # Получаем праздники на эту неделю
            await cursor.execute("""
                SELECT date, name, description
                FROM holidays 
                WHERE school_id = %s
                  AND date BETWEEN %s AND %s
                  AND affects_classes = TRUE
            """, (school_id, week_start_date, week_end_date))
            holidays = {row['date']: row for row in await cursor.fetchall()}
            
            # Получаем замены на эту неделю
            await cursor.execute("""
                SELECT date, lesson_number, change_type, reason,
                       new_teacher_id, new_subject_id, new_room_id,
                       original_teacher_id, original_subject_id, original_room_id
                FROM timetable_changes 
                WHERE date BETWEEN %s AND %s AND class_id = %s
            """, (week_start_date, week_end_date, class_id))
            changes = {}
            for row in await cursor.fetchall():
                key = (row['date'], row['lesson_number'])
                changes[key] = row
            
            # Получаем основное расписание (джойним названия из справочников)
            await cursor.execute("""
                SELECT 
                    t.day_of_week, t.lesson_number, t.start_time, t.end_time,
                    t.teacher_id, t.subject_id,
                    s.name AS subject_name,
                    TRIM(BOTH '"' FROM p.full_name) AS teacher_name,
                    r.name AS room_name
                FROM timetable_templates t
                LEFT JOIN subjects s ON s.id = t.subject_id
                LEFT JOIN profiles p ON p.user_id = t.teacher_id
                LEFT JOIN rooms r ON r.id = t.room_id
                WHERE t.class_id = %s
                  AND (%s = 0 OR t.academic_period_id = %s)
                ORDER BY t.day_of_week, t.lesson_number
            """, (class_id, academic_period_id, academic_period_id))
            templates = await cursor.fetchall()
            
            paid_sessions_by_date = {}
            if student_id is not None:
                await cursor.execute("""
                    SELECT
                        s.id,
                        s.lesson_id,
                        s.session_date,
                        s.start_time,
                        s.end_time,
                        s.status,
                        s.lessons_reserved,
                        e.remaining_lessons,
                        l.title AS lesson_title,
                        l.subject AS lesson_subject,
                        TRIM(BOTH '"' FROM p.full_name) AS tutor_name,
                        COALESCE(s.location, l.location, '') AS location_name,
                        COALESCE(s.is_online, l.is_online, FALSE) AS is_online
                    FROM lesson_student_sessions s
                    JOIN additional_lessons l ON l.id = s.lesson_id
                    LEFT JOIN lesson_enrollments e ON e.id = s.enrollment_id
                    LEFT JOIN profiles p ON p.user_id = s.tutor_id
                    WHERE s.student_id = %s
                      AND s.session_date BETWEEN %s AND %s
                      AND s.status <> 'cancelled'
                    ORDER BY s.session_date, s.start_time
                """, (student_id, week_start_date, week_end_date))
                for row in await cursor.fetchall():
                    paid_sessions_by_date.setdefault(row['session_date'], []).append(row)

            # Строим расписание по дням
            days = []
            day_names = ['', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
            
            for day_offset in range(7):
                current_date = week_start_date + timedelta(days=day_offset)
                day_of_week = current_date.weekday() + 1  # 1=Понедельник
                
                # Проверяем, является ли день праздником
                holiday_info = holidays.get(current_date)
                is_holiday = holiday_info is not None
                
                if is_holiday:
                    days.append(DaySchedule(
                        date=current_date.strftime('%Y-%m-%d'),
                        day_of_week=day_names[day_of_week],
                        is_holiday=True,
                        holiday_name=holiday_info['name'],
                        holiday_reason=holiday_info['description'],
                        lessons=[]
                    ))
                    continue
                
                # Получаем уроки для этого дня
                day_lessons = []
                day_templates = [t for t in templates if t['day_of_week'] == day_of_week]
                
                for template in day_templates:
                    lesson_number = template['lesson_number']
                    change_key = (current_date, lesson_number)
                    change = changes.get(change_key)
                    
                    if change and change['change_type'] == 'cancel':
                        # Урок отменен
                        continue
                    
                    # Определяем данные урока (с учетом замен)
                    # Пока применяем только базовый шаблон; обработка замен возможна позже
                    subject_name = template.get('subject_name') or 'Предмет'
                    teacher_name = template.get('teacher_name') or 'Преподаватель'
                    room_name = template.get('room_name') or 'Не указано'
                    change_type = change['change_type'] if change else None
                    change_reason = change['reason'] if change else None
                    
                    day_lessons.append(LessonItem(
                        lesson_number=lesson_number,
                        start_time=_format_time(template['start_time']),
                        end_time=_format_time(template['end_time']),
                        subject=subject_name,
                        teacher=teacher_name,
                        room=room_name or 'Не указано',
                        change_type=change_type,
                        change_reason=change_reason
                        ,
                        class_id=class_id,
                        subject_id=template.get('subject_id'),
                        teacher_id=template.get('teacher_id'),
                    ))

                for session in paid_sessions_by_date.get(current_date, []):
                    room_label = 'Онлайн' if session.get('is_online') else (
                        session.get('location_name') or 'Платное занятие'
                    )
                    day_lessons.append(LessonItem(
                        lesson_number=None,
                        start_time=_format_time(session['start_time']),
                        end_time=_format_time(session['end_time']),
                        subject=session.get('lesson_title') or 'Платное занятие',
                        teacher=session.get('tutor_name') or 'Преподаватель',
                        room=room_label,
                        teacher_id=None,
                        is_paid_lesson=True,
                        paid_lesson_id=session.get('lesson_id'),
                        paid_lesson_session_id=session.get('id'),
                        paid_lesson_status=session.get('status'),
                        remaining_lessons=session.get('remaining_lessons'),
                    ))

                day_lessons.sort(
                    key=lambda item: (
                        item.start_time or '99:99',
                        item.end_time or '99:99',
                        item.lesson_number if item.lesson_number is not None else 999,
                    )
                )
                
                days.append(DaySchedule(
                    date=current_date.strftime('%Y-%m-%d'),
                    day_of_week=day_names[day_of_week],
                    is_holiday=is_holiday,
                    lessons=day_lessons
                ))
            
            return WeekSchedule(
                week_start_date=week_start_date.strftime('%Y-%m-%d'),
                week_end_date=week_end_date.strftime('%Y-%m-%d'),
                week_number=week_number,
                week_type=week_type,
                days=days
            )
    finally:
        conn.close()


async def _get_subject_name(subject_id: int) -> str:
    """Получить название предмета по ID"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT name FROM subjects WHERE id = %s", (subject_id,))
            result = await cursor.fetchone()
            return result['name'] if result else 'Неизвестно'
    finally:
        conn.close()


async def _get_teacher_name(teacher_id: int) -> str:
    """Получить имя учителя по ID"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT p.full_name 
                FROM users u 
                JOIN profiles p ON u.id = p.user_id 
                WHERE u.id = %s
            """, (teacher_id,))
            result = await cursor.fetchone()
            return result['full_name'] if result else 'Неизвестно'
    finally:
        conn.close()


async def _get_room_name(room_id: int) -> str:
    """Получить название комнаты по ID"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT name FROM rooms WHERE id = %s", (room_id,))
            result = await cursor.fetchone()
            return result['name'] if result else 'Не указано'
    finally:
        conn.close()


async def get_rooms(school_id: int) -> List[Room]:
    """Получить список активных кабинетов школы."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT r.id, r.name, r.building, r.capacity,
                       r.room_type, r.is_active
                FROM school_rooms sr
                JOIN rooms r ON r.id = sr.room_id
                WHERE sr.school_id = %s AND r.is_active = TRUE
                ORDER BY r.name
            """, (school_id,))
            rows = await cursor.fetchall()
            return [Room(**row) for row in rows]
    finally:
        conn.close()


async def get_teacher_week_schedule(teacher_id: int, week_start_date: date) -> WeekSchedule:
    """Получить расписание на неделю для учителя (с классами на карточке)"""
    conn = await get_db_connection()
    try:
        week_end_date = week_start_date + timedelta(days=6)

        async with conn.cursor() as cursor:
            # Информация о неделе
            await cursor.execute(
                """
                SELECT week_number, week_type, academic_period_id
                FROM academic_weeks
                WHERE week_start_date = %s
                """,
                (week_start_date,)
            )
            week_info = await cursor.fetchone()
            if not week_info:
                week_number, week_type, academic_period_id = 1, 'A', 1
            else:
                week_number = week_info['week_number']
                week_type = week_info['week_type']
                academic_period_id = week_info['academic_period_id']

            # Шаблоны уроков для конкретного учителя с join названий и class_id, subject_id
            await cursor.execute(
                """
                SELECT 
                    t.day_of_week, t.lesson_number, t.start_time, t.end_time,
                    t.class_id, t.subject_id,
                    c.name AS class_name,
                    s.name AS subject_name,
                    r.name AS room_name
                FROM timetable_templates t
                JOIN classes c ON c.id = t.class_id
                LEFT JOIN subjects s ON s.id = t.subject_id
                LEFT JOIN rooms r ON r.id = t.room_id
                WHERE t.teacher_id = %s
                  AND t.academic_period_id = COALESCE(
                      (
                          SELECT ap.id
                          FROM academic_periods ap
                          WHERE ap.school_id = c.school_id
                            AND ap.start_date <= %s
                            AND ap.end_date >= %s
                          ORDER BY ap.is_active DESC, ap.end_date DESC
                          LIMIT 1
                      ),
                      (
                          SELECT ap.id
                          FROM academic_periods ap
                          WHERE ap.school_id = c.school_id
                          ORDER BY ap.end_date DESC
                          LIMIT 1
                      )
                  )
                ORDER BY t.day_of_week, t.lesson_number
                """,
                (teacher_id, week_end_date, week_start_date)
            )
            templates = await cursor.fetchall()

            # Замены для всех классов, где учитель участвует, за неделю
            await cursor.execute(
                """
                    SELECT class_id, date, lesson_number, change_type, reason
                FROM timetable_changes
                WHERE date BETWEEN %s AND %s AND (new_teacher_id = %s OR original_teacher_id = %s)
                """,
                (week_start_date, week_end_date, teacher_id, teacher_id)
            )
            changes = {
                (row['class_id'], row['date'], row['lesson_number']): row
                for row in await cursor.fetchall()
            }

            await cursor.execute(
                """
                SELECT h.date, h.name, h.description
                FROM holidays h
                JOIN school_users su
                  ON su.school_id = h.school_id AND su.user_id = %s
                WHERE h.date BETWEEN %s AND %s
                  AND h.affects_classes = TRUE
                """,
                (teacher_id, week_start_date, week_end_date),
            )
            holidays = {
                row["date"]: row for row in await cursor.fetchall()
            }

            await cursor.execute(
                """
                SELECT
                    s.id,
                    s.lesson_id,
                    s.student_id,
                    s.session_date,
                    s.start_time,
                    s.end_time,
                    s.status,
                    s.lessons_reserved,
                    e.remaining_lessons,
                    l.title AS lesson_title,
                    TRIM(BOTH '"' FROM st.full_name) AS student_name,
                    COALESCE(s.location, l.location, '') AS location_name,
                    COALESCE(s.is_online, l.is_online, FALSE) AS is_online
                FROM lesson_student_sessions s
                JOIN additional_lessons l ON l.id = s.lesson_id
                LEFT JOIN lesson_enrollments e ON e.id = s.enrollment_id
                LEFT JOIN profiles st ON st.user_id = s.student_id
                WHERE s.tutor_id = %s
                  AND s.session_date BETWEEN %s AND %s
                  AND s.status <> 'cancelled'
                ORDER BY s.session_date, s.start_time
                """,
                (teacher_id, week_start_date, week_end_date)
            )
            paid_sessions_by_date = {}
            for row in await cursor.fetchall():
                paid_sessions_by_date.setdefault(row['session_date'], []).append(row)

            days = []
            day_names = ['', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
            for day_offset in range(7):
                current_date = week_start_date + timedelta(days=day_offset)
                day_of_week = current_date.weekday() + 1
                holiday_info = holidays.get(current_date)
                if holiday_info:
                    days.append(DaySchedule(
                        date=current_date.strftime('%Y-%m-%d'),
                        day_of_week=day_names[day_of_week],
                        is_holiday=True,
                        holiday_name=holiday_info['name'],
                        holiday_reason=holiday_info.get('description'),
                        lessons=[],
                    ))
                    continue
                day_templates = [t for t in templates if t['day_of_week'] == day_of_week]

                day_lessons = []
                for t in day_templates:
                    change = changes.get(
                        (t['class_id'], current_date, t['lesson_number'])
                    )
                    change_type = change['change_type'] if change else None
                    change_reason = change['reason'] if change else None
                    # Явно берём class_id и subject_id (для экрана действий по уроку у учителя)
                    raw_class_id = t.get('class_id') or t.get('CLASS_ID')
                    raw_subject_id = t.get('subject_id') or t.get('SUBJECT_ID')
                    class_id = int(raw_class_id) if raw_class_id is not None else None
                    subject_id = int(raw_subject_id) if raw_subject_id is not None else None
                    # В subject кладем НАЗВАНИЕ КЛАССА, а в teacher — НАЗВАНИЕ ПРЕДМЕТА
                    day_lessons.append(LessonItem(
                        lesson_number=t['lesson_number'],
                        start_time=_format_time(t['start_time']),
                        end_time=_format_time(t['end_time']),
                        subject=t.get('class_name') or 'Класс',
                        teacher=t.get('subject_name') or 'Предмет',
                        room=t.get('room_name') or 'Не указано',
                        change_type=change_type,
                        change_reason=change_reason,
                        class_id=class_id,
                        subject_id=subject_id,
                        teacher_id=teacher_id,
                    ))

                for session in paid_sessions_by_date.get(current_date, []):
                    room_label = 'Онлайн' if session.get('is_online') else (
                        session.get('location_name') or 'Платное занятие'
                    )
                    day_lessons.append(LessonItem(
                        lesson_number=None,
                        start_time=_format_time(session['start_time']),
                        end_time=_format_time(session['end_time']),
                        subject=session.get('student_name') or 'Ученик',
                        teacher=session.get('lesson_title') or 'Платное занятие',
                        room=room_label,
                        teacher_id=teacher_id,
                        is_paid_lesson=True,
                        paid_lesson_id=session.get('lesson_id'),
                        paid_lesson_session_id=session.get('id'),
                        paid_lesson_status=session.get('status'),
                        remaining_lessons=session.get('remaining_lessons'),
                    ))

                day_lessons.sort(
                    key=lambda item: (
                        item.start_time or '99:99',
                        item.end_time or '99:99',
                        item.lesson_number if item.lesson_number is not None else 999,
                    )
                )

                days.append(DaySchedule(
                    date=current_date.strftime('%Y-%m-%d'),
                    day_of_week=day_names[day_of_week],
                    is_holiday=False,
                    lessons=day_lessons
                ))

            return WeekSchedule(
                week_start_date=week_start_date.strftime('%Y-%m-%d'),
                week_end_date=week_end_date.strftime('%Y-%m-%d'),
                week_number=week_number,
                week_type=week_type,
                days=days
            )
    finally:
        conn.close()


async def create_timetable_change(
    date: date,
    class_id: int,
    lesson_number: int,
    change_type: str,
    created_by: int,
    **kwargs
) -> bool:
    """Создать замену в расписании"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                INSERT INTO timetable_changes 
                (date, class_id, lesson_number, change_type, created_by,
                 original_teacher_id, new_teacher_id, original_subject_id, new_subject_id,
                 original_room_id, new_room_id, reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                date, class_id, lesson_number, change_type, created_by,
                kwargs.get('original_teacher_id'),
                kwargs.get('new_teacher_id'),
                kwargs.get('original_subject_id'),
                kwargs.get('new_subject_id'),
                kwargs.get('original_room_id'),
                kwargs.get('new_room_id'),
                kwargs.get('reason')
            ))
            await conn.commit()
            return True
    except Exception as e:
        print(f"Error creating timetable change: {e}")
        return False
    finally:
        conn.close()


async def create_holiday(
    school_id: int,
    date: date,
    name: str,
    holiday_type: str = 'holiday',
    affects_classes: bool = True,
    description: Optional[str] = None
) -> bool:
    """Создать праздник"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                INSERT INTO holidays
                    (school_id, date, name, type, affects_classes, description)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                school_id,
                date,
                name,
                holiday_type,
                affects_classes,
                description,
            ))
            await conn.commit()
            return True
    except Exception as e:
        print(f"Error creating holiday: {e}")
        return False
    finally:
        conn.close()


async def get_holidays(
    school_id: int,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> List[dict]:
    """Список праздников (опционально по диапазону дат)."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            if date_from and date_to:
                await cursor.execute(
                    """
                    SELECT id, date, name, type, affects_classes, description
                    FROM holidays
                    WHERE school_id = %s AND date BETWEEN %s AND %s
                    ORDER BY date
                    """,
                    (school_id, date_from, date_to),
                )
            else:
                await cursor.execute(
                    """
                    SELECT id, date, name, type, affects_classes, description
                    FROM holidays
                    WHERE school_id = %s
                    ORDER BY date DESC LIMIT 200
                    """,
                    (school_id,),
                )
            rows = await cursor.fetchall()
            return [
                {
                    "id": r["id"],
                    "date": str(r["date"]),
                    "name": r["name"],
                    "type": r["type"],
                    "affects_classes": bool(r.get("affects_classes", True)),
                    "description": r.get("description"),
                }
                for r in rows
            ]
    finally:
        conn.close()


async def delete_holiday(holiday_id: int, school_id: int) -> bool:
    """Удалить праздник по ID."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM holidays WHERE id = %s AND school_id = %s",
                (holiday_id, school_id),
            )
            await conn.commit()
            return cursor.rowcount > 0
    finally:
        conn.close()
