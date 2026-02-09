#!/usr/bin/env python3
"""
Миграция: grade, is_reviewed, comment для homework_submissions
"""

import asyncio
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from app.db.connection import get_db_connection


async def run():
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            for col, sql in [
                ("grade", "ALTER TABLE homework_submissions ADD COLUMN grade TINYINT NULL"),
                ("is_reviewed", "ALTER TABLE homework_submissions ADD COLUMN is_reviewed BOOLEAN DEFAULT FALSE"),
                ("comment", "ALTER TABLE homework_submissions ADD COLUMN comment TEXT NULL"),
            ]:
                await cursor.execute("""
                    SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'homework_submissions' AND COLUMN_NAME = %s
                """, (col,))
                if not await cursor.fetchone():
                    await cursor.execute(sql)
                    await conn.commit()
                    print(f"✅ Добавлена колонка {col}")
                else:
                    print(f"ℹ️ Колонка {col} уже есть")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(run())
