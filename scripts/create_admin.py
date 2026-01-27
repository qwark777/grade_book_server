#!/usr/bin/env python3
"""
Скрипт для создания администратора (admin) в системе.
Используется после создания владельца для создания первого администратора.

Использование:
    python3 scripts/create_admin.py --username admin --password your_password --school-id 1
"""

import asyncio
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db.connection import get_db_connection
from app.core.security import get_password_hash


async def create_admin(username: str, password: str, school_id: int, full_name: str = None):
    """Создать администратора в системе"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем, существует ли пользователь с таким username
            await cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
            existing_user = await cursor.fetchone()
            
            if existing_user:
                print(f"❌ Пользователь с username '{username}' уже существует!")
                return False
            
            # Хешируем пароль
            hashed_password = get_password_hash(password)
            
            # Создаем пользователя с ролью admin
            try:
                await cursor.execute(
                    "INSERT INTO users (username, hashed_password, role) VALUES (%s, %s, 'admin')",
                    (username, hashed_password)
                )
                await conn.commit()
                
                # Получаем ID созданного пользователя
                await cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
                row = await cursor.fetchone()
                admin_id = row[0] if isinstance(row, (list, tuple)) else row.get("id")
                
                print(f"✅ Администратор успешно создан!")
                print(f"   Username: {username}")
                print(f"   ID: {admin_id}")
                print(f"   Role: admin")
                
                # Связываем админа со школой
                try:
                    await cursor.execute(
                        "INSERT IGNORE INTO school_admins (school_id, admin_user_id) VALUES (%s, %s)",
                        (school_id, admin_id)
                    )
                    await conn.commit()
                    print(f"   School ID: {school_id}")
                except Exception as e:
                    print(f"⚠️  Не удалось связать админа со школой: {e}")
                
                # Создаем профиль, если указано имя
                if full_name:
                    try:
                        await cursor.execute(
                            "INSERT INTO profiles (user_id, full_name) VALUES (%s, %s)",
                            (admin_id, full_name)
                        )
                        await conn.commit()
                        print(f"   Full Name: {full_name}")
                    except Exception as e:
                        print(f"⚠️  Не удалось создать профиль: {e}")
                
                print("\n📝 Теперь вы можете войти в приложение с этими учетными данными:")
                print(f"   Username: {username}")
                print(f"   Password: {password}")
                print("\n⚠️  ВАЖНО: Сохраните эти данные в безопасном месте!")
                
                return True
                
            except Exception as e:
                print(f"❌ Ошибка при создании администратора: {e}")
                await conn.rollback()
                return False
                
    except Exception as e:
        print(f"❌ Ошибка подключения к базе данных: {e}")
        return False
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Создать администратора (admin) в системе",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  python3 scripts/create_admin.py --username admin --password mypassword --school-id 1
  python3 scripts/create_admin.py --username admin --password secure123 --school-id 1 --full-name "Иван Иванов"
        """
    )
    
    parser.add_argument(
        "--username",
        required=True,
        help="Username для администратора"
    )
    
    parser.add_argument(
        "--password",
        required=True,
        help="Password для администратора"
    )
    
    parser.add_argument(
        "--school-id",
        type=int,
        required=True,
        help="ID школы для привязки администратора"
    )
    
    parser.add_argument(
        "--full-name",
        default=None,
        help="Полное имя администратора (опционально)"
    )
    
    args = parser.parse_args()
    
    # Валидация
    if len(args.username) < 3:
        print("❌ Username должен содержать минимум 3 символа")
        sys.exit(1)
    
    if len(args.password) < 6:
        print("❌ Password должен содержать минимум 6 символов")
        sys.exit(1)
    
    # Запускаем создание администратора
    success = asyncio.run(create_admin(args.username, args.password, args.school_id, args.full_name))
    
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
