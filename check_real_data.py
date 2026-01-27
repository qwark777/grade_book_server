#!/usr/bin/env python3
"""
Скрипт для проверки реальных данных в базе данных
"""

import asyncio
import sys
import os

# Добавляем путь к модулям приложения
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

from app.db.connection import get_db_connection


async def check_real_data():
    """Проверка реальных данных в базе данных"""
    print("🔍 Проверка реальных данных в базе данных\n")
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем предметы
            print("1. Предметы в базе данных:")
            await cursor.execute("SELECT name, coefficient FROM subjects ORDER BY name")
            subjects = await cursor.fetchall()
            for subject in subjects:
                print(f"   - {subject['name']}: коэффициент {subject['coefficient']}")
            
            print(f"\n   Всего предметов: {len(subjects)}")
            
            # Проверяем оценки студентов
            print("\n2. Оценки студентов:")
            await cursor.execute("""
                SELECT 
                    u.id AS student_id,
                    p.full_name AS student_name,
                    s.name AS subject,
                    g.value AS grade,
                    g.date
                FROM users u
                JOIN profiles p ON u.id = p.user_id
                JOIN grades g ON u.id = g.student_id
                JOIN subjects s ON s.id = g.subject_id
                WHERE u.role = 'student'
                ORDER BY u.id, g.date
            """)
            grades = await cursor.fetchall()
            
            # Группируем по студентам
            student_grades = {}
            for grade in grades:
                student_id = grade['student_id']
                if student_id not in student_grades:
                    student_grades[student_id] = {
                        'name': grade['student_name'],
                        'grades': []
                    }
                student_grades[student_id]['grades'].append({
                    'subject': grade['subject'],
                    'grade': grade['grade'],
                    'date': grade['date']
                })
            
            for student_id, data in student_grades.items():
                print(f"\n   📚 {data['name']} (ID: {student_id}):")
                for grade_info in data['grades']:
                    print(f"      {grade_info['subject']}: {grade_info['grade']} ({grade_info['date']})")
            
            # Проверяем баллы из таблицы user_points
            print("\n3. Баллы из таблицы user_points:")
            await cursor.execute("""
                SELECT 
                    u.id,
                    p.full_name,
                    COALESCE(SUM(up.points), 0) as total_points
                FROM users u
                LEFT JOIN profiles p ON u.id = p.user_id
                LEFT JOIN user_points up ON u.id = up.user_id
                WHERE u.role = 'student'
                GROUP BY u.id, p.full_name
                ORDER BY total_points DESC
            """)
            points = await cursor.fetchall()
            
            for point in points:
                print(f"   {point['full_name']}: {point['total_points']} баллов")
            
            print(f"\n   Всего студентов с баллами: {len(points)}")
            
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(check_real_data())
