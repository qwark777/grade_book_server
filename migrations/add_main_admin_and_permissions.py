#!/usr/bin/env python3
"""
Миграция: главные админы и права обычных админов.

1. school_admins: добавить is_main_admin BOOLEAN DEFAULT FALSE
2. admin_permissions: новая таблица (school_id, admin_user_id, permission_key)
   - Главные админы имеют все права автоматически
   - Обычные админы — только то, что указано в admin_permissions

Права: edit_school, edit_classes, edit_teachers, edit_students, edit_subjects,
       edit_schedule, edit_finances, edit_academic_periods, edit_holidays, manage_admins
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
            # 1. Проверяем и добавляем is_main_admin в school_admins
            await cursor.execute("""
                SELECT COLUMN_NAME 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_SCHEMA = DATABASE() 
                AND TABLE_NAME = 'school_admins' 
                AND COLUMN_NAME = 'is_main_admin'
            """)
            if await cursor.fetchone():
                print("ℹ️ Колонка is_main_admin уже есть в school_admins")
            else:
                await cursor.execute("""
                    ALTER TABLE school_admins 
                    ADD COLUMN is_main_admin BOOLEAN DEFAULT FALSE
                """)
                await conn.commit()
                print("✅ Колонка is_main_admin добавлена в school_admins")

            # 2. Делаем одного админа каждой школы главным (минимальный admin_user_id)
            await cursor.execute("""
                UPDATE school_admins sa
                INNER JOIN (
                    SELECT school_id, MIN(admin_user_id) as min_id
                    FROM school_admins GROUP BY school_id
                ) sub ON sa.school_id = sub.school_id AND sa.admin_user_id = sub.min_id
                SET sa.is_main_admin = TRUE
            """)
            await conn.commit()
            print("✅ Один админ каждой школы назначен главным")

            # 3. Создаём таблицу admin_permissions
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS admin_permissions (
                    school_id INT NOT NULL,
                    admin_user_id INT NOT NULL,
                    permission_key VARCHAR(50) NOT NULL,
                    PRIMARY KEY (school_id, admin_user_id, permission_key),
                    FOREIGN KEY (school_id) REFERENCES schools(id) ON DELETE CASCADE,
                    FOREIGN KEY (admin_user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)
            await conn.commit()
            print("✅ Таблица admin_permissions создана")

    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
