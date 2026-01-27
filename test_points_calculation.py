#!/usr/bin/env python3
"""
Тестовый скрипт для проверки расчета баллов на основе реальных оценок
"""

import asyncio
import sys
import os

# Добавляем путь к модулям приложения
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

from app.utils.points_calculator import PointsCalculator


async def test_real_grades_calculation():
    """Тестирование расчета баллов на основе реальных оценок из логов"""
    print("🧪 Тестирование расчета баллов на основе реальных оценок\n")
    
    # Данные из логов
    students_data = [
        {
            "student_id": 9,
            "student_name": "Петров Петр Петрович",
            "scores": [4, 2, 5, 4, 5],
            "average_score": 4.0
        },
        {
            "student_id": 2,
            "student_name": "Иванов Иван Иванович",
            "scores": [3, 5, 2, 5, 3],
            "average_score": 3.6
        },
        {
            "student_id": 10,
            "student_name": "Сидоров Сидор Сидорович",
            "scores": [4, 2, 4, 2],
            "average_score": 3.0
        },
        {
            "student_id": 13,
            "student_name": "Медведев Медведь Медведевич",
            "scores": [3, 2, 2, 5],
            "average_score": 3.0
        },
        {
            "student_id": 12,
            "student_name": "Волков Волк Волкович",
            "scores": [3, 2, 2, 3, 4],
            "average_score": 2.8
        },
        {
            "student_id": 11,
            "student_name": "Козлов Козел Козлович",
            "scores": [4, 3, 2, 2],
            "average_score": 2.75
        }
    ]
    
    # Предполагаемые предметы (нужно будет уточнить реальные)
    subjects = ["Математика", "Физика", "Русский язык", "История", "Английский язык"]
    
    print("Расчет баллов для каждого студента:")
    print("=" * 80)
    
    results = []
    
    for student in students_data:
        print(f"\n📚 {student['student_name']} (ID: {student['student_id']})")
        print(f"   Оценки: {student['scores']}")
        print(f"   Средний балл: {student['average_score']}")
        
        total_points = 0
        grade_details = []
        
        for i, grade in enumerate(student['scores']):
            # Предполагаем предмет (в реальности нужно знать точный предмет)
            subject = subjects[i % len(subjects)]
            
            # Получаем коэффициент предмета (используем моковые данные)
            coefficient = get_mock_coefficient(subject)
            
            # Рассчитываем баллы
            points = PointsCalculator.calculate_points_for_grade(grade, coefficient)
            total_points += points
            
            base_points = PointsCalculator.BASE_POINTS.get(grade, 0)
            
            grade_details.append({
                'subject': subject,
                'grade': grade,
                'coefficient': coefficient,
                'base_points': base_points,
                'points': points
            })
            
            print(f"   {subject}: {grade} → {base_points} × {coefficient} = {points} баллов")
        
        print(f"   🎯 ИТОГО: {total_points} баллов")
        
        results.append({
            'student_name': student['student_name'],
            'total_points': total_points,
            'average_score': student['average_score'],
            'grades': grade_details
        })
    
    # Сортируем по баллам
    results.sort(key=lambda x: x['total_points'], reverse=True)
    
    print("\n" + "=" * 80)
    print("🏆 РЕЙТИНГ ПО БАЛЛАМ:")
    print("=" * 80)
    
    for i, result in enumerate(results, 1):
        print(f"{i}. {result['student_name']}: {result['total_points']} баллов (средний: {result['average_score']})")
    
    print("\n" + "=" * 80)
    print("📊 СРАВНЕНИЕ С ТАБЛИЦЕЙ В ПРИЛОЖЕНИИ:")
    print("=" * 80)
    
    # Данные из таблицы в приложении
    app_data = [
        ("Петров Петр Петрович", 48),
        ("Сидоров Сидор Сидорович", 35),
        ("Козлов Козел Козлович", 15),
        ("Волков Волк Волкович", 14),
        ("Иванов Иван Иванович", 5),
        ("Медведев Медведь Медведевич", 1)
    ]
    
    print("Приложение → Наш расчет:")
    for app_name, app_points in app_data:
        calculated = next((r for r in results if r['student_name'] == app_name), None)
        if calculated:
            print(f"{app_name}: {app_points} → {calculated['total_points']} баллов")
        else:
            print(f"{app_name}: {app_points} → НЕ НАЙДЕН")
    
    print("\n✅ Тестирование завершено!")


def get_mock_coefficient(subject: str) -> float:
    """Получить моковый коэффициент предмета"""
    coefficients = {
        "Математика": 1.5,
        "Физика": 1.5,
        "Английский язык": 1.4,
        "Информатика": 1.3,
        "Химия": 1.3,
        "Русский язык": 1.2,
        "Биология": 1.1,
        "История": 1.0,
        "География": 1.0,
        "Физкультура": 0.8
    }
    return coefficients.get(subject, 1.0)


if __name__ == "__main__":
    asyncio.run(test_real_grades_calculation())
