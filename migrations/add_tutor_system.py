#!/usr/bin/env python3
"""
Миграция для добавления системы репетиторов и дополнительных занятий
"""

import asyncio
import aiomysql
from app.core.config import settings


async def add_tutor_system():
    """Добавляет систему репетиторов и дополнительных занятий"""
    conn = await aiomysql.connect(
        host=settings.MYSQL_HOST,
        port=settings.MYSQL_PORT,
        user=settings.MYSQL_USER,
        password=settings.MYSQL_PASSWORD,
        db=settings.MYSQL_DB,
        cursorclass=aiomysql.DictCursor,
        charset="utf8mb4",
        use_unicode=True
    )
    
    try:
        async with conn.cursor() as cursor:
            # Обновляем существующих пользователей с ролью 'teacher' на 'tutor' если нужно
            # (это опционально, можно оставить как есть)
            
            # Создаем таблицу для дополнительных занятий
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS additional_lessons (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    title VARCHAR(255) NOT NULL,
                    description TEXT,
                    subject VARCHAR(100) NOT NULL,
                    tutor_id INT NOT NULL,
                    school_id INT NULL, -- NULL для частных репетиторов
                    price DECIMAL(10,2) NULL, -- NULL для бесплатных занятий
                    max_students INT DEFAULT 10,
                    duration_minutes INT DEFAULT 60,
                    is_online BOOLEAN DEFAULT FALSE,
                    location TEXT NULL, -- Адрес для очных занятий
                    online_link VARCHAR(500) NULL, -- Ссылка для онлайн занятий
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (tutor_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (school_id) REFERENCES schools(id) ON DELETE SET NULL
                )
            ''')
            
            # Создаем таблицу для записи студентов на дополнительные занятия
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS lesson_enrollments (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    lesson_id INT NOT NULL,
                    student_id INT NOT NULL,
                    enrolled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status ENUM('enrolled', 'completed', 'cancelled') DEFAULT 'enrolled',
                    payment_status ENUM('pending', 'paid', 'refunded') DEFAULT 'pending',
                    FOREIGN KEY (lesson_id) REFERENCES additional_lessons(id) ON DELETE CASCADE,
                    FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE,
                    UNIQUE KEY unique_enrollment (lesson_id, student_id)
                )
            ''')
            
            # Создаем таблицу для расписания дополнительных занятий
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS lesson_schedule (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    lesson_id INT NOT NULL,
                    start_datetime DATETIME NOT NULL,
                    end_datetime DATETIME NOT NULL,
                    is_recurring BOOLEAN DEFAULT FALSE,
                    recurrence_pattern VARCHAR(50) NULL, -- 'weekly', 'monthly', etc.
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (lesson_id) REFERENCES additional_lessons(id) ON DELETE CASCADE
                )
            ''')
            
            # Создаем таблицу для отзывов о занятиях
            await cursor.execute('''
                CREATE TABLE IF NOT EXISTS lesson_reviews (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    lesson_id INT NOT NULL,
                    student_id INT NOT NULL,
                    rating INT NOT NULL CHECK (rating >= 1 AND rating <= 5),
                    comment TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (lesson_id) REFERENCES additional_lessons(id) ON DELETE CASCADE,
                    FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE,
                    UNIQUE KEY unique_review (lesson_id, student_id)
                )
            ''')
            
            print("✅ Таблицы для системы репетиторов созданы успешно")
            
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(add_tutor_system())
