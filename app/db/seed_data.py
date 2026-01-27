"""
Скрипт для заполнения базы данных тестовыми данными расписания
"""

import asyncio
from datetime import date, time, timedelta
from app.db.connection import get_db_connection


async def seed_timetable_data():
    """Заполнить базу данных тестовыми данными"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # 1. Создаем комнаты
            await cursor.execute("""
                INSERT IGNORE INTO rooms (id, name, building, room_type) VALUES
                (1, '301', 'Главный корпус', 'classroom'),
                (2, '302', 'Главный корпус', 'classroom'),
                (3, 'Спортзал', 'Спортивный комплекс', 'gym'),
                (4, 'Лаборатория', 'Главный корпус', 'lab'),
                (5, 'Актовый зал', 'Главный корпус', 'auditorium')
            """)
            
            # 2. Создаем учебный период
            await cursor.execute("""
                INSERT IGNORE INTO academic_periods (id, name, start_date, end_date, is_active) VALUES
                (1, '1 семестр 2024/2025', '2024-09-01', '2024-12-31', TRUE)
            """)
            
            # 3. Создаем предметы (если их еще нет)
            await cursor.execute("""
                INSERT IGNORE INTO subjects (id, name, coefficient) VALUES
                (1, 'Математика', 1.5),
                (2, 'Информатика', 1.3),
                (3, 'Русский язык', 1.2),
                (4, 'История', 1.0),
                (5, 'Английский язык', 1.4),
                (6, 'Физика', 1.5),
                (7, 'Химия', 1.3),
                (8, 'Биология', 1.1),
                (9, 'География', 1.0),
                (10, 'Физкультура', 0.8)
            """)
            
            # 4. Создаем класс (если его еще нет)
            await cursor.execute("""
                INSERT IGNORE INTO classes (id, name, academic_year) VALUES
                (1, '10А', '2024/2025')
            """)
            
            # 5. Создаем шаблон расписания для класса 10А
            # Понедельник
            await cursor.execute("""
                INSERT IGNORE INTO timetable_templates 
                (class_id, subject_id, teacher_id, room_id, day_of_week, lesson_number, 
                 start_time, end_time, week_type, academic_period_id) VALUES
                (1, 1, 1, 1, 1, 1, '08:00:00', '08:45:00', 'BOTH', 1),
                (1, 2, 1, 2, 1, 2, '08:55:00', '09:40:00', 'BOTH', 1),
                (1, 3, 1, 1, 1, 3, '10:00:00', '10:45:00', 'BOTH', 1),
                (1, 4, 1, 2, 1, 4, '11:05:00', '11:50:00', 'BOTH', 1),
                (1, 5, 1, 1, 1, 5, '12:00:00', '12:45:00', 'BOTH', 1)
            """)
            
            # Вторник
            await cursor.execute("""
                INSERT IGNORE INTO timetable_templates 
                (class_id, subject_id, teacher_id, room_id, day_of_week, lesson_number, 
                 start_time, end_time, week_type, academic_period_id) VALUES
                (1, 6, 1, 4, 2, 1, '08:00:00', '08:45:00', 'BOTH', 1),
                (1, 7, 1, 4, 2, 2, '08:55:00', '09:40:00', 'BOTH', 1),
                (1, 1, 1, 1, 2, 3, '10:00:00', '10:45:00', 'BOTH', 1),
                (1, 8, 1, 2, 2, 4, '11:05:00', '11:50:00', 'BOTH', 1),
                (1, 9, 1, 1, 2, 5, '12:00:00', '12:45:00', 'BOTH', 1)
            """)
            
            # Среда
            await cursor.execute("""
                INSERT IGNORE INTO timetable_templates 
                (class_id, subject_id, teacher_id, room_id, day_of_week, lesson_number, 
                 start_time, end_time, week_type, academic_period_id) VALUES
                (1, 2, 1, 2, 3, 1, '08:00:00', '08:45:00', 'BOTH', 1),
                (1, 3, 1, 1, 3, 2, '08:55:00', '09:40:00', 'BOTH', 1),
                (1, 10, 1, 3, 3, 3, '10:00:00', '10:45:00', 'BOTH', 1),
                (1, 4, 1, 2, 3, 4, '11:05:00', '11:50:00', 'BOTH', 1),
                (1, 5, 1, 1, 3, 5, '12:00:00', '12:45:00', 'BOTH', 1)
            """)
            
            # Четверг
            await cursor.execute("""
                INSERT IGNORE INTO timetable_templates 
                (class_id, subject_id, teacher_id, room_id, day_of_week, lesson_number, 
                 start_time, end_time, week_type, academic_period_id) VALUES
                (1, 1, 1, 1, 4, 1, '08:00:00', '08:45:00', 'BOTH', 1),
                (1, 6, 1, 4, 4, 2, '08:55:00', '09:40:00', 'BOTH', 1),
                (1, 2, 1, 2, 4, 3, '10:00:00', '10:45:00', 'BOTH', 1),
                (1, 7, 1, 4, 4, 4, '11:05:00', '11:50:00', 'BOTH', 1),
                (1, 3, 1, 1, 4, 5, '12:00:00', '12:45:00', 'BOTH', 1)
            """)
            
            # Пятница
            await cursor.execute("""
                INSERT IGNORE INTO timetable_templates 
                (class_id, subject_id, teacher_id, room_id, day_of_week, lesson_number, 
                 start_time, end_time, week_type, academic_period_id) VALUES
                (1, 8, 1, 2, 5, 1, '08:00:00', '08:45:00', 'BOTH', 1),
                (1, 9, 1, 1, 5, 2, '08:55:00', '09:40:00', 'BOTH', 1),
                (1, 10, 1, 3, 5, 3, '10:00:00', '10:45:00', 'BOTH', 1),
                (1, 5, 1, 1, 5, 4, '11:05:00', '11:50:00', 'BOTH', 1),
                (1, 1, 1, 2, 5, 5, '12:00:00', '12:45:00', 'BOTH', 1)
            """)
            
            # 6. Создаем несколько праздников
            await cursor.execute("""
                INSERT IGNORE INTO holidays (date, name, type, affects_classes, description) VALUES
                ('2024-12-31', 'Новый год', 'holiday', TRUE, 'Новогодние каникулы'),
                ('2025-01-01', 'Новый год', 'holiday', TRUE, 'Новогодние каникулы'),
                ('2025-01-02', 'Новогодние каникулы', 'vacation', TRUE, 'Новогодние каникулы'),
                ('2025-01-03', 'Новогодние каникулы', 'vacation', TRUE, 'Новогодние каникулы'),
                ('2025-01-04', 'Новогодние каникулы', 'vacation', TRUE, 'Новогодние каникулы'),
                ('2025-01-05', 'Новогодние каникулы', 'vacation', TRUE, 'Новогодние каникулы'),
                ('2025-01-06', 'Новогодние каникулы', 'vacation', TRUE, 'Новогодние каникулы'),
                ('2025-01-07', 'Рождество', 'holiday', TRUE, 'Рождество Христово'),
                ('2025-01-08', 'Новогодние каникулы', 'vacation', TRUE, 'Новогодние каникулы')
            """)
            
            # 7. Создаем несколько недель
            start_date = date(2024, 12, 30)  # Понедельник
            for i in range(10):  # 10 недель
                week_start = start_date + timedelta(weeks=i)
                week_end = week_start + timedelta(days=6)
                week_number = i + 1
                week_type = 'A' if i % 2 == 0 else 'B'
                
                await cursor.execute("""
                    INSERT IGNORE INTO academic_weeks 
                    (week_start_date, week_end_date, week_number, week_type, academic_period_id) VALUES
                    (%s, %s, %s, %s, 1)
                """, (week_start, week_end, week_number, week_type))
            
            # 8. Создаем несколько замен
            await cursor.execute("""
                INSERT IGNORE INTO timetable_changes 
                (date, class_id, lesson_number, change_type, reason, created_by) VALUES
                ('2024-12-30', 1, 2, 'cancel', 'Учитель на совещании', 1),
                ('2024-12-31', 1, 1, 'cancel', 'Новогодние каникулы', 1),
                ('2025-01-02', 1, 3, 'replace', 'Замена по болезни', 1)
            """)
            
            await conn.commit()
            print("✅ Тестовые данные расписания успешно добавлены!")
            
    except Exception as e:
        print(f"❌ Ошибка при добавлении тестовых данных: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(seed_timetable_data())

