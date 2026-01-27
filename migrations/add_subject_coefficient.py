#!/usr/bin/env python3
"""
Миграция для добавления поля coefficient в таблицу subjects
"""

import asyncio
import sys
import os

# Добавляем путь к модулям приложения
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from app.db.connection import get_db_connection


async def add_subject_coefficient():
    """Добавляет поле coefficient в таблицу subjects"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем, существует ли уже поле coefficient
            await cursor.execute("""
                SELECT COLUMN_NAME 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_SCHEMA = DATABASE() 
                AND TABLE_NAME = 'subjects' 
                AND COLUMN_NAME = 'coefficient'
            """)
            
            if not await cursor.fetchone():
                # Добавляем поле coefficient
                await cursor.execute("""
                    ALTER TABLE subjects 
                    ADD COLUMN coefficient DECIMAL(3,2) DEFAULT 1.00
                """)
                
                # Обновляем существующие записи, устанавливая коэффициент по умолчанию
                await cursor.execute("""
                    UPDATE subjects 
                    SET coefficient = 1.00 
                    WHERE coefficient IS NULL
                """)
                
                await conn.commit()
                print("✅ Поле coefficient успешно добавлено в таблицу subjects")
            else:
                print("ℹ️ Поле coefficient уже существует в таблице subjects")
                
    except Exception as e:
        print(f"❌ Ошибка при добавлении поля coefficient: {e}")
        await conn.rollback()
    finally:
        conn.close()


async def main():
    """Основная функция миграции"""
    print("🚀 Запуск миграции: добавление коэффициента для предметов...")
    await add_subject_coefficient()
    print("✅ Миграция завершена!")


if __name__ == "__main__":
    asyncio.run(main())
