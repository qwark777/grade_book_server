#!/usr/bin/env python3
"""
Скрипт для добавления плохих данных студенту ID 1 для тестирования AI-советов
"""
import asyncio
from datetime import date, timedelta
import random
import sys
import os

# Добавляем путь к модулям приложения
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

from app.db.connection import get_db_connection


async def add_bad_data_for_student_1():
    """
    Добавляет плохие данные для студента ID 1:
    - Плохие оценки (много 2 и 3)
    - Много пропусков
    - Негативный тренд (ухудшение)
    """
    student_id = 1
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем, существует ли студент
            await cursor.execute("SELECT id FROM users WHERE id = %s", (student_id,))
            student = await cursor.fetchone()
            
            if not student:
                print(f"❌ Студент с ID {student_id} не найден!")
                print("💡 Создаю тестового студента...")
                await cursor.execute("""
                    INSERT IGNORE INTO users (id, username, hashed_password, role)
                    VALUES (%s, %s, '$2y$10$test', 'student')
                """, (student_id, f'student_{student_id}'))
                
                await cursor.execute("""
                    INSERT IGNORE INTO profiles (user_id, full_name)
                    VALUES (%s, 'Тестовый Студент 1')
                """, (student_id,))
                await conn.commit()
                print(f"✅ Студент {student_id} создан")
            
            # Получаем предметы
            await cursor.execute("SELECT id, name FROM subjects LIMIT 10")
            subjects = await cursor.fetchall()
            
            if not subjects:
                print("❌ Нет предметов в базе! Создаю базовые предметы...")
                await cursor.execute("""
                    INSERT IGNORE INTO subjects (name, coefficient) VALUES
                    ('Математика', 1.5),
                    ('Физика', 1.5),
                    ('Русский язык', 1.2),
                    ('История', 1.0),
                    ('Химия', 1.3)
                """)
                await cursor.execute("SELECT id, name FROM subjects")
                subjects = await cursor.fetchall()
            
            # Получаем teacher_id
            await cursor.execute("SELECT id FROM users WHERE role = 'teacher' LIMIT 1")
            teacher = await cursor.fetchone()
            teacher_id = teacher['id'] if teacher else 1
            
            print(f"\n📊 Добавляю плохие данные для студента {student_id}...")
            print(f"   Предметов: {len(subjects)}")
            print(f"   Учитель ID: {teacher_id}\n")
            
            # УДАЛЯЕМ СТАРЫЕ ОЦЕНКИ для чистоты эксперимента
            await cursor.execute("DELETE FROM grades WHERE student_id = %s", (student_id,))
            print("🗑️  Удалены старые оценки")
            
            # 1. Добавляем ПЛОХИЕ оценки с негативным трендом
            print("1️⃣  Добавляю плохие оценки с негативным трендом...")
            start_date = date.today() - timedelta(days=90)  # Последние 3 месяца
            grades_added = 0
            
            for i in range(90):  # 90 дней
                current_date = start_date + timedelta(days=i)
                
                # Пропускаем выходные
                if current_date.weekday() >= 5:
                    continue
                
                # Прогрессия: в начале хорошие оценки, в конце плохие
                progress = i / 90  # От 0 до 1
                
                # Каждый день 1-2 оценки
                num_grades = random.randint(1, 2) if random.random() < 0.7 else 0
                
                for _ in range(num_grades):
                    subject = random.choice(subjects)
                    subject_id = subject['id']
                    
                    # Прогрессия: сначала хорошие (4-5), потом плохие (2-3)
                    if progress < 0.2:  # Первые 20% - хорошие (4 и 5)
                        grade_value = random.choice([4, 4, 5, 4, 5])
                    elif progress < 0.5:  # Средние 30% - средние (3 и 4)
                        grade_value = random.choice([3, 3, 4, 3, 4])
                    else:  # Последние 50% - плохие (2 и 3)
                        # Больше двоек в конце
                        grade_value = random.choice([2, 2, 3, 2, 2, 3])
                    
                    await cursor.execute("""
                        INSERT INTO grades (student_id, subject_id, value, date, teacher_id)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (student_id, subject_id, grade_value, current_date, teacher_id))
                    grades_added += 1
            
            print(f"   ✅ Добавлено {grades_added} оценок с негативным трендом")
            
            # 2. Добавляем МНОГО пропусков (плохая посещаемость)
            print("\n2️⃣  Добавляю плохую посещаемость (много пропусков)...")
            
            try:
                # Проверяем, существует ли таблица attendance
                await cursor.execute("""
                    SELECT COUNT(*) as count FROM information_schema.tables 
                    WHERE table_schema = DATABASE() AND table_name = 'attendance'
                """)
                table_exists = (await cursor.fetchone())['count'] > 0
                
                if not table_exists:
                    print("   ⚠️  Таблица attendance не существует, пропускаю...")
                else:
                    # Удаляем старые данные посещаемости
                    await cursor.execute("DELETE FROM attendance WHERE student_id = %s", (student_id,))
                    
                    start_date_attendance = date.today() - timedelta(days=30)
                    absences = 0
                    lates = 0
                    
                    for i in range(30):
                        current_date = start_date_attendance + timedelta(days=i)
                        
                        if current_date.weekday() >= 5:
                            continue
                        
                        num_lessons = random.randint(5, 6)
                        
                        for lesson_num in range(1, num_lessons + 1):
                            subject = random.choice(subjects)
                            subject_id = subject['id']
                            
                            # Плохая посещаемость: 45% отсутствие, 20% опоздание, 35% присутствие
                            rand = random.random()
                            if rand < 0.45:  # 45% - отсутствие
                                status = 'absent'
                                absences += 1
                            elif rand < 0.65:  # 20% - опоздание
                                status = 'late'
                                lates += 1
                            else:  # 35% - присутствие
                                status = 'present'
                            
                            await cursor.execute("""
                                INSERT INTO attendance 
                                (student_id, date, status, lesson_number, subject_id, teacher_id)
                                VALUES (%s, %s, %s, %s, %s, %s)
                            """, (student_id, current_date, status, lesson_num, subject_id, teacher_id))
                    
                    print(f"   ✅ Добавлено пропусков: {absences}, опозданий: {lates}")
            except Exception as e:
                print(f"   ⚠️  Ошибка при добавлении посещаемости: {e}")
            
            await conn.commit()
            
            # Выводим статистику
            print("\n📈 Статистика добавленных данных:")
            await cursor.execute("""
                SELECT 
                    COUNT(*) as total_grades,
                    AVG(value) as avg_grade,
                    MIN(value) as min_grade,
                    MAX(value) as max_grade,
                    COUNT(CASE WHEN value = 2 THEN 1 END) as grade_2,
                    COUNT(CASE WHEN value = 3 THEN 1 END) as grade_3,
                    COUNT(CASE WHEN value = 4 THEN 1 END) as grade_4,
                    COUNT(CASE WHEN value = 5 THEN 1 END) as grade_5
                FROM grades
                WHERE student_id = %s
            """, (student_id,))
            stats = await cursor.fetchone()
            
            print(f"   Всего оценок: {stats['total_grades']}")
            if stats['total_grades'] > 0:
                print(f"   Средний балл: {stats['avg_grade']:.2f} ⚠️")
                print(f"   Минимум: {stats['min_grade']}, Максимум: {stats['max_grade']}")
                print(f"   Двоек: {stats['grade_2']}, Троек: {stats['grade_3']}")
                print(f"   Четверок: {stats['grade_4']}, Пятерок: {stats['grade_5']}")
            
            print(f"\n✅ Плохие данные успешно добавлены для студента {student_id}!")
            print(f"💡 Теперь проверьте AI-советы для студента {student_id}")
            print(f"   API: GET /api/v1/ai-advice/{student_id}")
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(add_bad_data_for_student_1())

