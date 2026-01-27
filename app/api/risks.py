"""
API для определения рисков в успеваемости студентов
"""
from typing import List
from datetime import datetime, timedelta
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.security import get_current_user
from app.db.connection import get_db_connection
from app.models.user import User

router = APIRouter()


class RiskResponse(BaseModel):
    """Ответ с информацией о риске"""
    id: str
    title: str
    detail: str
    level: str  # "high", "medium", "low"
    category: str  # "grades", "attendance", "no_grades"
    date: str


def _detect_risks(
    grades: List[dict],
    attendance_stats: dict = None,
    performance_trends: List[dict] = None
) -> List[RiskResponse]:
    """
    Определяет риски на основе данных студента
    """
    risks = []
    
    # 1. Риск: Падение среднего балла
    if performance_trends and len(performance_trends) >= 2:
        # Сравниваем последние 2 недели с предыдущими 2 неделями
        recent_count = min(14, len(performance_trends))
        if recent_count >= 4:
            recent_trends = performance_trends[-recent_count:]
            previous_trends = performance_trends[-recent_count*2:-recent_count] if len(performance_trends) >= recent_count*2 else []
            
            if previous_trends:
                recent_avg = sum(t.get('average_score', 0) for t in recent_trends) / len(recent_trends)
                previous_avg = sum(t.get('average_score', 0) for t in previous_trends) / len(previous_trends)
                decline = previous_avg - recent_avg
                
                if decline > 0.5:
                    risks.append(RiskResponse(
                        id="risk_grade_decline",
                        title="Падение среднего балла",
                        detail=f"Средний балл снизился на {decline:.1f} за последние {recent_count//7} недель",
                        level="high",
                        category="grades",
                        date=datetime.now().strftime("%Y-%m-%d")
                    ))
                elif decline > 0.3:
                    risks.append(RiskResponse(
                        id="risk_grade_decline_medium",
                        title="Небольшое падение среднего балла",
                        detail=f"Средний балл снизился на {decline:.1f} за последние {recent_count//7} недель",
                        level="medium",
                        category="grades",
                        date=datetime.now().strftime("%Y-%m-%d")
                    ))
    
    # 2. Риск: Низкая посещаемость
    if attendance_stats:
        absent = attendance_stats.get('absent', 0)
        total = attendance_stats.get('total_lessons', 0)
        late = attendance_stats.get('late', 0)
        
        if total > 0:
            absence_rate = (absent / total) * 100
            recent_absent = absent  # За последние 30 дней
            
            if absence_rate > 25 or recent_absent > 5:
                risks.append(RiskResponse(
                    id="risk_attendance_high",
                    title="Низкая посещаемость",
                    detail=f"{absent} пропусков за последние 30 дней ({absence_rate:.1f}%)",
                    level="high",
                    category="attendance",
                    date=datetime.now().strftime("%Y-%m-%d")
                ))
            elif absence_rate > 15 or recent_absent > 3:
                risks.append(RiskResponse(
                    id="risk_attendance_medium",
                    title="Снижение посещаемости",
                    detail=f"{absent} пропусков за последние 30 дней ({absence_rate:.1f}%)",
                    level="medium",
                    category="attendance",
                    date=datetime.now().strftime("%Y-%m-%d")
                ))
    
    # 3. Риск: Отсутствие оценок по предмету
    if grades:
        # Группируем оценки по предметам
        subject_last_grade = {}
        for grade in grades:
            subject = grade.get('subject', '')
            date_str = grade.get('date', '')
            if subject and date_str:
                try:
                    grade_date = datetime.strptime(date_str, '%Y-%m-%d')
                    if subject not in subject_last_grade or grade_date > subject_last_grade[subject]:
                        subject_last_grade[subject] = grade_date
                except:
                    pass
        
        # Проверяем, есть ли предметы без оценок более 10 дней
        today = datetime.now()
        for subject, last_date in subject_last_grade.items():
            days_without_grades = (today - last_date).days
            if days_without_grades > 10:
                risks.append(RiskResponse(
                    id=f"risk_no_grades_{subject}",
                    title="Отсутствие оценок",
                    detail=f"Нет оценок по {subject} уже {days_without_grades} дней",
                    level="low" if days_without_grades < 20 else "medium",
                    category="no_grades",
                    date=datetime.now().strftime("%Y-%m-%d")
                ))
    
    # 4. Риск: Много низких оценок подряд
    if grades:
        # Группируем по предметам и проверяем последние оценки
        subject_recent_grades = defaultdict(list)
        for grade in sorted(grades, key=lambda x: x.get('date', ''), reverse=True)[:20]:  # Последние 20 оценок
            subject = grade.get('subject', '')
            value = grade.get('value', 0)
            if subject:
                subject_recent_grades[subject].append(value)
        
        for subject, recent_values in subject_recent_grades.items():
            if len(recent_values) >= 3:
                # Проверяем последние 3 оценки
                last_three = recent_values[:3]
                if all(v <= 3 for v in last_three):
                    risks.append(RiskResponse(
                        id=f"risk_low_grades_{subject}",
                        title="Последовательные низкие оценки",
                        detail=f"Последние 3 оценки по {subject}: {', '.join(map(str, last_three))}",
                        level="high" if all(v <= 2 for v in last_three) else "medium",
                        category="grades",
                        date=datetime.now().strftime("%Y-%m-%d")
                    ))
    
    return risks


@router.get("/risks/{student_id}", response_model=List[RiskResponse], tags=["risks"])
async def get_student_risks(
    student_id: int,
    current_user: User = Depends(get_current_user)
):
    """
    Получает список рисков для студента, анализируя данные из базы.
    """
    # Проверяем доступ
    if current_user.role not in ["owner", "admin", "teacher"] and current_user.id != student_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Получаем оценки с названиями предметов
            await cursor.execute("""
                SELECT s.name AS subject, g.value, g.date
                FROM grades g
                JOIN subjects s ON g.subject_id = s.id
                WHERE g.student_id = %s
                ORDER BY g.date DESC
                LIMIT 100
            """, (student_id,))
            grades_rows = await cursor.fetchall()
            
            grades = []
            for row in grades_rows:
                subject = row.get("subject")
                value = row.get("value")
                date_obj = row.get("date")
                
                if subject and value is not None and date_obj:
                    try:
                        if hasattr(date_obj, 'strftime'):
                            date_str = date_obj.strftime("%Y-%m-%d")
                        elif isinstance(date_obj, str):
                            date_str = date_obj
                        else:
                            date_str = str(date_obj)
                    except Exception:
                        date_str = str(date_obj)
                    
                    grades.append({
                        "subject": str(subject),
                        "value": int(value),
                        "date": date_str
                    })
            
            # Получаем статистику посещаемости
            attendance_stats = None
            try:
                await cursor.execute("""
                    SELECT 
                        COUNT(CASE WHEN status = 'present' THEN 1 END) as present,
                        COUNT(CASE WHEN status = 'late' THEN 1 END) as late,
                        COUNT(CASE WHEN status = 'absent' THEN 1 END) as absent,
                        COUNT(*) as total_lessons
                    FROM attendance
                    WHERE student_id = %s
                    AND date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                """, (student_id,))
                attendance_row = await cursor.fetchone()
                if attendance_row:
                    attendance_stats = {
                        "present": attendance_row.get("present") or 0,
                        "late": attendance_row.get("late") or 0,
                        "absent": attendance_row.get("absent") or 0,
                        "total_lessons": attendance_row.get("total_lessons") or 0
                    }
            except Exception:
                pass
            
            # Вычисляем тренды из оценок
            performance_trends = []
            if grades:
                grades_by_date = defaultdict(list)
                for grade in grades:
                    if grade.get("date"):
                        grades_by_date[grade["date"]].append(grade.get("value", 0))
                
                for date, values in sorted(grades_by_date.items()):
                    avg_score = sum(values) / len(values) if values else 0
                    performance_trends.append({
                        "date": date,
                        "average_score": avg_score
                    })
            
            # Определяем риски
            risks = _detect_risks(
                grades=grades,
                attendance_stats=attendance_stats,
                performance_trends=performance_trends
            )
            
            return risks
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting risks: {str(e)}")
    finally:
        conn.close()

