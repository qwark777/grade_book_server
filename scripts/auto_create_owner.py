#!/usr/bin/env python3
"""
Автоматическое создание владельца при первом запуске через Docker.
Использует переменные окружения OWNER_USERNAME и OWNER_PASSWORD.
"""

import asyncio
import os
import sys
from pathlib import Path

# Добавляем корневую директорию проекта в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db.connection import get_db_connection
from app.core.security import get_password_hash


async def auto_create_owner():
    """Автоматически создать владельца из переменных окружения"""
    username = os.getenv("OWNER_USERNAME", "owner")
    password = os.getenv("OWNER_PASSWORD")
    full_name = os.getenv("OWNER_FULL_NAME")
    
    if not password:
        print("⚠️  OWNER_PASSWORD не установлен. Пропускаем создание владельца.")
        print("   Вы можете создать владельца вручную:")
        print("   docker-compose exec app python scripts/create_owner.py --username owner --password your_password")
        return False
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем, есть ли уже владелец
            await cursor.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1")
            existing_owner = await cursor.fetchone()
            
            if existing_owner:
                print("✅ Владелец уже существует в системе.")
                return True
            
            # Проверяем, существует ли пользователь с таким username
            await cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
            existing_user = await cursor.fetchone()
            
            if existing_user:
                print(f"⚠️  Пользователь с username '{username}' уже существует.")
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
                
                print(f"✅ Владелец успешно создан автоматически!")
                print(f"   Username: {username}")
                print(f"   ID: {owner_id}")
                
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


if __name__ == "__main__":
    asyncio.run(auto_create_owner())
