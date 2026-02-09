#!/usr/bin/env python3
"""
Заполняет class_subject_teachers из class_teachers + class_subjects.
Для каждой пары (class_id, teacher_id) в class_teachers добавляет записи
(class_id, subject_id, teacher_id) для всех предметов класса.
Запускать один раз при переходе на модель «учитель — предмет по классу».
"""
import asyncio
import aiomysql
import os
from dotenv import load_dotenv

load_dotenv()


async def main():
    conn = await aiomysql.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", 3306)),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        db=os.getenv("MYSQL_DB", "grade_book"),
        charset="utf8mb4",
    )
    try:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("""
                SELECT ct.class_id, ct.teacher_id, cs.subject_id
                FROM class_teachers ct
                JOIN class_subjects cs ON cs.class_id = ct.class_id
                WHERE NOT EXISTS (
                    SELECT 1 FROM class_subject_teachers cst
                    WHERE cst.class_id = ct.class_id
                    AND cst.teacher_id = ct.teacher_id
                    AND cst.subject_id = cs.subject_id
                )
            """)
            rows = await cursor.fetchall()
            if not rows:
                print("Нет новых записей для добавления.")
                return
            inserted = 0
            for r in rows:
                await cursor.execute(
                    """INSERT IGNORE INTO class_subject_teachers (class_id, subject_id, teacher_id)
                       VALUES (%s, %s, %s)""",
                    (r["class_id"], r["subject_id"], r["teacher_id"]),
                )
                inserted += cursor.rowcount
            await conn.commit()
            print(f"Добавлено записей в class_subject_teachers: {inserted}")
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
