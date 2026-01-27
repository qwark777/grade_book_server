#!/usr/bin/env python3
"""
Скрипт для заполнения базы данных демо-данными для графиков успеваемости
Добавляет оценки для всех студентов за последние 2 месяца
"""

import asyncio
from datetime import date, timedelta
import random
import sys
import os

# Добавляем путь к модулям приложения
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

from app.db.connection import get_db_connection


async def seed_performance_data():
    """
    Добавляет демо-данные для всех студентов:
    - Оценки за последние 2 месяца (60 дней)
    - Разные предметы
    - Реалистичные тренды
    """
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Получаем всех студентов
            await cursor.execute("""
                SELECT u.id, p.full_name 
                FROM users u
                LEFT JOIN profiles p ON p.user_id = u.id
                WHERE u.role = 'student'
            """)
            students = await cursor.fetchall()
            
            if not students:
                print("❌ Нет студентов в базе данных!")
                return
            
            print(f"📊 Найдено студентов: {len(students)}")
            
            # Получаем предметы
            await cursor.execute("SELECT id, name FROM subjects")
            subjects = await cursor.fetchall()
            
            if not subjects:
                print("❌ Нет предметов в базе! Создаю базовые предметы...")
                await cursor.execute("""
                    INSERT IGNORE INTO subjects (name, coefficient) VALUES
                    ('Математика', 1.5),
                    ('Физика', 1.5),
                    ('Русский язык', 1.2),
                    ('История', 1.0),
                    ('Химия', 1.3),
                    ('Английский язык', 1.4),
                    ('Биология', 1.1),
                    ('География', 1.0),
                    ('Информатика', 1.3),
                    ('Физкультура', 0.8)
                """)
                await cursor.execute("SELECT id, name FROM subjects")
                subjects = await cursor.fetchall()
            
            print(f"📚 Найдено предметов: {len(subjects)}")
            
            # Получаем teacher_id
            await cursor.execute("SELECT id FROM users WHERE role = 'teacher' LIMIT 1")
            teacher = await cursor.fetchone()
            teacher_id = teacher['id'] if teacher else 1
            
            if not teacher:
                print("⚠️  Учитель не найден, используем ID=1")
            
            # Для каждого студента добавляем оценки
            total_grades_added = 0
            
            for student in students:
                student_id = student['id']
                student_name = student.get('full_name', f'Студент {student_id}')
                
                # Удаляем старые оценки (опционально, можно закомментировать)
                # await cursor.execute("DELETE FROM grades WHERE student_id = %s", (student_id,))
                
                # Генерируем оценки за последние 60 дней
                start_date = date.today() - timedelta(days=60)
                grades_for_student = 0
                
                # Создаем тренд для каждого студента (случайный, но реалистичный)
                # Базовый средний балл от 3.0 до 4.5
                base_avg = random.uniform(3.0, 4.5)
                # Тренд: улучшение или ухудшение
                trend_direction = random.choice([-0.3, -0.2, -0.1, 0, 0.1, 0.2, 0.3])
                
                for i in range(60):
                    current_date = start_date + timedelta(days=i)
                    
                    # Пропускаем выходные
                    if current_date.weekday() >= 5:
                        continue
                    
                    # Прогрессия тренда (от 0 до 1)
                    progress = i / 60
                    # Текущий средний балл с учетом тренда
                    current_avg = base_avg + (trend_direction * progress)
                    current_avg = max(2.0, min(5.0, current_avg))  # Ограничиваем от 2 до 5
                    
                    # Каждый день 1-3 оценки (70% вероятность)
                    if random.random() < 0.7:
                        num_grades = random.randint(1, 3)
                        
                        for _ in range(num_grades):
                            subject = random.choice(subjects)
                            subject_id = subject['id']
                            
                            # Генерируем оценку на основе текущего среднего
                            # С небольшим разбросом
                            if current_avg >= 4.5:
                                # Отличник: в основном 4 и 5
                                grade_value = random.choices(
                                    [4, 5], 
                                    weights=[30, 70]
                                )[0]
                            elif current_avg >= 4.0:
                                # Хорошист: в основном 4, иногда 5 и 3
                                grade_value = random.choices(
                                    [3, 4, 5], 
                                    weights=[10, 70, 20]
                                )[0]
                            elif current_avg >= 3.5:
                                # Средний: в основном 3 и 4
                                grade_value = random.choices(
                                    [3, 4, 5], 
                                    weights=[40, 50, 10]
                                )[0]
                            elif current_avg >= 3.0:
                                # Ниже среднего: в основном 3, иногда 2 и 4
                                grade_value = random.choices(
                                    [2, 3, 4], 
                                    weights=[20, 60, 20]
                                )[0]
                            else:
                                # Плохой: в основном 2 и 3
                                grade_value = random.choices(
                                    [2, 3, 4], 
                                    weights=[50, 40, 10]
                                )[0]
                            
                            # Проверяем, нет ли уже оценки за этот день и предмет
                            await cursor.execute("""
                                SELECT COUNT(*) as count 
                                FROM grades 
                                WHERE student_id = %s 
                                AND subject_id = %s 
                                AND date = %s
                            """, (student_id, subject_id, current_date))
                            
                            existing = await cursor.fetchone()
                            if existing['count'] == 0:
                                await cursor.execute("""
                                    INSERT INTO grades (student_id, subject_id, value, date, teacher_id)
                                    VALUES (%s, %s, %s, %s, %s)
                                """, (student_id, subject_id, grade_value, current_date, teacher_id))
                                grades_for_student += 1
                
                total_grades_added += grades_for_student
                print(f"   ✅ {student_name} (ID: {student_id}): добавлено {grades_for_student} оценок")
            
            await conn.commit()
            
            print(f"\n✅ Всего добавлено оценок: {total_grades_added}")
            print(f"📈 Данные добавлены за последние 60 дней")
            print(f"\n💡 Теперь графики должны строиться для всех студентов!")
            print(f"   API эндпоинты:")
            print(f"   - GET /api/v1/performance-trends/{{student_id}}")
            print(f"   - GET /api/v1/grade-distribution/{{student_id}}")
            print(f"   - GET /api/v1/attendance-stats/{{student_id}}")
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(seed_performance_data())
