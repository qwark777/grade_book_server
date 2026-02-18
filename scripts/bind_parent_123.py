#!/usr/bin/env python3
"""Привязать пару учеников к родителю (username 123, id=1)."""

import asyncio
import aiomysql
from app.core.config import settings


async def main():
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
            # parent_id=1 (username 123), student_id 2 и 9
            await cursor.executemany(
                "INSERT IGNORE INTO parent_students (parent_id, student_id) VALUES (%s, %s)",
                [(1, 2), (1, 9)],
            )
            await conn.commit()
        print("Done: bound students 2, 9 to parent 1 (username 123)")
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
