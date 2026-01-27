#!/usr/bin/env python3
"""
Тест нового API эндпоинта
"""

import asyncio
import sys
import os

# Добавляем путь к модулям приложения
sys.path.append(os.path.join(os.path.dirname(__file__)))

from app.api.grades import get_students_points_leaderboard


async def test_new_api():
    """Тестируем новый API эндпоинт"""
    print("🧪 Тестирование нового API /students/points-leaderboard\n")
    
    try:
        # Тестируем новый API эндпоинт
        result = await get_students_points_leaderboard(None)  # Без аутентификации для теста
        print('✅ API работает!')
        print(f'Получено студентов: {len(result)}')
        
        print("\n🏆 Топ 5 студентов:")
        for i, student in enumerate(result[:5], 1):
            print(f'{i}. {student["student_name"]}: {student["total_points"]} баллов')
            
    except Exception as e:
        print(f'❌ Ошибка API: {e}')


if __name__ == "__main__":
    asyncio.run(test_new_api())
