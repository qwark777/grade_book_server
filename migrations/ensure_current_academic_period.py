#!/usr/bin/env python3
"""Ensure the current school year exists and is marked active."""

import asyncio
import os
import sys
from datetime import date

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.db.connection import close_db_pool, get_db_connection


def _current_school_year(today: date) -> tuple[str, str, str]:
    if today.month >= 9:
        start_year = today.year
    else:
        start_year = today.year - 1
    end_year = start_year + 1
    return (
        f"{start_year}-{end_year}",
        f"{start_year}-09-01",
        f"{end_year}-08-31",
    )


async def ensure_current_academic_period() -> None:
    name, start_date, end_date = _current_school_year(date.today())
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id
                FROM academic_periods
                WHERE start_date = %s AND end_date = %s
                LIMIT 1
                """,
                (start_date, end_date),
            )
            existing = await cursor.fetchone()

            await cursor.execute("UPDATE academic_periods SET is_active = FALSE")
            if existing:
                period_id = existing["id"]
                await cursor.execute(
                    """
                    UPDATE academic_periods
                    SET name = %s, is_active = TRUE
                    WHERE id = %s
                    """,
                    (name, period_id),
                )
                action = f"Activated existing academic period {name}"
            else:
                await cursor.execute(
                    """
                    INSERT INTO academic_periods (name, start_date, end_date, is_active)
                    VALUES (%s, %s, %s, TRUE)
                    """,
                    (name, start_date, end_date),
                )
                action = f"Created academic period {name}"

            await conn.commit()
            print(action)
    except Exception:
        await conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    async def main() -> None:
        try:
            await ensure_current_academic_period()
        finally:
            await close_db_pool()

    asyncio.run(main())
