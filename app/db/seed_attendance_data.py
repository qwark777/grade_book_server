"""
Скрипт для заполнения базы данных тестовыми данными посещаемости
"""

import asyncio
import random
from datetime import date, timedelta
from app.db.connection import get_db_connection


async def seed_attendance_data():
    """Заполнить базу данных тестовыми данными посещаемости"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Сначала создаем таблицу attendance
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS attendance (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    student_id INT NOT NULL,
                    date DATE NOT NULL,
                    status ENUM('present', 'late', 'absent') NOT NULL,
                    lesson_number TINYINT,
                    subject_id INT,
                    teacher_id INT,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (student_id) REFERENCES users(id),
                    FOREIGN KEY (subject_id) REFERENCES subjects(id),
                    FOREIGN KEY (teacher_id) REFERENCES users(id),
                    UNIQUE KEY unique_attendance (student_id, date, lesson_number)
                )
            """)
            
            # Создаем индексы (MySQL не поддерживает IF NOT EXISTS для индексов)
            try:
                await cursor.execute("""
                    CREATE INDEX idx_attendance_student_date ON attendance(student_id, date)
                """)
            except:
                pass  # Индекс уже существует
            
            try:
                await cursor.execute("""
                    CREATE INDEX idx_attendance_date ON attendance(date)
                """)
            except:
                pass  # Индекс уже существует
                
            try:
                await cursor.execute("""
                    CREATE INDEX idx_attendance_status ON attendance(status)
                """)
            except:
                pass  # Индекс уже существует
            
            # Получаем всех студентов
            await cursor.execute("SELECT id FROM users WHERE role = 'student'")
            students = await cursor.fetchall()
            
            if not students:
                print("❌ Нет студентов в базе данных")
                return
            
            # Получаем предметы
            await cursor.execute("SELECT id FROM subjects")
            subjects = await cursor.fetchall()
            
            if not subjects:
                print("❌ Нет предметов в базе данных")
                return
            
            # Генерируем данные посещаемости за последние 30 дней
            start_date = date.today() - timedelta(days=30)
            
            for student in students:
                student_id = student['id']
                
                # Генерируем посещаемость для каждого дня
                for i in range(30):
                    current_date = start_date + timedelta(days=i)
                    
                    # Пропускаем выходные
                    if current_date.weekday() >= 5:  # 5 = суббота, 6 = воскресенье
                        continue
                    
                    # Генерируем 5-6 уроков в день
                    num_lessons = random.randint(5, 6)
                    
                    for lesson_num in range(1, num_lessons + 1):
                        # Выбираем случайный предмет
                        subject = random.choice(subjects)
                        subject_id = subject['id']
                        
                        # Генерируем статус посещаемости
                        # 85% присутствие, 10% опоздание, 5% отсутствие
                        rand = random.random()
                        if rand < 0.85:
                            status = 'present'
                        elif rand < 0.95:
                            status = 'late'
                        else:
                            status = 'absent'
                        
                        # Вставляем запись посещаемости
                        await cursor.execute("""
                            INSERT IGNORE INTO attendance 
                            (student_id, date, status, lesson_number, subject_id, teacher_id) 
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (student_id, current_date, status, lesson_num, subject_id, 1))
            
            await conn.commit()
            print("✅ Тестовые данные посещаемости успешно добавлены!")
            
    except Exception as e:
        print(f"❌ Ошибка при добавлении тестовых данных посещаемости: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(seed_attendance_data())
