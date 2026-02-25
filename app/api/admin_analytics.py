"""
API endpoints for admin analytics and attention-required alerts
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta

from app.core.security import get_current_user
from app.db.connection import get_db_connection
from app.models.user import User

router = APIRouter(prefix="/admin/analytics")


def _extract_count(row) -> int:
    if not row:
        return 0
    if isinstance(row, dict):
        return int(row.get("cnt", row.get("COUNT(*)", 0)) or 0)
    return int(row[0] if row else 0)


class AttentionAlert(BaseModel):
    """Карточка предупреждения для админа"""
    id: str
    type: str  # "low_attendance", "grade_decline"
    title: str
    subtitle: str
    class_id: Optional[int] = None
    class_name: Optional[str] = None
    value: float  # Процент посещаемости или средний балл
    severity: str  # "high", "medium", "low"
    previous_value: Optional[float] = None  # Предыдущее значение (для снижения успеваемости)
    decline_amount: Optional[float] = None  # На сколько снизилось


class FinancialStatsResponse(BaseModel):
    """Финансовая статистика для администратора"""
    total_revenue: float  # Общий доход
    active_subscriptions: int  # Количество активных подписок
    pending_payments: float  # Сумма ожидаемых платежей
    overdue_payments: float  # Сумма просроченных платежей


class TransactionItem(BaseModel):
    """Элемент транзакции для финансового мониторинга"""
    id: int
    user_id: int
    user_name: str
    transaction_type: str
    amount: float
    currency: str
    description: Optional[str] = None
    created_at: datetime


class RecentEvent(BaseModel):
    """Последнее событие для админа"""
    id: int
    event_type: str  # "grade_added", "homework_submitted", "class_created", "student_added", "teacher_added"
    title: str
    subtitle: str
    created_at: datetime


@router.get("/recent-events", response_model=List[RecentEvent])
async def get_recent_events(
    limit: int = 10,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    current_user: User = Depends(get_current_user)
):
    """Получить последние события школы (admin/owner)"""
    if current_user.role not in ["admin", "owner", "superadmin"]:
        raise HTTPException(status_code=403, detail="Access denied")

    conn = await get_db_connection()
    events = []

    try:
        async with conn.cursor() as cursor:
            school_id = None
            if current_user.role == "admin":
                await cursor.execute(
                    "SELECT school_id FROM school_admins WHERE admin_user_id = %s",
                    (current_user.id,)
                )
                row = await cursor.fetchone()
                if row:
                    school_id = row["school_id"] if isinstance(row, dict) else row[0]
                else:
                    return []

            school_filter = "AND c.school_id = %s" if school_id else ""
            date_filter = ""
            grade_params = [limit * 3]
            if date_from:
                date_filter = " AND g.date >= %s"
                grade_params.insert(0, date_from)
            if date_to:
                date_filter += " AND g.date <= %s"
                grade_params.insert(1 if date_from else 0, date_to)
            if school_id:
                grade_params.insert(0, school_id)

            # 1. Последние оценки (grade_added)
            grades_sql = """
                SELECT g.id, g.date, c.name as class_name,
                       COALESCE(TRIM(BOTH '"' FROM p.full_name), u.username) as student_name,
                       g.value, s.name as subject_name
                FROM grades g
                JOIN class_students cs ON g.student_id = cs.student_id
                JOIN classes c ON cs.class_id = c.id
                JOIN users u ON g.student_id = u.id
                LEFT JOIN profiles p ON u.id = p.user_id
                LEFT JOIN subjects s ON g.subject_id = s.id
                WHERE c.school_id IS NOT NULL
            """ + (" AND c.school_id = %s" if school_id else "") + date_filter + """
                ORDER BY g.date DESC, g.id DESC
                LIMIT %s
            """
            await cursor.execute(grades_sql, tuple(grade_params))
            grade_rows = await cursor.fetchall()
            seen_grade_ids = set()

            for r in grade_rows:
                rid = r["id"] if isinstance(r, dict) else r[0]
                if rid in seen_grade_ids:
                    continue
                seen_grade_ids.add(rid)
                rdate = r.get("date") or r[3] if isinstance(r, dict) else r[1]
                class_name = r.get("class_name") or "" if isinstance(r, dict) else (r[2] or "")
                student_name = r.get("student_name") or "Ученик" if isinstance(r, dict) else (r[3] or "Ученик")
                value = r.get("value") if isinstance(r, dict) else r[4]
                subject_name = r.get("subject_name") or "" if isinstance(r, dict) else (r[5] or "")
                dt = datetime.combine(rdate, datetime.min.time()) if hasattr(rdate, 'isoformat') and not isinstance(rdate, datetime) else (rdate or datetime.now())
                events.append(RecentEvent(
                    id=rid,
                    event_type="grade_added",
                    title=f"Оценка {value}",
                    subtitle=f"{class_name} · {student_name}" + (f" · {subject_name}" if subject_name else ""),
                    created_at=dt,
                ))

            # 2. Последние сдачи ДЗ (homework_submitted)
            hw_date_filter = ""
            hw_params = [limit]
            if date_from:
                hw_date_filter = " AND DATE(hs.created_at) >= %s"
                hw_params.insert(0, date_from)
            if date_to:
                hw_date_filter += " AND DATE(hs.created_at) <= %s"
                hw_params.insert(1 if date_from else 0, date_to)
            if school_id:
                hw_params.insert(0, school_id)
            hw_sql = """
                SELECT hs.id, hs.created_at, c.name as class_name,
                       COALESCE(TRIM(BOTH '"' FROM p.full_name), u.username) as student_name
                FROM homework_submissions hs
                JOIN homeworks h ON hs.homework_id = h.id
                JOIN classes c ON h.class_id = c.id
                JOIN users u ON hs.student_id = u.id
                LEFT JOIN profiles p ON u.id = p.user_id
                WHERE c.school_id IS NOT NULL
            """ + (f" AND c.school_id = %s" if school_id else "") + hw_date_filter + """
                ORDER BY hs.created_at DESC
                LIMIT %s
            """
            await cursor.execute(hw_sql, tuple(hw_params))
            hw_rows = await cursor.fetchall()

            for r in hw_rows:
                rid = r["id"] if isinstance(r, dict) else r[0]
                created = r.get("created_at") if isinstance(r, dict) else r[1]
                class_name = r.get("class_name") or "" if isinstance(r, dict) else (r[2] or "")
                student_name = r.get("student_name") or "Ученик" if isinstance(r, dict) else (r[3] or "Ученик")
                events.append(RecentEvent(
                    id=rid,
                    event_type="homework_submitted",
                    title="Сдано ДЗ",
                    subtitle=f"{class_name} · {student_name}",
                    created_at=created or datetime.now(),
                ))

            # 3. Созданные классы (class_created) — используем id как порядок
            class_params = [limit]
            if school_id:
                class_params.insert(0, school_id)
            class_sql = """
                SELECT id, name FROM classes
                WHERE school_id IS NOT NULL
            """ + (f" AND school_id = %s" if school_id else "") + """
                ORDER BY id DESC
                LIMIT %s
            """
            await cursor.execute(class_sql, tuple(class_params))
            class_rows = await cursor.fetchall()

            for r in class_rows:
                cid = r["id"] if isinstance(r, dict) else r[0]
                cname = r.get("name") or "" if isinstance(r, dict) else (r[1] or "")
                # Нет created_at — используем текущее время минус смещение по id
                dt = datetime.now() - timedelta(days=min(cid, 365))
                events.append(RecentEvent(
                    id=cid,
                    event_type="class_created",
                    title="Создан класс",
                    subtitle=cname,
                    created_at=dt,
                ))

            events.sort(key=lambda x: x.created_at, reverse=True)
            return events[:limit]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting recent events: {str(e)}")
    finally:
        conn.close()


@router.get("/attention-required", response_model=List[AttentionAlert])
async def get_attention_required(current_user: User = Depends(get_current_user)):
    """Получить список классов, требующих внимания"""
    # Проверяем права доступа
    if current_user.role not in ["admin", "owner", "superadmin"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    conn = await get_db_connection()
    alerts = []
    
    try:
        async with conn.cursor() as cursor:
            # 1. Находим классы с низкой посещаемостью (< 80%)
            await cursor.execute("""
                SELECT 
                    c.id as class_id,
                    c.name as class_name,
                    COUNT(DISTINCT cs.student_id) as student_count,
                    SUM(CASE WHEN a.status = 'present' THEN 1 ELSE 0 END) as present_count,
                    SUM(CASE WHEN a.status IN ('absent', 'late') THEN 1 ELSE 0 END) as absent_count,
                    COUNT(a.id) as total_attendance
                FROM classes c
                LEFT JOIN class_students cs ON c.id = cs.class_id
                LEFT JOIN attendance a ON cs.student_id = a.student_id 
                    AND a.date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                GROUP BY c.id, c.name
                HAVING total_attendance > 0
            """)
            
            attendance_rows = await cursor.fetchall()
            
            for row in attendance_rows:
                total = row.get("total_attendance", 0)
                present = row.get("present_count", 0)
                
                if total > 0:
                    attendance_rate = (present / total) * 100
                    
                    # Низкая посещаемость (< 80%)
                    if attendance_rate < 80:
                        alerts.append(AttentionAlert(
                            id=f"attendance_{row['class_id']}",
                            type="low_attendance",
                            title="Низкая посещаемость",
                            subtitle=f"Класс {row['class_name']} - {attendance_rate:.0f}% посещаемость",
                            class_id=row['class_id'],
                            class_name=row['class_name'],
                            value=attendance_rate,
                            severity="high" if attendance_rate < 70 else "medium"
                        ))
            
            # 2. Находим классы со снижением успеваемости
            await cursor.execute("""
                SELECT 
                    c.id as class_id,
                    c.name as class_name,
                    AVG(g.value) as current_avg,
                    (SELECT AVG(g2.value)
                     FROM grades g2
                     JOIN class_students cs2 ON g2.student_id = cs2.student_id
                     JOIN users u2 ON u2.id = g2.student_id AND u2.role = 'student'
                     WHERE cs2.class_id = c.id
                     AND g2.date >= DATE_SUB(CURDATE(), INTERVAL 60 DAY)
                     AND g2.date < DATE_SUB(CURDATE(), INTERVAL 30 DAY)) as previous_avg
                FROM classes c
                JOIN class_students cs ON c.id = cs.class_id
                JOIN grades g ON cs.student_id = g.student_id
                JOIN users u ON u.id = g.student_id AND u.role = 'student'
                WHERE g.date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                GROUP BY c.id, c.name
                HAVING current_avg IS NOT NULL
            """)
            
            grade_rows = await cursor.fetchall()
            
            for row in grade_rows:
                current_avg = float(row.get("current_avg", 0)) if row.get("current_avg") else None
                previous_avg = float(row.get("previous_avg", 0)) if row.get("previous_avg") else None
                
                if current_avg and previous_avg:
                    decline = previous_avg - current_avg
                    
                    # Снижение успеваемости более чем на 0.3 балла
                    if decline > 0.3:
                        alerts.append(AttentionAlert(
                            id=f"grade_decline_{row['class_id']}",
                            type="grade_decline",
                            title="Снижение успеваемости",
                            subtitle=f"Класс {row['class_name']} - средний балл {current_avg:.1f}",
                            class_id=row['class_id'],
                            class_name=row['class_name'],
                            value=current_avg,
                            previous_value=previous_avg,
                            decline_amount=decline,
                            severity="high" if decline > 0.5 or current_avg < 3.5 else "medium"
                        ))
                    # Низкий средний балл (< 3.5)
                    elif current_avg < 3.5:
                        alerts.append(AttentionAlert(
                            id=f"low_grade_{row['class_id']}",
                            type="grade_decline",
                            title="Снижение успеваемости",
                            subtitle=f"Класс {row['class_name']} - средний балл {current_avg:.1f}",
                            class_id=row['class_id'],
                            class_name=row['class_name'],
                            value=current_avg,
                            previous_value=previous_avg,
                            decline_amount=decline if decline > 0 else None,
                            severity="high" if current_avg < 3.0 else "medium"
                        ))
            
            # Сортируем по важности (high > medium > low) и затем по значению
            alerts.sort(key=lambda x: (
                {"high": 0, "medium": 1, "low": 2}.get(x.severity, 3),
                -x.value if x.type == "low_attendance" else x.value  # Для посещаемости - по убыванию, для оценок - по возрастанию
            ))
            
            return alerts[:10]  # Возвращаем топ-10 самых важных
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting attention required: {str(e)}")
    finally:
        conn.close()


@router.get("/financial-stats", response_model=FinancialStatsResponse)
async def get_financial_stats(current_user: User = Depends(get_current_user)):
    """Получить финансовую статистику для администратора"""
    # Проверяем права доступа
    if current_user.role not in ["admin", "owner", "superadmin"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    conn = await get_db_connection()
    
    try:
        async with conn.cursor() as cursor:
            # Получаем school_id для админа (для owner будет None - все школы)
            school_id = None
            if current_user.role == "admin":
                await cursor.execute("""
                    SELECT school_id FROM school_admins WHERE admin_user_id = %s
                """, (current_user.id,))
                school_row = await cursor.fetchone()
                if school_row:
                    school_id = school_row["school_id"] if isinstance(school_row, dict) else school_row[0]
            
            # 1. Общий доход - сумма всех транзакций типа "payment" или "deposit"
            if school_id:
                # Для админа - только транзакции пользователей его школы
                await cursor.execute("""
                    SELECT COALESCE(SUM(bt.amount), 0) as total_revenue
                    FROM balance_transactions bt
                    JOIN users u ON bt.user_id = u.id
                    JOIN class_students cs ON u.id = cs.student_id
                    JOIN classes c ON cs.class_id = c.id
                    WHERE bt.transaction_type IN ('payment', 'deposit')
                    AND c.school_id = %s
                """, (school_id,))
            else:
                # Для owner - все транзакции
                await cursor.execute("""
                    SELECT COALESCE(SUM(amount), 0) as total_revenue
                    FROM balance_transactions
                    WHERE transaction_type IN ('payment', 'deposit')
                """)
            
            revenue_row = await cursor.fetchone()
            total_revenue = float(revenue_row["total_revenue"] or 0) if isinstance(revenue_row, dict) else float(revenue_row[0] or 0)
            
            # 2. Активные подписки
            if school_id:
                await cursor.execute("""
                    SELECT COUNT(*) as count
                    FROM school_subscriptions
                    WHERE school_id = %s AND status = 'active'
                """, (school_id,))
            else:
                await cursor.execute("""
                    SELECT COUNT(*) as count
                    FROM school_subscriptions
                    WHERE status = 'active'
                """)
            
            subs_row = await cursor.fetchone()
            active_subscriptions = subs_row["count"] if isinstance(subs_row, dict) else subs_row[0]
            
            # 3. Ожидается оплат - сумма счетов pending с датой оплаты в будущем или сегодня
            # 4. Просрочено - сумма счетов pending с просроченной датой оплаты
            if school_id:
                await cursor.execute("""
                    SELECT 
                        COALESCE(SUM(CASE WHEN status = 'pending' AND due_date >= CURDATE() THEN amount ELSE 0 END), 0) as pending,
                        COALESCE(SUM(CASE WHEN status = 'pending' AND due_date < CURDATE() THEN amount ELSE 0 END), 0) as overdue
                    FROM subscription_invoices
                    WHERE school_id = %s
                """, (school_id,))
            else:
                await cursor.execute("""
                    SELECT 
                        COALESCE(SUM(CASE WHEN status = 'pending' AND due_date >= CURDATE() THEN amount ELSE 0 END), 0) as pending,
                        COALESCE(SUM(CASE WHEN status = 'pending' AND due_date < CURDATE() THEN amount ELSE 0 END), 0) as overdue
                    FROM subscription_invoices
                """)
            inv_row = await cursor.fetchone()
            pending_payments = float(inv_row["pending"] or 0) if isinstance(inv_row, dict) else float(inv_row[0] or 0)
            overdue_payments = float(inv_row["overdue"] or 0) if isinstance(inv_row, dict) else float(inv_row[1] or 0)
            
            return FinancialStatsResponse(
                total_revenue=total_revenue,
                active_subscriptions=active_subscriptions,
                pending_payments=pending_payments,
                overdue_payments=overdue_payments
            )
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting financial stats: {str(e)}")
    finally:
        conn.close()


@router.get("/dashboard-stats")
async def get_dashboard_stats(current_user: User = Depends(get_current_user)):
    """Ключевые показатели для дашборда: ученики, учителя, классы."""
    if current_user.role not in ("admin", "owner", "superadmin"):
        raise HTTPException(status_code=403, detail="Access denied")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            school_id = None
            if current_user.role == "admin":
                await cursor.execute(
                    "SELECT school_id FROM school_admins WHERE admin_user_id = %s",
                    (current_user.id,)
                )
                row = await cursor.fetchone()
                if row:
                    school_id = row["school_id"] if isinstance(row, dict) else row[0]

            # Классы
            if school_id:
                await cursor.execute("SELECT COUNT(*) as cnt FROM classes WHERE school_id = %s", (school_id,))
            else:
                await cursor.execute("SELECT COUNT(*) as cnt FROM classes")
            classes_row = await cursor.fetchone()
            classes_count = _extract_count(classes_row)

            # Ученики
            if school_id:
                await cursor.execute("""
                    SELECT COUNT(DISTINCT cs.student_id) as cnt FROM class_students cs
                    JOIN classes c ON c.id = cs.class_id AND c.school_id = %s
                """, (school_id,))
            else:
                await cursor.execute("SELECT COUNT(*) as cnt FROM users WHERE role = 'student'")
            students_row = await cursor.fetchone()
            students_count = _extract_count(students_row)

            # Учителя
            if school_id:
                await cursor.execute("""
                    SELECT COUNT(DISTINCT ct.teacher_id) as cnt FROM class_teachers ct
                    JOIN classes c ON c.id = ct.class_id AND c.school_id = %s
                """, (school_id,))
            else:
                await cursor.execute("SELECT COUNT(*) as cnt FROM users WHERE role = 'teacher'")
            teachers_row = await cursor.fetchone()
            teachers_count = _extract_count(teachers_row)

            # Средний балл по школе
            if school_id:
                await cursor.execute("""
                    SELECT ROUND(AVG(g.value), 2) as avg_val
                    FROM grades g
                    JOIN class_students cs ON g.student_id = cs.student_id
                    JOIN classes c ON cs.class_id = c.id AND c.school_id = %s
                """, (school_id,))
            else:
                await cursor.execute("""
                    SELECT ROUND(AVG(value), 2) as avg_val FROM grades
                """)
            avg_row = await cursor.fetchone()
            avg_grade = None
            if avg_row:
                v = avg_row.get("avg_val") if isinstance(avg_row, dict) else avg_row[0]
                if v is not None:
                    avg_grade = round(float(v), 1)

            return {
                "students": students_count or 0,
                "teachers": teachers_count or 0,
                "classes": classes_count or 0,
                "average_grade": avg_grade,
            }
    finally:
        conn.close()


@router.get("/classes-count")
async def get_classes_count(current_user: User = Depends(get_current_user)):
    """Получить количество классов для администратора"""
    # Проверяем права доступа
    if current_user.role not in ["admin", "owner", "superadmin"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    conn = await get_db_connection()
    
    try:
        async with conn.cursor() as cursor:
            school_id = None
            if current_user.role == "admin":
                # Получаем school_id для админа
                await cursor.execute(
                    "SELECT school_id FROM school_admins WHERE admin_user_id = %s",
                    (current_user.id,)
                )
                result = await cursor.fetchone()
                if result:
                    school_id = result["school_id"] if isinstance(result, dict) else result[0]
            
            # Подсчитываем классы
            if school_id:
                await cursor.execute(
                    "SELECT COUNT(*) as count FROM classes WHERE school_id = %s",
                    (school_id,)
                )
            else:
                # Для owner - все классы
                await cursor.execute("SELECT COUNT(*) as count FROM classes")
            
            result = await cursor.fetchone()
            count = result["count"] if isinstance(result, dict) else result[0]
            
            return {"count": count}
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting classes count: {str(e)}")
    finally:
        conn.close()


@router.get("/financial-transactions", response_model=List[TransactionItem])
async def get_financial_transactions(
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user)
):
    """Получить транзакции для финансового мониторинга (только для админов)"""
    # Проверяем права доступа
    if current_user.role not in ["admin", "owner", "superadmin"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    conn = await get_db_connection()
    
    try:
        async with conn.cursor() as cursor:
            # Получаем school_id для админа
            school_id = None
            if current_user.role == "admin":
                await cursor.execute("""
                    SELECT school_id FROM school_admins WHERE admin_user_id = %s
                """, (current_user.id,))
                school_row = await cursor.fetchone()
                if school_row:
                    school_id = school_row["school_id"] if isinstance(school_row, dict) else school_row[0]
            
            # Получаем транзакции
            if school_id:
                await cursor.execute("""
                    SELECT 
                        bt.id,
                        bt.user_id,
                        COALESCE(TRIM(BOTH '"' FROM p.full_name), u.username) as user_name,
                        bt.transaction_type,
                        bt.amount,
                        bt.currency,
                        bt.description,
                        bt.created_at
                    FROM balance_transactions bt
                    JOIN users u ON bt.user_id = u.id
                    LEFT JOIN profiles p ON u.id = p.user_id
                    JOIN class_students cs ON u.id = cs.student_id
                    JOIN classes c ON cs.class_id = c.id
                    WHERE c.school_id = %s
                    ORDER BY bt.created_at DESC
                    LIMIT %s OFFSET %s
                """, (school_id, limit, offset))
            else:
                await cursor.execute("""
                    SELECT 
                        bt.id,
                        bt.user_id,
                        COALESCE(TRIM(BOTH '"' FROM p.full_name), u.username) as user_name,
                        bt.transaction_type,
                        bt.amount,
                        bt.currency,
                        bt.description,
                        bt.created_at
                    FROM balance_transactions bt
                    JOIN users u ON bt.user_id = u.id
                    LEFT JOIN profiles p ON u.id = p.user_id
                    ORDER BY bt.created_at DESC
                    LIMIT %s OFFSET %s
                """, (limit, offset))
            
            rows = await cursor.fetchall()
            
            return [
                TransactionItem(
                    id=row["id"],
                    user_id=row["user_id"],
                    user_name=row.get("user_name", "Неизвестно"),
                    transaction_type=row["transaction_type"],
                    amount=float(row["amount"]),
                    currency=row["currency"],
                    description=row.get("description"),
                    created_at=row["created_at"]
                )
                for row in rows
            ]
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting financial transactions: {str(e)}")
    finally:
        conn.close()


class FinancialChartData(BaseModel):
    """Данные для графика финансов"""
    labels: List[str]  # Месяцы (Янв, Фев...)
    values: List[float]  # Суммы
    currency: str = "RUB"


@router.get("/financial-chart", response_model=FinancialChartData)
async def get_financial_chart_data(current_user: User = Depends(get_current_user)):
    """Получить данные для графика выручки за последние 12 месяцев"""
    if current_user.role not in ["admin", "owner", "superadmin"]:
        raise HTTPException(status_code=403, detail="Access denied")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            school_id = None
            if current_user.role == "admin":
                await cursor.execute(
                    "SELECT school_id FROM school_admins WHERE admin_user_id = %s",
                    (current_user.id,)
                )
                row = await cursor.fetchone()
                if row:
                    school_id = row["school_id"] if isinstance(row, dict) else row[0]

            # Генерируем список последних 12 месяцев
            today = datetime.now()
            months = []
            for i in range(11, -1, -1):
                d = today - timedelta(days=i * 30)  # Приблизительно
                months.append(d.strftime("%Y-%m"))

            # SQL для группировки по месяцам
            if school_id:
                sql = """
                    SELECT DATE_FORMAT(bt.created_at, '%Y-%m') as month, SUM(bt.amount) as total
                    FROM balance_transactions bt
                    JOIN users u ON bt.user_id = u.id
                    JOIN class_students cs ON u.id = cs.student_id
                    JOIN classes c ON cs.class_id = c.id
                    WHERE bt.transaction_type IN ('payment', 'deposit')
                    AND c.school_id = %s
                    AND bt.created_at >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)
                    GROUP BY month
                    ORDER BY month
                """
                params = (school_id,)
            else:
                sql = """
                    SELECT DATE_FORMAT(created_at, '%Y-%m') as month, SUM(amount) as total
                    FROM balance_transactions
                    WHERE transaction_type IN ('payment', 'deposit')
                    AND created_at >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)
                    GROUP BY month
                    ORDER BY month
                """
                params = ()

            await cursor.execute(sql, params)
            rows = await cursor.fetchall()
            
            # Создаем словарь {месяц: сумма}
            data_map = {}
            for r in rows:
                m = r["month"] if isinstance(r, dict) else r[0]
                val = float(r["total"] if isinstance(r, dict) else r[1])
                data_map[m] = val

            # Формируем итоговые списки, заполняя нулями отсутствующие месяцы
            chart_labels = []
            chart_values = []
            
            # Mapping для названий месяцев (опционально можно локализовать на клиенте)
            month_names = {
                "01": "Янв", "02": "Фев", "03": "Мар", "04": "Апр",
                "05": "Май", "06": "Июн", "07": "Июл", "08": "Авг",
                "09": "Сен", "10": "Окт", "11": "Ноя", "12": "Дек"
            }

            for m_str in months:
                # m_str like "2023-10"
                parts = m_str.split("-")
                month_key = parts[1]
                label = f"{month_names.get(month_key, month_key)}"
                
                chart_labels.append(label)
                chart_values.append(data_map.get(m_str, 0.0))

            return FinancialChartData(
                labels=chart_labels,
                values=chart_values,
                currency="RUB"
            )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting financial chart: {str(e)}")
    finally:
        conn.close()
