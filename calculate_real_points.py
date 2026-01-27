#!/usr/bin/env python3
"""
Скрипт для расчета реальных баллов на основе оценок с коэффициентами
"""

import asyncio
import sys
import os

# Добавляем путь к модулям приложения
sys.path.append(os.path.join(os.path.dirname(__file__)))

from app.db.connection import get_db_connection
from app.utils.points_calculator import PointsCalculator


async def calculate_real_points():
    """Рассчитываем реальные баллы на основе оценок с коэффициентами"""
    print("🧮 Расчет реальных баллов на основе оценок с коэффициентами\n")
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Получаем все оценки студентов с предметами
            await cursor.execute("""
                SELECT 
                    u.id AS student_id,
                    p.full_name AS student_name,
                    s.name AS subject,
                    s.coefficient,
                    g.value AS grade
                FROM users u
                JOIN profiles p ON u.id = p.user_id
                JOIN grades g ON u.id = g.student_id
                JOIN subjects s ON s.id = g.subject_id
                WHERE u.role = 'student'
                ORDER BY u.id, g.date
            """)
            rows = await cursor.fetchall()
            
            # Группируем по студентам
            student_grades = {}
            for row in rows:
                student_id = row['student_id']
                if student_id not in student_grades:
                    student_grades[student_id] = {
                        'name': row['student_name'],
                        'grades': []
                    }
                student_grades[student_id]['grades'].append({
                    'subject': row['subject'],
                    'grade': row['grade'],
                    'coefficient': float(row['coefficient'])
                })
            
            # Рассчитываем баллы для каждого студента
            results = []
            for student_id, data in student_grades.items():
                total_points = 0
                grade_details = []
                
                for grade_info in data['grades']:
                    # Рассчитываем баллы
                    points = PointsCalculator.calculate_points_for_grade(
                        grade_info['grade'], 
                        grade_info['coefficient']
                    )
                    total_points += points
                    
                    base_points = PointsCalculator.BASE_POINTS.get(grade_info['grade'], 0)
                    
                    grade_details.append({
                        'subject': grade_info['subject'],
                        'grade': grade_info['grade'],
                        'coefficient': grade_info['coefficient'],
                        'base_points': base_points,
                        'points': points
                    })
                
                results.append({
                    'student_id': student_id,
                    'student_name': data['name'],
                    'total_points': total_points,
                    'grades': grade_details
                })
            
            # Сортируем по убыванию баллов
            results.sort(key=lambda x: x['total_points'], reverse=True)
            
            print("🏆 РЕЙТИНГ ПО РАСЧЕТНЫМ БАЛЛАМ:")
            print("=" * 80)
            
            for i, result in enumerate(results, 1):
                print(f"{i}. {result['student_name']}: {result['total_points']} баллов")
                
                # Показываем детали для первых 3 студентов
                if i <= 3:
                    print("   Детали оценок:")
                    subject_totals = {}
                    for grade in result['grades']:
                        subject = grade['subject']
                        if subject not in subject_totals:
                            subject_totals[subject] = {'points': 0, 'count': 0}
                        subject_totals[subject]['points'] += grade['points']
                        subject_totals[subject]['count'] += 1
                    
                    for subject, totals in subject_totals.items():
                        avg_points = totals['points'] / totals['count']
                        print(f"     {subject}: {totals['points']} баллов ({totals['count']} оценок, ср. {avg_points:.1f})")
                    print()
            
            print("=" * 80)
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
                    diff = calculated['total_points'] - app_points
                    print(f"{app_name}: {app_points} → {calculated['total_points']} баллов (разница: {diff:+d})")
                else:
                    print(f"{app_name}: {app_points} → НЕ НАЙДЕН")
            
            print("\n✅ Расчет завершен!")
            
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(calculate_real_points())
