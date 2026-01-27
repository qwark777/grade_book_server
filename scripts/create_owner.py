#!/usr/bin/env python3
"""
Скрипт для создания первого владельца (owner) в системе.
Используется при первом запуске сервера для создания учетной записи владельца.

Использование:
    python3 scripts/create_owner.py --username owner --password your_password
    python3 scripts/create_owner.py --username owner --password your_password --full-name "Имя Фамилия"
"""

import asyncio
import argparse
import sys
from pathlib import Path

# Добавляем корневую директорию проекта в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db.connection import get_db_connection
from app.core.security import get_password_hash


async def create_owner(username: str, password: str, full_name: str = None):
    """Создать первого владельца в системе"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем, есть ли уже владелец
            await cursor.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1")
            existing_owner = await cursor.fetchone()
            
            if existing_owner:
                print("⚠️  Владелец уже существует в системе!")
                print("   Если вы хотите создать нового владельца, удалите существующего из базы данных.")
                return False
            
            # Проверяем, существует ли пользователь с таким username
            await cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
            existing_user = await cursor.fetchone()
            
            if existing_user:
                print(f"❌ Пользователь с username '{username}' уже существует!")
                return False
            
            # Хешируем пароль
            hashed_password = get_password_hash(password)
            
            # Создаем пользователя с ролью owner
            try:
                await cursor.execute(
                    "INSERT INTO users (username, hashed_password, role) VALUES (%s, %s, 'owner')",
                    (username, hashed_password)
                )
                await conn.commit()
                
                # Получаем ID созданного пользователя
                await cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
                row = await cursor.fetchone()
                owner_id = row[0] if isinstance(row, (list, tuple)) else row.get("id")
                
                print(f"✅ Владелец успешно создан!")
                print(f"   Username: {username}")
                print(f"   ID: {owner_id}")
                print(f"   Role: owner")
                
                # Создаем профиль, если указано имя
                if full_name:
                    try:
                        await cursor.execute(
                            "INSERT INTO profiles (user_id, full_name) VALUES (%s, %s)",
                            (owner_id, full_name)
                        )
                        await conn.commit()
                        print(f"   Full Name: {full_name}")
                    except Exception as e:
                        print(f"⚠️  Не удалось создать профиль: {e}")
                        # Продолжаем, так как профиль не обязателен
                
                print("\n📝 Теперь вы можете войти в приложение с этими учетными данными:")
                print(f"   Username: {username}")
                print(f"   Password: {password}")
                print("\n⚠️  ВАЖНО: Сохраните эти данные в безопасном месте!")
                
                return True
                
            except Exception as e:
                print(f"❌ Ошибка при создании владельца: {e}")
                await conn.rollback()
                return False
                
    except Exception as e:
        print(f"❌ Ошибка подключения к базе данных: {e}")
        return False
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Создать первого владельца (owner) в системе",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  python3 scripts/create_owner.py --username owner --password mypassword
  python3 scripts/create_owner.py --username admin --password secure123 --full-name "Иван Иванов"
        """
    )
    
    parser.add_argument(
        "--username",
        required=True,
        help="Username для владельца"
    )
    
    parser.add_argument(
        "--password",
        required=True,
        help="Password для владельца"
    )
    
    parser.add_argument(
        "--full-name",
        default=None,
        help="Полное имя владельца (опционально)"
    )
    
    args = parser.parse_args()
    
    # Валидация
    if len(args.username) < 3:
        print("❌ Username должен содержать минимум 3 символа")
        sys.exit(1)
    
    if len(args.password) < 6:
        print("❌ Password должен содержать минимум 6 символов")
        sys.exit(1)
    
    # Запускаем создание владельца
    success = asyncio.run(create_owner(args.username, args.password, args.full_name))
    
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
