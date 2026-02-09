#!/usr/bin/env python3
"""
Скрипт для добавления расписания учителю по username.
Создаёт шаблон расписания (timetable_templates) для указанного учителя.

Использование:
    python3 scripts/add_teacher_timetable.py qweqwe
    python3 scripts/add_teacher_timetable.py --username qweqwe
"""

import asyncio
import argparse
import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db.connection import get_db_connection


# Стандартные слоты: урок номер, начало, конец
LESSON_SLOTS = [
    (1, "08:00:00", "08:45:00"),
    (2, "08:55:00", "09:40:00"),
    (3, "10:00:00", "10:45:00"),
    (4, "11:05:00", "11:50:00"),
    (5, "12:00:00", "12:45:00"),
]


async def add_teacher_timetable(username: str) -> bool:
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # 1) Найти пользователя teacher с username
            await cursor.execute(
                "SELECT id FROM users WHERE username = %s AND role = 'teacher'",
                (username,),
            )
            row = await cursor.fetchone()
            if not row:
                print(f"❌ Учитель с username '{username}' не найден (или не роль teacher).")
                return False
            teacher_id = row["id"] if hasattr(row, "get") else row[0]
            print(f"✅ Учитель: {username} (id={teacher_id})")

            # 2) Учебный период (активный или первый)
            await cursor.execute(
                "SELECT id FROM academic_periods WHERE is_active = TRUE LIMIT 1"
            )
            period_row = await cursor.fetchone()
            if not period_row:
                await cursor.execute(
                    "SELECT id FROM academic_periods ORDER BY id DESC LIMIT 1"
                )
                period_row = await cursor.fetchone()
            if not period_row:
                today = date.today()
                await cursor.execute(
                    """INSERT INTO academic_periods (name, start_date, end_date, is_active)
                       VALUES (%s, %s, %s, TRUE)""",
                    ("Учебный год", today, date(today.year + 1, 6, 30)),
                )
                await conn.commit()
                period_id = cursor.lastrowid
                print(f"   Создан учебный период id={period_id}")
            else:
                period_id = period_row["id"] if hasattr(period_row, "get") else period_row[0]
                print(f"   Учебный период id={period_id}")

            # 3) Класс: где учитель уже привязан (class_teachers или class_subject_teachers) или первый класс в БД
            await cursor.execute(
                """SELECT c.id FROM classes c
                   INNER JOIN class_teachers ct ON ct.class_id = c.id AND ct.teacher_id = %s
                   LIMIT 1""",
                (teacher_id,),
            )
            class_row = await cursor.fetchone()
            if not class_row:
                await cursor.execute(
                    """SELECT c.id FROM classes c
                       INNER JOIN class_subject_teachers cst ON cst.class_id = c.id AND cst.teacher_id = %s
                       LIMIT 1""",
                    (teacher_id,),
                )
                class_row = await cursor.fetchone()
            if not class_row:
                await cursor.execute("SELECT id FROM classes ORDER BY id LIMIT 1")
                class_row = await cursor.fetchone()
            if not class_row:
                print("❌ В БД нет ни одного класса. Создайте класс и привяжите к нему учителя.")
                return False
            class_id = class_row["id"] if hasattr(class_row, "get") else class_row[0]
            await cursor.execute("SELECT name FROM classes WHERE id = %s", (class_id,))
            cn = await cursor.fetchone()
            class_name = cn.get("name", class_id) if hasattr(cn, "get") else class_id
            print(f"   Класс: id={class_id} ({class_name})")

            # 4) Предмет: из teacher_subjects или первый предмет в БД
            await cursor.execute(
                "SELECT subject_id FROM teacher_subjects WHERE teacher_id = %s LIMIT 1",
                (teacher_id,),
            )
            sub_row = await cursor.fetchone()
            if not sub_row:
                await cursor.execute("SELECT id FROM subjects ORDER BY id LIMIT 1")
                sub_row = await cursor.fetchone()
            if not sub_row:
                await cursor.execute(
                    "INSERT INTO subjects (name, coefficient) VALUES ('Математика', 1.5)"
                )
                await conn.commit()
                subject_id = cursor.lastrowid
                await cursor.execute(
                    "INSERT IGNORE INTO teacher_subjects (teacher_id, subject_id) VALUES (%s, %s)",
                    (teacher_id, subject_id),
                )
                await conn.commit()
                print(f"   Создан предмет Математика id={subject_id} и привязка к учителю")
            else:
                subject_id = sub_row.get("subject_id") or sub_row.get("id") or sub_row[0]
                print(f"   Предмет id={subject_id}")

            # 5) Кабинет: первый room или создаём
            await cursor.execute("SELECT id FROM rooms ORDER BY id LIMIT 1")
            room_row = await cursor.fetchone()
            if not room_row:
                await cursor.execute(
                    "INSERT INTO rooms (name, building, room_type) VALUES ('201', 'Корпус 1', 'classroom')"
                )
                await conn.commit()
                room_id = cursor.lastrowid
            else:
                room_id = room_row["id"] if hasattr(room_row, "get") else room_row[0]

            # 6) Привязка учителя к классу/предмету если ещё нет
            await cursor.execute(
                "SELECT 1 FROM class_teachers WHERE class_id = %s AND teacher_id = %s",
                (class_id, teacher_id),
            )
            if not await cursor.fetchone():
                await cursor.execute(
                    "INSERT IGNORE INTO class_teachers (class_id, teacher_id) VALUES (%s, %s)",
                    (class_id, teacher_id),
                )
                await conn.commit()
            await cursor.execute(
                "SELECT 1 FROM class_subjects WHERE class_id = %s AND subject_id = %s",
                (class_id, subject_id),
            )
            if not await cursor.fetchone():
                await cursor.execute(
                    "INSERT IGNORE INTO class_subjects (class_id, subject_id) VALUES (%s, %s)",
                    (class_id, subject_id),
                )
                await conn.commit()
            await cursor.execute(
                "SELECT 1 FROM class_subject_teachers WHERE class_id = %s AND subject_id = %s AND teacher_id = %s",
                (class_id, subject_id, teacher_id),
            )
            if not await cursor.fetchone():
                await cursor.execute(
                    """INSERT IGNORE INTO class_subject_teachers (class_id, subject_id, teacher_id)
                       VALUES (%s, %s, %s)""",
                    (class_id, subject_id, teacher_id),
                )
                await conn.commit()

            # 7) Вставить расписание: Пн–Пт по несколько уроков (день 1–5, day_of_week 1=Пн)
            inserted = 0
            for day_of_week in range(1, 6):  # Пн–Пт
                for lesson_number, start_time, end_time in LESSON_SLOTS[:3]:  # по 3 урока
                    try:
                        await cursor.execute(
                            """INSERT INTO timetable_templates
                               (class_id, subject_id, teacher_id, room_id, day_of_week, lesson_number,
                                start_time, end_time, week_type, academic_period_id)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'BOTH', %s)""",
                            (
                                class_id,
                                subject_id,
                                teacher_id,
                                room_id,
                                day_of_week,
                                lesson_number,
                                start_time,
                                end_time,
                                period_id,
                            ),
                        )
                        inserted += 1
                    except Exception as e:
                        if "Duplicate" in str(e) or "unique" in str(e).lower():
                            pass  # уже есть такой слот
                        else:
                            print(f"   ⚠️  Ошибка вставки день={day_of_week} урок={lesson_number}: {e}")
            await conn.commit()
            print(f"✅ Добавлено записей в расписание: {inserted}")
            print("   Учитель может открыть раздел «Расписание» в приложении.")
            return True
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Добавить расписание учителю по username")
    parser.add_argument(
        "username",
        nargs="?",
        default="qweqwe",
        help="Username учителя (по умолчанию: qweqwe)",
    )
    parser.add_argument("--username", "-u", dest="username_opt", help="Username учителя")
    args = parser.parse_args()
    username = args.username_opt or args.username
    if not username:
        username = "qweqwe"
    asyncio.run(add_teacher_timetable(username))


if __name__ == "__main__":
    main()
