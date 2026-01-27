#!/usr/bin/env python3
"""
Скрипт для исправления кодировки и настройки коэффициентов предметов
"""

import asyncio
import sys
import os

# Добавляем путь к модулям приложения
sys.path.append(os.path.join(os.path.dirname(__file__)))

from app.db.connection import get_db_connection


async def fix_encoding_and_coefficients():
    """Исправляем кодировку и настраиваем коэффициенты предметов"""
    print("🔧 Исправление кодировки и настройка коэффициентов предметов\n")
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Исправляем кодировку названий предметов
            print("1. Исправление кодировки названий предметов:")
            
            # Словарь для исправления кодировки
            encoding_fixes = {
                'Ð¤Ð¸Ð·Ð¸ÐºÐ°': 'Физика',
                'ÐœÐ°Ñ‚ÐµÐ¼Ð°Ñ‚Ð¸ÐºÐ°': 'Математика'
            }
            
            for wrong_name, correct_name in encoding_fixes.items():
                await cursor.execute(
                    "UPDATE subjects SET name = %s WHERE name = %s",
                    (correct_name, wrong_name)
                )
                print(f"   Исправлено: '{wrong_name}' → '{correct_name}'")
            
            # Настраиваем коэффициенты предметов
            print("\n2. Настройка коэффициентов предметов:")
            
            coefficients = {
                'Математика': 1.5,
                'Физика': 1.5,
                'Английский язык': 1.4,
                'Информатика': 1.3,
                'Химия': 1.3,
                'Русский язык': 1.2,
                'Биология': 1.1,
                'История': 1.0,
                'География': 1.0,
                'Физкультура': 0.8,
                'Литература': 1.1,
                'Обществознание': 1.0,
                'ОБЖ': 0.9,
                'Музыка': 0.7,
                'ИЗО': 0.7,
                'Технология': 0.8
            }
            
            for subject_name, coefficient in coefficients.items():
                await cursor.execute(
                    "UPDATE subjects SET coefficient = %s WHERE name = %s",
                    (coefficient, subject_name)
                )
                print(f"   {subject_name}: коэффициент {coefficient}")
            
            await conn.commit()
            print("\n✅ Исправления применены успешно!")
            
            # Проверяем результат
            print("\n3. Проверка результата:")
            await cursor.execute("SELECT name, coefficient FROM subjects ORDER BY name")
            subjects = await cursor.fetchall()
            
            for subject in subjects:
                print(f"   {subject['name']}: коэффициент {subject['coefficient']}")
            
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(fix_encoding_and_coefficients())
