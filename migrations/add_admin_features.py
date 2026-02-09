#!/usr/bin/env python3
"""
Миграция: архив классов, настройки школы, журнал действий, заметки админа.
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
            # 1. classes: is_archived
            await cursor.execute("""
                SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'classes' AND COLUMN_NAME = 'is_archived'
            """)
            if not await cursor.fetchone():
                await cursor.execute("ALTER TABLE classes ADD COLUMN is_archived BOOLEAN DEFAULT FALSE")
                await conn.commit()
                print("✅ classes.is_archived добавлена")

            # 2. schools: email, phone
            for col in ['email', 'phone']:
                await cursor.execute(f"""
                    SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS 
                    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'schools' AND COLUMN_NAME = '{col}'
                """)
                if not await cursor.fetchone():
                    await cursor.execute(f"ALTER TABLE schools ADD COLUMN {col} VARCHAR(255) NULL")
                    await conn.commit()
                    print(f"✅ schools.{col} добавлена")

            # 3. audit_log
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    school_id INT NULL,
                    user_id INT NULL,
                    action VARCHAR(100) NOT NULL,
                    entity_type VARCHAR(50) NULL,
                    entity_id INT NULL,
                    details JSON NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (school_id) REFERENCES schools(id) ON DELETE SET NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
                    INDEX idx_school_created (school_id, created_at),
                    INDEX idx_user_created (user_id, created_at)
                )
            """)
            await conn.commit()
            print("✅ Таблица audit_log создана")

            # 4. admin_notes (быстрые заметки на главной)
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS admin_notes (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    school_id INT NOT NULL,
                    user_id INT NOT NULL,
                    content TEXT NOT NULL,
                    is_pinned BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (school_id) REFERENCES schools(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    INDEX idx_school (school_id)
                )
            """)
            await conn.commit()
            print("✅ Таблица admin_notes создана")

    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
