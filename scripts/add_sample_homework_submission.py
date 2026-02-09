#!/usr/bin/env python3
"""
Добавляет тестовую сданную работу для проверки функционала ДЗ.
Запуск: python scripts/add_sample_homework_submission.py
"""

import asyncio
import os
import sys

# Минимальный валидный PNG 1x1 пиксель (прозрачный)
MINI_PNG = bytes([
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D,
    0x49, 0x48, 0x44, 0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
    0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4, 0x89, 0x00, 0x00, 0x00,
    0x0A, 0x49, 0x44, 0x41, 0x54, 0x78, 0x9C, 0x63, 0x00, 0x01, 0x00, 0x00,
    0x05, 0x00, 0x01, 0x0D, 0x0A, 0x2D, 0xB4, 0x00, 0x00, 0x00, 0x00, 0x49,
    0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82
])

# Добавляем путь к app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.connection import get_db_connection
from app.core.config import settings


async def main():
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Найти любое ДЗ и ученика в этом классе
            await cursor.execute("""
                SELECT h.id AS homework_id, h.class_id
                FROM homeworks h
                LIMIT 1
            """)
            row = await cursor.fetchone()
            if not row:
                print("Нет домашних заданий в БД. Создайте ДЗ через урок в расписании.")
                return

            homework_id = row["homework_id"]
            class_id = row["class_id"]

            await cursor.execute("""
                SELECT cs.student_id
                FROM class_students cs
                WHERE cs.class_id = %s
                LIMIT 1
            """, (class_id,))
            student_row = await cursor.fetchone()
            if not student_row:
                print("Нет учеников в классе. Добавьте учеников в класс.")
                return

            student_id = student_row["student_id"]

            # Создать директорию и файл
            dir_path = os.path.join(settings.HOMEWORK_SUBMISSIONS_DIR, str(homework_id))
            os.makedirs(dir_path, exist_ok=True)
            fname = f"{homework_id}_{student_id}_sample.png"
            file_path = os.path.join(dir_path, fname)
            with open(file_path, "wb") as f:
                f.write(MINI_PNG)

            rel_path = f"{homework_id}/{fname}"
            await cursor.execute(
                """INSERT INTO homework_submissions (homework_id, student_id, file_path, file_name)
                   VALUES (%s, %s, %s, %s)""",
                (homework_id, student_id, rel_path, "sample_work.png")
            )
            await conn.commit()
            sid = cursor.lastrowid
            print(f"Добавлена тестовая работа:")
            print(f"  - homework_id: {homework_id}")
            print(f"  - student_id: {student_id}")
            print(f"  - submission_id: {sid}")
            print(f"  - file: {file_path}")
            print("Проверьте в приложении: ученик — вкладка ДЗ; учитель — Оценки → класс → ДЗ.")
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
