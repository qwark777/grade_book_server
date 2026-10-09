"""
API endpoints for owner analytics - schools requiring attention, recent events, licenses
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
import psutil
import time

from app.core.config import settings
from app.core.security import get_current_user
from app.db.connection import get_db_connection

router = APIRouter(prefix="/owner/analytics")


class SchoolAttentionItem(BaseModel):
    """Школа, требующая внимания"""
    school_id: int
    school_name: str
    issue_type: str  # "no_admin", "no_schedule", "no_classes"
    issue_description: str


class RecentEvent(BaseModel):
    """Последнее событие"""
    id: int
    event_type: str  # "school_created", "admin_invited", "subscription_created"
    title: str
    subtitle: str
    created_at: datetime


class LicenseInfo(BaseModel):
    """Информация о лицензиях школы"""
    school_id: int
    school_name: str
    used_licenses: int
    total_licenses: Optional[int] = None  # None означает безлимит
    license_usage_percent: Optional[float] = None


class OwnerFinancialStats(BaseModel):
    """Сводка подписок для карточек владельца."""
    mrr_active: int
    mrr_total: Optional[int] = None
    churn_at_risk: int
    churn_total: int


class ServerStatus(BaseModel):
    """Статус сервера"""
    online: bool
    cpu_usage: float
    memory_usage: float
    memory_total: int
    memory_used: int
    uptime_seconds: float
    timestamp: datetime
    # Время запуска процесса сервера (приложения)
    process_start_time: Optional[datetime] = None
    process_uptime_seconds: Optional[float] = None


@router.get("/attention-required", response_model=List[SchoolAttentionItem])
async def get_schools_requiring_attention(current_user=Depends(get_current_user)):
    """Получить список школ, требующих внимания (owner only)"""
    role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")

    conn = await get_db_connection()
    items = []
    
    try:
        async with conn.cursor() as cursor:
            # 1. Школы без админа
            await cursor.execute("""
                SELECT s.id, s.name
                FROM schools s
                LEFT JOIN school_admins sa ON s.id = sa.school_id
                WHERE sa.school_id IS NULL
            """)
            no_admin_schools = await cursor.fetchall()
            
            for school in no_admin_schools:
                items.append(SchoolAttentionItem(
                    school_id=school["id"] if isinstance(school, dict) else school[0],
                    school_name=school["name"] if isinstance(school, dict) else school[1],
                    issue_type="no_admin",
                    issue_description="Нет назначенного админа"
                ))
            
            # 2. Школы без расписания
            await cursor.execute("""
                SELECT s.id, s.name
                FROM schools s
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM classes c
                    JOIN timetable_templates tt ON tt.class_id = c.id
                    WHERE c.school_id = s.id
                )
            """)
            no_schedule_schools = await cursor.fetchall()
            
            for school in no_schedule_schools:
                school_id = school["id"] if isinstance(school, dict) else school[0]
                school_name = school["name"] if isinstance(school, dict) else school[1]
                # Проверяем, не добавили ли уже эту школу (без админа)
                if not any(item.school_id == school_id for item in items):
                    items.append(SchoolAttentionItem(
                        school_id=school_id,
                        school_name=school_name,
                        issue_type="no_schedule",
                        issue_description="Нет расписания"
                    ))
            
            # 3. Школы без классов
            await cursor.execute("""
                SELECT s.id, s.name
                FROM schools s
                LEFT JOIN classes c ON c.school_id = s.id
                WHERE c.id IS NULL
            """)
            no_classes_schools = await cursor.fetchall()
            
            for school in no_classes_schools:
                school_id = school["id"] if isinstance(school, dict) else school[0]
                school_name = school["name"] if isinstance(school, dict) else school[1]
                # Проверяем, не добавили ли уже эту школу
                if not any(item.school_id == school_id for item in items):
                    items.append(SchoolAttentionItem(
                        school_id=school_id,
                        school_name=school_name,
                        issue_type="no_classes",
                        issue_description="Нет классов"
                    ))

            # 4. Школы с долгом или истекшей подпиской
            await cursor.execute("""
                SELECT s.id, s.name, ss.status, ss.current_period_end
                FROM schools s
                LEFT JOIN school_subscriptions ss ON ss.school_id = s.id
                WHERE ss.id IS NULL
                   OR ss.status IN ('past_due', 'suspended', 'canceled')
                   OR ss.current_period_end < DATE_SUB(NOW(), INTERVAL %s DAY)
            """, (settings.SUBSCRIPTION_GRACE_DAYS,))
            subscription_rows = await cursor.fetchall()

            for school in subscription_rows:
                school_id = school["id"] if isinstance(school, dict) else school[0]
                school_name = school["name"] if isinstance(school, dict) else school[1]
                status = school.get("status") if isinstance(school, dict) else school[2]
                if any(item.school_id == school_id for item in items):
                    continue
                description = "Нет подписки школы"
                if status in ("past_due", "suspended", "canceled"):
                    description = f"Подписка: {status}"
                elif status:
                    description = "Период подписки истёк"
                items.append(SchoolAttentionItem(
                    school_id=school_id,
                    school_name=school_name,
                    issue_type="subscription",
                    issue_description=description,
                ))
            
            return items[:10]  # Ограничиваем 10 элементами
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting attention required: {str(e)}")
    finally:
        conn.close()


@router.get("/recent-events", response_model=List[RecentEvent])
async def get_recent_events(
    limit: int = 10,
    current_user=Depends(get_current_user)
):
    """Получить последние события (owner only)"""
    role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")

    conn = await get_db_connection()
    events = []
    
    try:
        async with conn.cursor() as cursor:
            # 1. Созданные школы (используем created_at из таблицы schools, если есть, иначе id как порядковый номер)
            await cursor.execute("""
                SELECT id, name, created_at
                FROM schools
                ORDER BY id DESC
                LIMIT %s
            """, (limit,))
            schools = await cursor.fetchall()
            
            for school in schools:
                school_id = school["id"] if isinstance(school, dict) else school[0]
                school_name = school["name"] if isinstance(school, dict) else school[1]
                created_at = school.get("created_at") if isinstance(school, dict) else school[2]
                
                events.append(RecentEvent(
                    id=school_id,
                    event_type="school_created",
                    title=f"Создана школа: {school_name}",
                    subtitle="",
                    created_at=created_at if isinstance(created_at, datetime) else datetime.now()
                ))
            
            # 2. Приглашенные админы (используем created_at из users и связь через school_admins)
            await cursor.execute("""
                SELECT u.id, u.username, sa.school_id, s.name as school_name, u.created_at
                FROM users u
                JOIN school_admins sa ON u.id = sa.admin_user_id
                JOIN schools s ON sa.school_id = s.id
                WHERE u.role = 'admin'
                ORDER BY u.created_at DESC
                LIMIT %s
            """, (limit,))
            admins = await cursor.fetchall()
            
            for admin in admins:
                admin_id = admin["id"] if isinstance(admin, dict) else admin[0]
                username = admin["username"] if isinstance(admin, dict) else admin[1]
                school_name = admin["school_name"] if isinstance(admin, dict) else admin[3]
                created_at = admin.get("created_at") if isinstance(admin, dict) else admin[4]
                
                events.append(RecentEvent(
                    id=admin_id,
                    event_type="admin_invited",
                    title="Приглашён администратор",
                    subtitle=f"{username} · {school_name}",
                    created_at=created_at if isinstance(created_at, datetime) else datetime.now()
                ))
            
            # Сортируем по дате и берем последние
            events.sort(key=lambda x: x.created_at, reverse=True)
            return events[:limit]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting recent events: {str(e)}")
    finally:
        conn.close()


@router.get("/licenses", response_model=List[LicenseInfo])
async def get_licenses_info(current_user=Depends(get_current_user)):
    """Получить информацию о лицензиях всех школ (owner only)"""
    role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")

    conn = await get_db_connection()
    licenses = []
    
    try:
        async with conn.cursor() as cursor:
            # Лицензия — это место ученика или учителя в school_users.
            # Лимиты всегда берём из тарифа, а не выводим из числа классов.
            await cursor.execute("""
                SELECT 
                    s.id,
                    s.name,
                    COUNT(DISTINCT CASE WHEN su.role = 'student' THEN su.user_id END) AS students_count,
                    COUNT(DISTINCT CASE WHEN su.role = 'teacher' THEN su.user_id END) AS teachers_count,
                    ss.plan_id
                FROM schools s
                LEFT JOIN school_users su ON su.school_id = s.id
                LEFT JOIN school_subscriptions ss ON ss.school_id = s.id
                GROUP BY s.id, s.name, ss.plan_id
            """)
            schools = await cursor.fetchall()
            
            for school in schools:
                school_id = school["id"] if isinstance(school, dict) else school[0]
                school_name = school["name"] if isinstance(school, dict) else school[1]
                students_count = school["students_count"] if isinstance(school, dict) else school[2]
                teachers_count = school["teachers_count"] if isinstance(school, dict) else school[3]
                plan_id = school["plan_id"] if isinstance(school, dict) else school[4]
                
                # Используем формулу: студенты + учителя = использованные лицензии
                # Предполагаем, что 1 лицензия = 1 пользователь
                used_licenses = (students_count or 0) + (teachers_count or 0)
                
                total_licenses = None
                if plan_id is not None:
                    await cursor.execute(
                        """
                        SELECT entitlement_key, entitlement_value
                        FROM plan_entitlements
                        WHERE plan_id = %s
                          AND entitlement_key IN (
                              'students.max', 'students.unlimited',
                              'teachers.max', 'teachers.unlimited'
                          )
                        """,
                        (plan_id,),
                    )
                    entitlements = {
                        (row["entitlement_key"] if isinstance(row, dict) else row[0]):
                        (row["entitlement_value"] if isinstance(row, dict) else row[1])
                        for row in await cursor.fetchall()
                    }
                    unlimited = any(
                        str(entitlements.get(key, "")).strip().lower()
                        in {"1", "true", "yes", "unlimited"}
                        for key in ("students.unlimited", "teachers.unlimited")
                    )
                    if not unlimited:
                        try:
                            total_licenses = (
                                int(entitlements["students.max"])
                                + int(entitlements["teachers.max"])
                            )
                        except (KeyError, TypeError, ValueError):
                            total_licenses = None
                usage_percent = (used_licenses / total_licenses * 100) if total_licenses else None
                
                licenses.append(LicenseInfo(
                    school_id=school_id,
                    school_name=school_name,
                    used_licenses=used_licenses,
                    total_licenses=total_licenses,
                    license_usage_percent=usage_percent
                ))
            
            return licenses
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting licenses info: {str(e)}")
    finally:
        conn.close()


@router.get("/financial-stats", response_model=OwnerFinancialStats)
async def get_owner_financial_stats(current_user=Depends(get_current_user)):
    """Получить финансовую сводку по подпискам школ (owner only)."""
    role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT COUNT(*) AS total FROM schools")
            total_row = await cursor.fetchone()
            total_schools = int((total_row.get("total") if isinstance(total_row, dict) else total_row[0]) or 0)

            await cursor.execute(
                """
                SELECT
                    SUM(
                        CASE
                            WHEN ss.status IN ('active', 'trial')
                             AND ss.current_period_end >= DATE_SUB(NOW(), INTERVAL %s DAY)
                            THEN 1 ELSE 0
                        END
                    ) AS active_count,
                    SUM(
                        CASE
                            WHEN ss.id IS NULL
                              OR ss.status IN ('past_due', 'suspended', 'canceled')
                              OR ss.current_period_end < DATE_SUB(NOW(), INTERVAL %s DAY)
                            THEN 1 ELSE 0
                        END
                    ) AS at_risk_count
                FROM schools s
                LEFT JOIN school_subscriptions ss ON ss.school_id = s.id
                """,
                (settings.SUBSCRIPTION_GRACE_DAYS, settings.SUBSCRIPTION_GRACE_DAYS),
            )
            stats_row = await cursor.fetchone()
            active_count = int((stats_row.get("active_count") if isinstance(stats_row, dict) else stats_row[0]) or 0)
            at_risk_count = int((stats_row.get("at_risk_count") if isinstance(stats_row, dict) else stats_row[1]) or 0)

            return OwnerFinancialStats(
                mrr_active=active_count,
                mrr_total=total_schools,
                churn_at_risk=at_risk_count,
                churn_total=total_schools,
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting owner financial stats: {str(e)}")
    finally:
        conn.close()


@router.get("/server-status", response_model=ServerStatus)
async def get_server_status(current_user=Depends(get_current_user)):
    """Получить статус сервера (owner only)"""
    role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        # CPU usage
        cpu_percent = psutil.cpu_percent(interval=None)
        
        # Memory usage
        memory = psutil.virtual_memory()
        
        # Uptime системы (с момента загрузки ОС)
        boot_time = psutil.boot_time()
        uptime = time.time() - boot_time

        # Время запуска и аптайм процесса сервера (приложения)
        process_start_time = None
        process_uptime_seconds = None
        try:
            p = psutil.Process()
            process_start_time = datetime.fromtimestamp(p.create_time())
            process_uptime_seconds = time.time() - p.create_time()
        except Exception:
            pass
        
        return ServerStatus(
            online=True,
            cpu_usage=cpu_percent,
            memory_usage=memory.percent,
            memory_total=memory.total,
            memory_used=memory.used,
            uptime_seconds=uptime,
            timestamp=datetime.now(),
            process_start_time=process_start_time,
            process_uptime_seconds=process_uptime_seconds,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting server status: {str(e)}")
