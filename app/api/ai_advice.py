from typing import List, Optional
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime
import traceback

from app.core.security import get_current_user
from app.core.entitlements import get_school_id_for_user, require_school_feature
from app.core.subscription_limits import consume_school_quota
from app.db.connection import get_db_connection
from app.models.user import User
from app.models.ai_feedback import AiAdviceFeedbackCreate
from app.api.student_access import ensure_can_access_student

# Безопасный импорт ML модели
try:
    from app.services.ai_model import get_analyzer
    ML_AVAILABLE = True
except ImportError:
    ML_AVAILABLE = False
    def get_analyzer():
        return None

router = APIRouter()


async def _consume_ai_quota(current_user: User) -> None:
    school_id = await get_school_id_for_user(current_user)
    if school_id is None:
        return
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await consume_school_quota(
                cursor,
                school_id=school_id,
                entitlement_key="ai.quota",
                user_id=current_user.id,
            )
            await conn.commit()
    finally:
        conn.close()


class AiAdviceRequest(BaseModel):
    """Данные для анализа и генерации AI-советов"""
    student_id: int
    grades: List[dict]  # Список оценок
    attendance_stats: Optional[dict] = None  # Статистика посещаемости
    performance_trends: Optional[List[dict]] = None  # Тренды успеваемости
    grade_distribution: Optional[dict] = None  # Распределение оценок


class AiAdviceResponse(BaseModel):
    """Ответ с AI-советами"""
    id: str
    title: str
    description: str
    priority: str  # "high", "medium", "low"
    category: str  # "grades", "attendance", "behavior", "study_habits", "time_management"
    icon: str
    date: str
    action_text: Optional[str] = None
    user_feedback: Optional[bool] = None


def _analyze_student_data(
    grades: List[dict],
    attendance_stats: Optional[dict] = None,
    performance_trends: Optional[List[dict]] = None,
    grade_distribution: Optional[dict] = None
) -> List[AiAdviceResponse]:
    """
    Анализирует данные студента и возвращает список AI-советов.
    Использует ML модель для определения приоритетов.
    """
    advice_list = []
    
    # Используем ML модель для определения приоритета
    ml_priority = None
    confidence = 0.0
    if ML_AVAILABLE:
        try:
            analyzer = get_analyzer()
            if analyzer is not None:
                ml_priority, confidence = analyzer.predict_priority(
                    grades=grades,
                    attendance_stats=attendance_stats,
                    performance_trends=performance_trends
                )
        except Exception as e:
            # Если модель не работает, используем правило-ориентированный подход
            pass
    
    # Анализ оценок
    if grades:
        # Подсчитываем средний балл
        total_score = sum(grade.get('value', 0) for grade in grades)
        avg_score = total_score / len(grades) if grades else 0
        
        # Анализируем предметы с низкими оценками
        subject_scores = defaultdict(list)
        for grade in grades:
            subject = grade.get('subject', '')
            value = grade.get('value', 0)
            if subject:
                subject_scores[subject].append(value)
        
        # Находим предметы с низким средним баллом
        # Используем ML приоритет для определения важности
        priority_map = {0: "low", 1: "medium", 2: "high"}
        base_priority_grades = priority_map.get(ml_priority, "medium") if ml_priority is not None else "medium"
        
        for subject, scores in subject_scores.items():
            subject_avg = sum(scores) / len(scores) if scores else 0
            if subject_avg < 3.5:
                # Приоритет зависит от серьезности проблемы
                subject_priority = "high" if subject_avg < 2.5 else ("medium" if subject_avg < 3.0 else base_priority_grades)
                
                advice_list.append(AiAdviceResponse(
                    id=f"advice_{len(advice_list) + 1}",
                    title=f"Улучшите {subject}",
                    description=f"Средний балл по предмету {subject_avg:.1f} — добавьте практику и повторение материала. Уверенность модели: {confidence:.0%}" if ml_priority is not None else f"Средний балл по предмету {subject_avg:.1f} — добавьте практику и повторение материала.",
                    priority=subject_priority,
                    category="grades",
                    icon="📊",
                    date=datetime.now().strftime("%Y-%m-%d"),
                    action_text="Подробнее"
                ))
        
        # Если средний балл хороший, добавляем похвалу
        if avg_score >= 4.0:
            if subject_scores:
                best_subject = max(subject_scores.items(), key=lambda x: sum(x[1]) / len(x[1]) if x[1] else 0)
                advice_list.append(AiAdviceResponse(
                    id=f"advice_{len(advice_list) + 1}",
                    title=f"Отличные результаты по {best_subject[0]}",
                    description=f"Стабильно высокие результаты по предмету {best_subject[0]}.",
                    priority="low",
                    category="grades",
                    icon="🎉",
                    date=datetime.now().strftime("%Y-%m-%d"),
                    action_text=None
                ))
    
    # Анализ посещаемости (используем ML приоритет)
    priority_map = {0: "low", 1: "medium", 2: "high"}
    base_priority = priority_map.get(ml_priority, "medium") if ml_priority is not None else "medium"
    
    if attendance_stats:
        absent = attendance_stats.get('absent', 0)
        total = attendance_stats.get('total_lessons', 0)
        if total > 0:
            absence_rate = (absent / total) * 100
            if absence_rate > 10:  # Более 10% пропусков
                # Приоритет зависит от серьезности проблемы
                attendance_priority = "high" if absence_rate > 20 else ("medium" if absence_rate > 15 else base_priority)
                ml_info = f" (ML уверенность: {confidence:.0%})" if ml_priority is not None else ""
                
                advice_list.append(AiAdviceResponse(
                    id=f"advice_{len(advice_list) + 1}",
                    title="Посещаемость",
                    description=f"{absent} пропусков за период ({absence_rate:.1f}%). Рекомендуется улучшить посещаемость.{ml_info}",
                    priority=attendance_priority,
                    category="attendance",
                    icon="📅",
                    date=datetime.now().strftime("%Y-%m-%d"),
                    action_text="План действий"
                ))
    
    # Анализ трендов (используем ML приоритет)
    if performance_trends and len(performance_trends) >= 2:
        recent_trends = performance_trends[-5:]  # Последние 5 точек
        if len(recent_trends) >= 2:
            first_score = recent_trends[0].get('average_score', 0)
            last_score = recent_trends[-1].get('average_score', 0)
            trend_diff = last_score - first_score
            
            if trend_diff < -0.3:  # Серьезное снижение
                ml_info = f" ML уверенность: {confidence:.0%}" if ml_priority is not None else ""
                advice_list.append(AiAdviceResponse(
                    id=f"advice_{len(advice_list) + 1}",
                    title="Снижение успеваемости",
                    description=f"Замечено снижение среднего балла на {abs(trend_diff):.1f}. Рекомендуется обратить внимание на учебу.{ml_info}",
                    priority="high",
                    category="study_habits",
                    icon="📉",
                    date=datetime.now().strftime("%Y-%m-%d"),
                    action_text="Рекомендации"
                ))
            elif trend_diff < 0:  # Небольшое снижение
                advice_list.append(AiAdviceResponse(
                    id=f"advice_{len(advice_list) + 1}",
                    title="Небольшое снижение успеваемости",
                    description=f"Небольшое снижение среднего балла на {abs(trend_diff):.1f}. Стоит обратить внимание.",
                    priority=base_priority if base_priority != "low" else "medium",
                    category="study_habits",
                    icon="📉",
                    date=datetime.now().strftime("%Y-%m-%d"),
                    action_text="Рекомендации"
                ))
            elif trend_diff > 0.3:  # Заметное улучшение
                advice_list.append(AiAdviceResponse(
                    id=f"advice_{len(advice_list) + 1}",
                    title="Улучшение успеваемости",
                    description=f"Отличный прогресс! Средний балл вырос на {trend_diff:.1f}. Продолжайте в том же духе.",
                    priority="low",
                    category="grades",
                    icon="📈",
                    date=datetime.now().strftime("%Y-%m-%d"),
                    action_text=None
                ))
    
    # Если нет советов, добавляем общий совет
    if not advice_list:
        advice_list.append(AiAdviceResponse(
            id="advice_default",
            title="Продолжайте в том же духе",
            description="Ваша успеваемость стабильна. Продолжайте поддерживать текущий уровень.",
            priority="low",
            category="study_habits",
            icon="✨",
            date=datetime.now().strftime("%Y-%m-%d"),
            action_text=None
        ))
    
    return advice_list


def _apply_feedback_preferences(
    advice_list: List[AiAdviceResponse],
    feedback_by_id: dict[str, bool],
) -> List[AiAdviceResponse]:
    priority_rank = {"high": 0, "medium": 1, "low": 2}

    for advice in advice_list:
        advice.user_feedback = feedback_by_id.get(advice.id)

    def sort_key(advice: AiAdviceResponse):
        feedback = feedback_by_id.get(advice.id)
        feedback_rank = 0 if feedback is True else (2 if feedback is False else 1)
        return (feedback_rank, priority_rank.get(advice.priority, 1), advice.title)

    return sorted(advice_list, key=sort_key)


@router.post("/ai-advice/analyze", response_model=List[AiAdviceResponse], tags=["ai-advice"])
async def analyze_student_data(
    request: AiAdviceRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Анализирует данные студента и возвращает AI-советы.
    
    Принимает данные об оценках, посещаемости и успеваемости,
    анализирует их и возвращает персонализированные советы.
    """
    await require_school_feature(current_user, "analytics_enabled", detail="Аналитика отключена в настройках школы")
    await ensure_can_access_student(current_user, request.student_id)
    await _consume_ai_quota(current_user)
    
    try:
        # Используем общую функцию анализа
        return _analyze_student_data(
            grades=request.grades,
            attendance_stats=request.attendance_stats,
            performance_trends=request.performance_trends,
            grade_distribution=request.grade_distribution
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error analyzing data: {str(e)}")


@router.get("/ai-advice/{student_id}", response_model=List[AiAdviceResponse], tags=["ai-advice"])
async def get_ai_advice(
    student_id: int,
    current_user: User = Depends(get_current_user)
):
    """
    Получает AI-советы для студента, собирая данные из базы.
    """
    await require_school_feature(current_user, "analytics_enabled", detail="Аналитика отключена в настройках школы")
    await ensure_can_access_student(current_user, student_id)
    await _consume_ai_quota(current_user)
    
    conn = await get_db_connection()
    try:
        # Собираем данные студента из базы
        async with conn.cursor() as cursor:
            # Получаем оценки с названиями предметов через JOIN
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
                    # Преобразуем дату в строку
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
            
            # Получаем статистику посещаемости (если есть таблица attendance)
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
                """, (student_id,))
                attendance_row = await cursor.fetchone()
                if attendance_row:
                    attendance_stats = {
                        "present": attendance_row.get("present") or 0,
                        "late": attendance_row.get("late") or 0,
                        "absent": attendance_row.get("absent") or 0,
                        "total_lessons": attendance_row.get("total_lessons") or 0
                    }
            except Exception as e:
                # Таблица attendance может не существовать
                # Просто пропускаем, если таблицы нет
                pass
            
            # Вычисляем тренды из оценок
            performance_trends = []
            if grades:
                # Группируем по датам
                grades_by_date = defaultdict(list)
                for grade in grades:
                    if grade.get("date"):
                        grades_by_date[grade["date"]].append(grade.get("value", 0))
                
                # Вычисляем средние по датам
                for date, values in sorted(grades_by_date.items()):
                    avg_score = sum(values) / len(values) if values else 0
                    performance_trends.append({
                        "date": date,
                        "average_score": avg_score
                    })
            
            # Используем общую функцию анализа
            advice_list = _analyze_student_data(
                grades=grades,
                attendance_stats=attendance_stats,
                performance_trends=performance_trends,
                grade_distribution=None
            )

            await cursor.execute(
                "SELECT advice_id, is_useful FROM ai_advice_feedback WHERE student_id = %s",
                (student_id,),
            )
            feedback_rows = await cursor.fetchall()
            feedback_by_id = {
                str(row.get("advice_id")): bool(row.get("is_useful"))
                for row in feedback_rows
                if row.get("advice_id") is not None
            }

            return _apply_feedback_preferences(advice_list, feedback_by_id)
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting AI advice: {str(e)}")
    finally:
        conn.close()

@router.post("/ai-advice/feedback", tags=["ai-advice"])
async def save_ai_advice_feedback(
    body: AiAdviceFeedbackCreate,
    current_user: User = Depends(get_current_user)
):
    await require_school_feature(current_user, "analytics_enabled", detail="Аналитика отключена в настройках школы")

    await ensure_can_access_student(current_user, body.student_id)

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO ai_advice_feedback (advice_id, student_id, is_useful, comment)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    is_useful = %s,
                    comment = %s,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    body.advice_id,
                    body.student_id,
                    int(body.is_useful),
                    body.comment,
                    int(body.is_useful),
                    body.comment,
                ),
            )

        return {"success": True, "advice_id": body.advice_id, "is_useful": body.is_useful}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving AI advice feedback: {str(e)}")
    finally:
        conn.close()
