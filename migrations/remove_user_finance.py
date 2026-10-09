#!/usr/bin/env python3
"""Remove legacy per-user finance tables.

The product billing model is school subscriptions only. This migration removes
old wallet tables and the optional lesson_enrollments.transaction_id link if
they exist in an already deployed database.
"""

import asyncio

from app.db.connection import get_db_connection


async def _table_exists(cursor, table_name: str) -> bool:
    await cursor.execute(
        """
        SELECT COUNT(*) AS count
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
        """,
        (table_name,),
    )
    row = await cursor.fetchone()
    return bool(row and row.get("count", 0) > 0)


async def _column_exists(cursor, table_name: str, column_name: str) -> bool:
    await cursor.execute(
        """
        SELECT COUNT(*) AS count
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
        """,
        (table_name, column_name),
    )
    row = await cursor.fetchone()
    return bool(row and row.get("count", 0) > 0)


async def _foreign_keys(cursor, table_name: str, column_name: str) -> list[str]:
    await cursor.execute(
        """
        SELECT CONSTRAINT_NAME
        FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
          AND REFERENCED_TABLE_NAME IS NOT NULL
        """,
        (table_name, column_name),
    )
    rows = await cursor.fetchall()
    return [row["CONSTRAINT_NAME"] for row in rows]


async def remove_user_finance() -> None:
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            if await _column_exists(cursor, "lesson_enrollments", "transaction_id"):
                for fk_name in await _foreign_keys(
                    cursor,
                    "lesson_enrollments",
                    "transaction_id",
                ):
                    await cursor.execute(
                        f"ALTER TABLE lesson_enrollments DROP FOREIGN KEY `{fk_name}`"
                    )
                await cursor.execute(
                    "ALTER TABLE lesson_enrollments DROP COLUMN transaction_id"
                )

            for table_name in (
                "balance_transactions",
                "user_balance",
                "user_subscriptions",
            ):
                if await _table_exists(cursor, table_name):
                    await cursor.execute(f"DROP TABLE `{table_name}`")

            await conn.commit()
            print("Legacy per-user finance tables removed")
    except Exception:
        await conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(remove_user_finance())
