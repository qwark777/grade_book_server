#!/usr/bin/env python3
"""
Скрипт для настройки коэффициентов предметов
"""

import asyncio
import sys
import os

# Добавляем путь к модулям приложения
sys.path.append(os.path.join(os.path.dirname(__file__)))

from app.db.connection import get_db_connection


async def set_coefficients_only():
    """Настраиваем коэффициенты предметов"""
    print("🔧 Настройка коэффициентов предметов\n")
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Настраиваем коэффициенты предметов
            print("Настройка коэффициентов предметов:")
            
            coefficients = {
                1: 1.5,   # Математика
                2: 1.2,   # Русский язык
                3: 1.1,   # Литература
                4: 1.4,   # Английский язык
                5: 1.3,   # Информатика
                6: 1.5,   # Физика
                7: 1.3,   # Химия
                8: 1.1,   # Биология
                9: 1.0,   # География
                10: 1.0,  # История
                11: 1.0,  # Обществознание
                12: 0.9,  # ОБЖ
                13: 0.8,  # Физкультура
                14: 0.7,  # ИЗО
                15: 0.7,  # Музыка
                16: 0.8   # Технология
            }
            
            for subject_id, coefficient in coefficients.items():
                await cursor.execute(
                    "UPDATE subjects SET coefficient = %s WHERE id = %s",
                    (coefficient, subject_id)
                )
                print(f"   ID {subject_id}: коэффициент {coefficient}")
            
            await conn.commit()
            print("\n✅ Коэффициенты настроены успешно!")
            
            # Проверяем результат
            print("\nПроверка результата:")
            await cursor.execute("SELECT id, name, coefficient FROM subjects ORDER BY id")
            subjects = await cursor.fetchall()
            
            for subject in subjects:
                print(f"   ID {subject['id']}: {subject['name']} - коэффициент {subject['coefficient']}")
            
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(set_coefficients_only())
