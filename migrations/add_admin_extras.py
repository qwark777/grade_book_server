#!/usr/bin/env python3
"""Миграция: объявления, история входов для админ-функций."""

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
                CREATE TABLE IF NOT EXISTS announcements (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    school_id INT NULL,
                    title VARCHAR(255) NOT NULL,
                    content TEXT NOT NULL,
                    target_type VARCHAR(50) DEFAULT 'all',
                    target_class_id INT NULL,
                    created_by INT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_school (school_id),
                    INDEX idx_created (created_at)
                )
            """)
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS login_log (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    school_id INT NULL,
                    ip_address VARCHAR(45) NULL,
                    user_agent VARCHAR(500) NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_user (user_id),
                    INDEX idx_school (school_id),
                    INDEX idx_created (created_at)
                )
            """)
            await conn.commit()
            print("✅ Таблицы announcements и login_log созданы")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
