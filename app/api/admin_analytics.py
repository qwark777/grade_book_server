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
            
            # 3. Ожидается оплат - сумма неоплаченных подписок (пока упрощенно - 0)
            # TODO: Реализовать расчет ожидаемых платежей на основе подписок
            pending_payments = 0.0
            
            # 4. Просрочено - сумма просроченных платежей (пока упрощенно - 0)
            # TODO: Реализовать расчет просроченных платежей
            overdue_payments = 0.0
            
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
