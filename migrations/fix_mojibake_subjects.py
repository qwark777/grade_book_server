#!/usr/bin/env python3
"""Merge subject rows that were stored with mojibake names."""

import asyncio

from app.db.connection import get_db_connection


SUBJECT_RENAMES = {
    "ÐœÐ°Ñ‚ÐµÐ¼Ð°Ñ‚Ð¸ÐºÐ°": "Математика",
    "Ð¤Ð¸Ð·Ð¸ÐºÐ°": "Физика",
}

SIMPLE_SUBJECT_REFS = (
    "grades",
    "attendance",
    "homeworks",
    "timetable_templates",
)


async def fix_mojibake_subjects() -> None:
    conn = await get_db_connection()
    changed: list[str] = []
    try:
        async with conn.cursor() as cursor:
            for broken_name, fixed_name in SUBJECT_RENAMES.items():
                await cursor.execute(
                    "SELECT id FROM subjects WHERE name = %s",
                    (broken_name,),
                )
                broken_row = await cursor.fetchone()
                if not broken_row:
                    continue

                broken_id = broken_row["id"]
                await cursor.execute(
                    "SELECT id FROM subjects WHERE name = %s",
                    (fixed_name,),
                )
                fixed_row = await cursor.fetchone()

                if not fixed_row:
                    await cursor.execute(
                        "UPDATE subjects SET name = %s WHERE id = %s",
                        (fixed_name, broken_id),
                    )
                    changed.append(f"renamed {broken_name} -> {fixed_name}")
                    continue

                fixed_id = fixed_row["id"]

                await cursor.execute(
                    """
                    DELETE cs_broken FROM class_subjects cs_broken
                    JOIN class_subjects cs_fixed
                      ON cs_fixed.class_id = cs_broken.class_id
                     AND cs_fixed.subject_id = %s
                    WHERE cs_broken.subject_id = %s
                    """,
                    (fixed_id, broken_id),
                )
                await cursor.execute(
                    "UPDATE class_subjects SET subject_id = %s WHERE subject_id = %s",
                    (fixed_id, broken_id),
                )

                await cursor.execute(
                    """
                    DELETE ts_broken FROM teacher_subjects ts_broken
                    JOIN teacher_subjects ts_fixed
                      ON ts_fixed.teacher_id = ts_broken.teacher_id
                     AND ts_fixed.subject_id = %s
                    WHERE ts_broken.subject_id = %s
                    """,
                    (fixed_id, broken_id),
                )
                await cursor.execute(
                    "UPDATE teacher_subjects SET subject_id = %s WHERE subject_id = %s",
                    (fixed_id, broken_id),
                )

                await cursor.execute(
                    """
                    DELETE cst_broken FROM class_subject_teachers cst_broken
                    JOIN class_subject_teachers cst_fixed
                      ON cst_fixed.class_id = cst_broken.class_id
                     AND cst_fixed.teacher_id = cst_broken.teacher_id
                     AND cst_fixed.subject_id = %s
                    WHERE cst_broken.subject_id = %s
                    """,
                    (fixed_id, broken_id),
                )
                await cursor.execute(
                    """
                    UPDATE class_subject_teachers
                    SET subject_id = %s
                    WHERE subject_id = %s
                    """,
                    (fixed_id, broken_id),
                )

                for table_name in SIMPLE_SUBJECT_REFS:
                    await cursor.execute(
                        f"UPDATE `{table_name}` SET subject_id = %s WHERE subject_id = %s",
                        (fixed_id, broken_id),
                    )

                await cursor.execute("DELETE FROM subjects WHERE id = %s", (broken_id,))
                changed.append(f"merged {broken_name} -> {fixed_name}")

            await conn.commit()
            print("\n".join(changed) if changed else "No mojibake subjects found")
    except Exception:
        await conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(fix_mojibake_subjects())
