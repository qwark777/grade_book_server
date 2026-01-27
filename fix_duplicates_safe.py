#!/usr/bin/env python3
"""
Безопасный скрипт для удаления дубликатов и настройки коэффициентов предметов
"""

import asyncio
import sys
import os

# Добавляем путь к модулям приложения
sys.path.append(os.path.join(os.path.dirname(__file__)))

from app.db.connection import get_db_connection


async def fix_duplicates_safe():
    """Безопасно удаляем дубликаты и настраиваем коэффициенты предметов"""
    print("🔧 Безопасное удаление дубликатов и настройка коэффициентов предметов\n")
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Удаляем связанные записи для дубликатов
            print("1. Удаление связанных записей для дубликатов:")
            
            # Удаляем записи из class_subject_teachers
            await cursor.execute("DELETE FROM class_subject_teachers WHERE subject_id IN (19, 20)")
            print("   Удалены записи из class_subject_teachers")
            
            # Удаляем записи из teacher_subjects
            await cursor.execute("DELETE FROM teacher_subjects WHERE subject_id IN (19, 20)")
            print("   Удалены записи из teacher_subjects")
            
            # Удаляем записи из class_subjects
            await cursor.execute("DELETE FROM class_subjects WHERE subject_id IN (19, 20)")
            print("   Удалены записи из class_subjects")
            
            # Теперь удаляем дубликаты предметов
            await cursor.execute("DELETE FROM subjects WHERE id IN (19, 20)")
            print("   Удалены дубликаты предметов")
            
            # Настраиваем коэффициенты предметов
            print("\n2. Настройка коэффициентов предметов:")
            
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
            print("\n✅ Исправления применены успешно!")
            
            # Проверяем результат
            print("\n3. Проверка результата:")
            await cursor.execute("SELECT id, name, coefficient FROM subjects ORDER BY id")
            subjects = await cursor.fetchall()
            
            for subject in subjects:
                print(f"   ID {subject['id']}: {subject['name']} - коэффициент {subject['coefficient']}")
            
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(fix_duplicates_safe())
