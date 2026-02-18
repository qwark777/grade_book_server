#!/usr/bin/env python3
"""Миграция: роль родитель и привязка к ученикам."""

import asyncio
import aiomysql
from app.core.config import settings


async def add_parent_students():
    conn = await aiomysql.connect(
        host=settings.MYSQL_HOST,
        port=settings.MYSQL_PORT,
        user=settings.MYSQL_USER,
        password=settings.MYSQL_PASSWORD,
        db=settings.MYSQL_DB,
        cursorclass=aiomysql.DictCursor,
        charset="utf8mb4",
        use_unicode=True,
    )

    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS parent_students (
                    parent_id INT NOT NULL,
                    student_id INT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (parent_id, student_id),
                    FOREIGN KEY (parent_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)
            await conn.commit()
        print("parent_students table created")
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(add_parent_students())
