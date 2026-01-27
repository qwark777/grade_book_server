#!/usr/bin/env python3
"""
Тест API эндпоинта для предметов с коэффициентами
"""

import asyncio
import sys
import os

# Добавляем путь к модулям приложения
sys.path.append(os.path.join(os.path.dirname(__file__)))

from app.api.grades import list_subjects_with_coefficients


async def test_subjects_api():
    """Тестируем API эндпоинт для предметов с коэффициентами"""
    print("🧪 Тестирование API /subjects-with-coefficients\n")
    
    try:
        result = await list_subjects_with_coefficients(None)
        print('✅ API /subjects-with-coefficients работает!')
        print(f'Получено предметов: {len(result)}')
        
        print("\n📚 Список предметов:")
        for subject in result:
            print(f'  - {subject.name}: коэффициент {subject.coefficient}')
            
    except Exception as e:
        print(f'❌ Ошибка API: {e}')


if __name__ == "__main__":
    asyncio.run(test_subjects_api())
