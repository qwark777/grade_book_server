#!/usr/bin/env python3
"""
Миграция: учебный план (curriculum) — часы в неделю по классам и предметам.
Используется для автогенерации расписания.
"""

import asyncio
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from app.db.connection import get_db_connection


async def migrate():
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS curriculum (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    class_id INT NOT NULL,
                    subject_id INT NOT NULL,
                    hours_per_week INT NOT NULL DEFAULT 0,
                    school_id INT NULL,
                    UNIQUE KEY unique_class_subject (class_id, subject_id),
                    FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE,
                    FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
                    FOREIGN KEY (school_id) REFERENCES schools(id) ON DELETE SET NULL,
                    INDEX idx_school (school_id)
                )
            """)
            await conn.commit()
            print("✅ Таблица curriculum создана")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
