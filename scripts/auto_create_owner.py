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
    
    print(f"🔍 Попытка создать владельца...")
    print(f"   Username: {username}")
    print(f"   Password: {'*' * len(password) if password else 'НЕ УСТАНОВЛЕН'}")
    print(f"   Full Name: {full_name or 'Не указано'}")
    print("")
    
    if not password:
        print("❌ OWNER_PASSWORD не установлен в переменных окружения!")
        print("   Пропускаем создание владельца.")
        print("")
        print("   Решение:")
        print("   1. Создай файл .env в корне проекта")
        print("   2. Добавь строку: OWNER_PASSWORD=твой_пароль")
        print("   3. Перезапусти: docker-compose restart app")
        print("")
        print("   Или создай владельца вручную:")
        print("   docker-compose exec app python scripts/create_owner.py --username owner --password your_password")
        return False
    
    try:
        print("🔌 Подключение к базе данных...")
        conn = await get_db_connection()
        print("✅ Подключение установлено")
        
        async with conn.cursor() as cursor:
            # Проверяем, есть ли уже владелец
            print("🔍 Проверка существующих владельцев...")
            await cursor.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1")
            existing_owner = await cursor.fetchone()
            
            if existing_owner:
                print("✅ Владелец уже существует в системе.")
                print("   Пропускаем создание.")
                return True
            
            # Проверяем, существует ли пользователь с таким username
            await cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
            existing_user = await cursor.fetchone()
            
            if existing_user:
                print(f"⚠️  Пользователь с username '{username}' уже существует.")
                return False
            
            # Хешируем пароль
            print("🔐 Хеширование пароля...")
            hashed_password = get_password_hash(password)
            
            # Создаем пользователя с ролью owner
            try:
                print("📝 Создание пользователя в базе данных...")
                await cursor.execute(
                    "INSERT INTO users (username, hashed_password, role) VALUES (%s, %s, 'owner')",
                    (username, hashed_password)
                )
                await conn.commit()
                print("✅ Пользователь создан в базе данных")
                
                # Получаем ID созданного пользователя
                await cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
                row = await cursor.fetchone()
                owner_id = row[0] if isinstance(row, (list, tuple)) else row.get("id")
                
                print("")
                print("=" * 60)
                print("✅ ВЛАДЕЛЕЦ УСПЕШНО СОЗДАН АВТОМАТИЧЕСКИ!")
                print("=" * 60)
                print(f"   Username: {username}")
                print(f"   ID: {owner_id}")
                print(f"   Role: owner")
                
                # Создаем профиль, если указано имя
                if full_name:
                    try:
                        print("📝 Создание профиля...")
                        await cursor.execute(
                            "INSERT INTO profiles (user_id, full_name) VALUES (%s, %s)",
                            (owner_id, full_name)
                        )
                        await conn.commit()
                        print(f"✅ Профиль создан: {full_name}")
                    except Exception as e:
                        print(f"⚠️  Не удалось создать профиль: {e}")
                
                print("")
                print("📝 Учетные данные для входа:")
                print(f"   Username: {username}")
                print(f"   Password: {password}")
                print("")
                print("⚠️  ВАЖНО: Сохраните эти данные в безопасном месте!")
                print("=" * 60)
                print("")
                
                return True
                
            except Exception as e:
                print("")
                print("=" * 60)
                print(f"❌ ОШИБКА ПРИ СОЗДАНИИ ВЛАДЕЛЬЦА")
                print("=" * 60)
                print(f"   {type(e).__name__}: {e}")
                print("")
                import traceback
                print("Детали ошибки:")
                traceback.print_exc()
                print("")
                print("Решение:")
                print("   1. Проверь логи базы данных")
                print("   2. Убедись, что таблица users существует")
                print("   3. Создай владельца вручную:")
                print("      docker-compose exec app python scripts/create_owner.py --username owner --password your_password")
                print("=" * 60)
                await conn.rollback()
                return False
                
    except Exception as e:
        print("")
        print("=" * 60)
        print(f"❌ ОШИБКА ПОДКЛЮЧЕНИЯ К БАЗЕ ДАННЫХ")
        print("=" * 60)
        print(f"   {type(e).__name__}: {e}")
        print("")
        print("Возможные причины:")
        print("   1. База данных еще не готова (подожди несколько секунд)")
        print("   2. Неверные учетные данные в переменных окружения")
        print("   3. База данных не запущена")
        print("")
        print("Решение:")
        print("   1. Проверь статус: docker-compose ps")
        print("   2. Проверь логи БД: docker-compose logs db")
        print("   3. Подожди и перезапусти: docker-compose restart app")
        print("=" * 60)
        return False
    finally:
        if 'conn' in locals():
            conn.close()


if __name__ == "__main__":
    asyncio.run(auto_create_owner())
