"""
Утилиты для расчета баллов с учетом коэффициентов предметов
"""

from typing import Dict, List
from app.db.connection import get_db_connection


class PointsCalculator:
    """Калькулятор баллов с учетом коэффициентов предметов"""
    
    # Базовые баллы согласно таблице
    BASE_POINTS = {
        5: 10,  # Отлично
        4: 5,   # Хорошо
        3: -3,  # Удовлетворительно
        2: -6   # Неудовлетворительно
    }
    
    @staticmethod
    async def get_subject_coefficient(
        subject_name: str,
        school_id: int,
    ) -> float:
        """Получить коэффициент предмета в конкретной школе."""
        conn = await get_db_connection()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT ss.coefficient
                    FROM school_subjects ss
                    JOIN subjects s ON s.id = ss.subject_id
                    WHERE ss.school_id = %s AND s.name = %s
                    """,
                    (school_id, subject_name),
                )
                result = await cursor.fetchone()
                return float(result["coefficient"]) if result else 1.0
        finally:
            conn.close()
    
    @staticmethod
    async def get_all_subject_coefficients(
        school_id: int,
    ) -> Dict[str, float]:
        """Получить коэффициенты предметов конкретной школы."""
        conn = await get_db_connection()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT s.name, ss.coefficient
                    FROM school_subjects ss
                    JOIN subjects s ON s.id = ss.subject_id
                    WHERE ss.school_id = %s
                    """,
                    (school_id,),
                )
                rows = await cursor.fetchall()
                return {
                    row["name"]: float(row["coefficient"]) 
                    for row in rows
                }
        finally:
            conn.close()
    
    @staticmethod
    def calculate_points_for_grade(grade: int, subject_coefficient: float = 1.0) -> int:
        """Рассчитать баллы для оценки с учетом коэффициента предмета"""
        base_points = PointsCalculator.BASE_POINTS.get(grade, 0)
        return int(base_points * subject_coefficient)
    
    @staticmethod
    async def calculate_points_for_grades(
        grades_data: List[Dict],
        school_id: int,
    ) -> List[Dict]:
        """
        Рассчитать баллы для списка оценок
        
        grades_data: List[Dict] - список словарей с ключами:
        - student_id: int
        - subject: str
        - grade: int
        - date: str
        """
        # Получаем все коэффициенты предметов
        subject_coefficients = await PointsCalculator.get_all_subject_coefficients(
            school_id,
        )
        
        result = []
        for grade_data in grades_data:
            subject_name = grade_data["subject"]
            grade_value = grade_data["grade"]
            coefficient = subject_coefficients.get(subject_name, 1.0)
            
            points = PointsCalculator.calculate_points_for_grade(grade_value, coefficient)
            
            result.append({
                **grade_data,
                "points": points,
                "coefficient": coefficient,
                "base_points": PointsCalculator.BASE_POINTS.get(grade_value, 0)
            })
        
        return result
    
    @staticmethod
    def get_points_table() -> Dict[int, Dict[str, int]]:
        """Получить таблицу баллов для отображения"""
        return {
            grade: {
                "base_points": points,
                "description": {
                    5: "Отлично",
                    4: "Хорошо", 
                    3: "Удовлетворительно",
                    2: "Неудовлетворительно"
                }.get(grade, "Неизвестно")
            }
            for grade, points in PointsCalculator.BASE_POINTS.items()
        }
