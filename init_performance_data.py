#!/usr/bin/env python3
"""
Скрипт для инициализации данных успеваемости
Запускает создание таблиц и заполнение тестовыми данными
"""

import asyncio
import sys
import os

# Добавляем путь к модулям приложения
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

from app.db.seed_attendance_data import seed_attendance_data
from app.db.seed_data import seed_timetable_data
from seed_performance_data import seed_performance_data


async def main():
    """Основная функция инициализации"""
    print("🚀 Инициализация данных успеваемости...")
    
    try:
        # Создаем таблицы и заполняем тестовыми данными расписания
        print("📅 Создание данных расписания...")
        await seed_timetable_data()
        
        # Создаем таблицы и заполняем тестовыми данными посещаемости
        print("📊 Создание данных посещаемости...")
        await seed_attendance_data()
        
        # Заполняем демо-данными для графиков (оценки и тренды)
        print("📈 Создание демо-данных для графиков...")
        await seed_performance_data()
        
        print("✅ Инициализация завершена успешно!")
        print("\n📋 Что было создано:")
        print("  - Таблица attendance с данными посещаемости")
        print("  - Таблица grades с оценками за последние 60 дней")
        print("  - API эндпоинты для получения данных успеваемости")
        print("  - Тестовые данные за последние 30-60 дней")
        print("\n🔗 Доступные API эндпоинты:")
        print("  - GET /attendance-stats/{student_id}")
        print("  - GET /attendance-heatmap/{student_id}")
        print("  - GET /grade-distribution/{student_id}")
        print("  - GET /performance-trends/{student_id}")
        print("  - GET /subject-performance/{student_id}/{subject_id}")
        
    except Exception as e:
        print(f"❌ Ошибка при инициализации: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
