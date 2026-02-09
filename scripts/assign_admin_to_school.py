#!/usr/bin/env python3
"""
Привязать админа (по логину) к школе, где есть люди (классы/ученики/учителя).

Использование:
    python3 scripts/assign_admin_to_school.py 543
    python3 scripts/assign_admin_to_school.py --username 543
"""

import asyncio
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db.connection import get_db_connection


async def assign_admin_to_school(username: str, school_id: int = None) -> bool:
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Найти пользователя по username
            await cursor.execute(
                "SELECT id, role FROM users WHERE username = %s",
                (username,)
            )
            row = await cursor.fetchone()
            if not row:
                print(f"❌ Пользователь с логином '{username}' не найден")
                return False

            admin_id = row["id"] if isinstance(row, dict) else row[0]
            role = row["role"] if isinstance(row, dict) else row[1]

            # Убедиться что роль admin
            if role != "admin":
                await cursor.execute(
                    "UPDATE users SET role = 'admin' WHERE id = %s",
                    (admin_id,)
                )
                await conn.commit()
                print(f"   Роль обновлена на 'admin'")

            # Школа: задана явно или ищем с людьми
            if school_id is not None:
                await cursor.execute("SELECT id, name FROM schools WHERE id = %s", (school_id,))
                school_row = await cursor.fetchone()
                if not school_row:
                    print(f"❌ Школа с id={school_id} не найдена")
                    return False
                school_name = school_row.get("name", "Школа") if isinstance(school_row, dict) else (school_row[1] if len(school_row) > 1 else "Школа")
            else:
                # Найти школу с людьми
                await cursor.execute("""
                    SELECT COUNT(*) as n FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'classes' AND COLUMN_NAME = 'school_id'
                """)
                has_school_id = (await cursor.fetchone() or {}).get("n", 0) > 0
                if has_school_id:
                    await cursor.execute("""
                        SELECT s.id, s.name FROM schools s
                        JOIN classes c ON c.school_id = s.id
                        WHERE EXISTS (SELECT 1 FROM class_students cs WHERE cs.class_id = c.id)
                           OR EXISTS (SELECT 1 FROM class_teachers ct WHERE ct.class_id = c.id)
                        LIMIT 1
                    """)
                else:
                    await cursor.execute("SELECT id, name FROM schools LIMIT 1")
                school_row = await cursor.fetchone()
                if not school_row and has_school_id:
                    await cursor.execute("SELECT id, name FROM schools LIMIT 1")
                    school_row = await cursor.fetchone()
                if not school_row:
                    print("❌ Не найдено школы")
                    return False
                school_id = school_row["id"] if isinstance(school_row, dict) else school_row[0]
                school_name = school_row.get("name", "Школа") if isinstance(school_row, dict) else (school_row[1] if len(school_row) > 1 else "Школа")

            # Удалить старые привязки этого админа (один админ — одна школа)
            await cursor.execute("DELETE FROM school_admins WHERE admin_user_id = %s", (admin_id,))

            # Проверить, не привязан ли уже к этой школе
            await cursor.execute(
                "SELECT 1 FROM school_admins WHERE admin_user_id = %s AND school_id = %s",
                (admin_id, school_id)
            )
            if await cursor.fetchone():
                print(f"✅ Админ '{username}' уже привязан к школе '{school_name}' (id={school_id})")
                return True

            await cursor.execute(
                "INSERT INTO school_admins (school_id, admin_user_id) VALUES (%s, %s)",
                (school_id, admin_id)
            )
            await conn.commit()
            print(f"✅ Админ '{username}' (id={admin_id}) привязан к школе '{school_name}' (id={school_id})")
            return True
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Привязать админа к школе")
    parser.add_argument("username", nargs="?", help="Логин админа (например 543)")
    parser.add_argument("--school-id", "-s", type=int, default=None, help="ID школы (например 124)")
    args = parser.parse_args()

    username = args.username
    if not username:
        print("Укажите логин: python3 scripts/assign_admin_to_school.py 543 --school-id 124")
        sys.exit(1)

    success = asyncio.run(assign_admin_to_school(str(username), args.school_id))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
