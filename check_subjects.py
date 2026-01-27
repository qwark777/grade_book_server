#!/usr/bin/env python3
"""
Скрипт для проверки предметов в базе данных
"""

import asyncio
import sys
import os

# Добавляем путь к модулям приложения
sys.path.append(os.path.join(os.path.dirname(__file__)))

from app.db.connection import get_db_connection


async def check_subjects():
    """Проверяем предметы в базе данных"""
    print("🔍 Проверка предметов в базе данных\n")
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT id, name, coefficient FROM subjects ORDER BY name")
            subjects = await cursor.fetchall()
            
            print("Предметы в базе данных:")
            for subject in subjects:
                print(f"   ID: {subject['id']}, Название: '{subject['name']}', Коэффициент: {subject['coefficient']}")
            
            print(f"\nВсего предметов: {len(subjects)}")
            
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(check_subjects())
