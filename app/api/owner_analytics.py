"""
API endpoints for owner analytics - schools requiring attention, recent events, licenses
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta

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
            
            # 2. Школы без расписания (проверяем наличие уроков в расписании)
            await cursor.execute("""
                SELECT DISTINCT s.id, s.name
                FROM schools s
                LEFT JOIN classes c ON c.school_id = s.id
                LEFT JOIN timetable_entries te ON te.class_id = c.id
                WHERE te.id IS NULL
                AND s.id NOT IN (
                    SELECT DISTINCT s2.id
                    FROM schools s2
                    JOIN classes c2 ON c2.school_id = s2.id
                    JOIN timetable_entries te2 ON te2.class_id = c2.id
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
                created_at = school.get("created_at") if isinstance(school, dict) else datetime.now()
                
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
                school_name = admin["school_name"] if isinstance(admin, dict) else admin[4]
                created_at = admin.get("created_at") if isinstance(admin, dict) else datetime.now()
                
                events.append(RecentEvent(
                    id=admin_id,
                    event_type="admin_invited",
                    title=f"Приглашён админ:",
                    subtitle=username,
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
            # Получаем все школы с количеством студентов и учителей
            await cursor.execute("""
                SELECT 
                    s.id,
                    s.name,
                    COUNT(DISTINCT cs.student_id) as students_count,
                    COUNT(DISTINCT ct.teacher_id) as teachers_count
                FROM schools s
                LEFT JOIN classes c ON c.school_id = s.id
                LEFT JOIN class_students cs ON cs.class_id = c.id
                LEFT JOIN class_teachers ct ON ct.class_id = c.id
                GROUP BY s.id, s.name
            """)
            schools = await cursor.fetchall()
            
            for school in schools:
                school_id = school["id"] if isinstance(school, dict) else school[0]
                school_name = school["name"] if isinstance(school, dict) else school[1]
                students_count = school["students_count"] if isinstance(school, dict) else school[2]
                teachers_count = school["teachers_count"] if isinstance(school, dict) else school[3]
                
                # Используем формулу: студенты + учителя = использованные лицензии
                # Предполагаем, что 1 лицензия = 1 пользователь
                used_licenses = (students_count or 0) + (teachers_count or 0)
                
                # Для упрощения считаем, что лимит = 20 * количество классов (или безлимит)
                # В реальности это должно браться из подписок
                await cursor.execute("""
                    SELECT COUNT(*) as class_count
                    FROM classes
                    WHERE school_id = %s
                """, (school_id,))
                class_count_row = await cursor.fetchone()
                class_count = class_count_row["class_count"] if isinstance(class_count_row, dict) else class_count_row[0]
                
                total_licenses = class_count * 20 if class_count > 0 else None
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
