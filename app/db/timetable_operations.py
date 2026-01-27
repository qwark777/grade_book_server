from datetime import date, datetime, timedelta
from typing import List, Optional
import aiomysql

from app.db.connection import get_db_connection
from app.models.timetable import (
    DaySchedule, LessonItem, WeekSchedule, TimetableTemplate, 
    TimetableChange, Holiday, AcademicWeek, Room
)


async def get_week_schedule(class_id: int, week_start_date: date) -> WeekSchedule:
    """Получить расписание на неделю для класса"""
    conn = await get_db_connection()
    try:
        week_end_date = week_start_date + timedelta(days=6)
        
        async with conn.cursor() as cursor:
            # Получаем информацию о неделе
            await cursor.execute("""
                SELECT week_number, week_type, academic_period_id
                FROM academic_weeks 
                WHERE week_start_date = %s
            """, (week_start_date,))
            week_info = await cursor.fetchone()
            
            if not week_info:
                # Если неделя не найдена, создаем базовую информацию
                week_number = 1
                week_type = 'A'
                academic_period_id = 1
            else:
                week_number = week_info['week_number']
                week_type = week_info['week_type']
                academic_period_id = week_info['academic_period_id']
            
            # Получаем праздники на эту неделю
            await cursor.execute("""
                SELECT date, name, description
                FROM holidays 
                WHERE date BETWEEN %s AND %s AND affects_classes = TRUE
            """, (week_start_date, week_end_date))
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
            templates = await cursor.fetchall()
            
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
                        start_time=str(template['start_time']),
                        end_time=str(template['end_time']),
                        subject=subject_name,
                        teacher=teacher_name,
                        room=room_name or 'Не указано',
                        change_type=change_type,
                        change_reason=change_reason
                    ))
                
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


async def get_rooms() -> List[Room]:
    """Получить список всех комнат"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT id, name, building, capacity, room_type, is_active
                FROM rooms 
                WHERE is_active = TRUE
                ORDER BY name
            """)
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

            # Шаблоны уроков для конкретного учителя с join названий
            await cursor.execute(
                """
                SELECT 
                    t.day_of_week, t.lesson_number, t.start_time, t.end_time,
                    c.name AS class_name,
                    s.name AS subject_name,
                    r.name AS room_name
                FROM timetable_templates t
                JOIN classes c ON c.id = t.class_id
                LEFT JOIN subjects s ON s.id = t.subject_id
                LEFT JOIN rooms r ON r.id = t.room_id
                WHERE t.teacher_id = %s
                ORDER BY t.day_of_week, t.lesson_number
                """,
                (teacher_id,)
            )
            templates = await cursor.fetchall()

            # Замены для всех классов, где учитель участвует, за неделю
            await cursor.execute(
                """
                SELECT date, lesson_number, change_type, reason
                FROM timetable_changes
                WHERE date BETWEEN %s AND %s AND (new_teacher_id = %s OR original_teacher_id = %s)
                """,
                (week_start_date, week_end_date, teacher_id, teacher_id)
            )
            changes = {(row['date'], row['lesson_number']): row for row in await cursor.fetchall()}

            days = []
            day_names = ['', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
            for day_offset in range(7):
                current_date = week_start_date + timedelta(days=day_offset)
                day_of_week = current_date.weekday() + 1
                day_templates = [t for t in templates if t['day_of_week'] == day_of_week]

                day_lessons = []
                for t in day_templates:
                    change = changes.get((current_date, t['lesson_number']))
                    change_type = change['change_type'] if change else None
                    change_reason = change['reason'] if change else None
                    # В subject кладем НАЗВАНИЕ КЛАССА, а в teacher — НАЗВАНИЕ ПРЕДМЕТА
                    day_lessons.append(LessonItem(
                        lesson_number=t['lesson_number'],
                        start_time=str(t['start_time']),
                        end_time=str(t['end_time']),
                        subject=t.get('class_name') or 'Класс',
                        teacher=t.get('subject_name') or 'Предмет',
                        room=t.get('room_name') or 'Не указано',
                        change_type=change_type,
                        change_reason=change_reason
                    ))

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
                INSERT INTO holidays (date, name, type, affects_classes, description)
                VALUES (%s, %s, %s, %s, %s)
            """, (date, name, holiday_type, affects_classes, description))
            await conn.commit()
            return True
    except Exception as e:
        print(f"Error creating holiday: {e}")
        return False
    finally:
        conn.close()
