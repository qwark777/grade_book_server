#!/usr/bin/env python3
"""
Тестовый скрипт для проверки системы коэффициентов предметов
"""

import asyncio
import sys
import os

# Добавляем путь к модулям приложения
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

from app.utils.points_calculator import PointsCalculator


async def test_coefficients():
    """Тестирование системы коэффициентов"""
    print("🧪 Тестирование системы коэффициентов предметов\n")
    
    # Тест 1: Базовые баллы
    print("1. Тестирование базовых баллов:")
    for grade in [5, 4, 3, 2]:
        points = PointsCalculator.calculate_points_for_grade(grade)
        print(f"   Оценка {grade}: {points} баллов")
    
    print()
    
    # Тест 2: Расчет с коэффициентами
    print("2. Тестирование расчета с коэффициентами:")
    test_cases = [
        (5, 1.5, "Математика"),
        (4, 1.0, "История"),
        (3, 0.8, "Физкультура"),
        (2, 1.2, "Русский язык")
    ]
    
    for grade, coefficient, subject in test_cases:
        points = PointsCalculator.calculate_points_for_grade(grade, coefficient)
        base_points = PointsCalculator.BASE_POINTS[grade]
        print(f"   {subject} (коэф. {coefficient}): {grade} → {base_points} × {coefficient} = {points} баллов")
    
    print()
    
    # Тест 3: Таблица баллов
    print("3. Таблица баллов:")
    points_table = PointsCalculator.get_points_table()
    for grade, data in points_table.items():
        print(f"   {grade} ({data['description']}): {data['base_points']} баллов")
    
    print()
    
    # Тест 4: Примеры для разных предметов
    print("4. Примеры расчета для разных предметов:")
    subjects = [
        ("Математика", 1.5),
        ("Физика", 1.5),
        ("Английский язык", 1.4),
        ("Информатика", 1.3),
        ("Химия", 1.3),
        ("Русский язык", 1.2),
        ("Биология", 1.1),
        ("История", 1.0),
        ("География", 1.0),
        ("Физкультура", 0.8)
    ]
    
    for subject, coefficient in subjects:
        print(f"   {subject} (коэф. {coefficient}):")
        for grade in [5, 4, 3, 2]:
            points = PointsCalculator.calculate_points_for_grade(grade, coefficient)
            base_points = PointsCalculator.BASE_POINTS[grade]
            print(f"     {grade}: {base_points} × {coefficient} = {points}")
        print()
    
    print("✅ Тестирование завершено!")


if __name__ == "__main__":
    asyncio.run(test_coefficients())
