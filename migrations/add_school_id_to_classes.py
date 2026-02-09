#!/usr/bin/env python3
"""
Миграция: добавить school_id в classes и привязать существующие классы к школе 124 (id=2).
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
            # 1. Проверяем, есть ли уже колонка school_id
            await cursor.execute("""
                SELECT COLUMN_NAME 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_SCHEMA = DATABASE() 
                AND TABLE_NAME = 'classes' 
                AND COLUMN_NAME = 'school_id'
            """)
            if await cursor.fetchone():
                print("ℹ️ Колонка school_id уже есть в classes")
            else:
                await cursor.execute("""
                    ALTER TABLE classes 
                    ADD COLUMN school_id INT NULL,
                    ADD CONSTRAINT fk_classes_school 
                        FOREIGN KEY (school_id) REFERENCES schools(id)
                """)
                await conn.commit()
                print("✅ Колонка school_id добавлена в classes")

            # 2. Привязываем все классы к школе 124 (id=2)
            await cursor.execute("UPDATE classes SET school_id = 2 WHERE school_id IS NULL")
            updated = cursor.rowcount
            await conn.commit()
            print(f"✅ {updated} классов привязаны к школе 124 (id=2)")

    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
