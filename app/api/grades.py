import os
import uuid
from datetime import date as date_type
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from starlette.responses import FileResponse

from app.core.security import get_current_user
from app.core.config import settings
from app.core.entitlements import require_entitlement, get_school_id_for_user
from app.db.connection import get_db_connection
from app.models.grade import (
    ClassItem, AttendanceStats, AttendanceHeatmapData, AttendanceDetail,
    GradeDistribution, PerformanceTrend, SubjectPerformance, Subject
)
from app.models.user import User
from pydantic import BaseModel

router = APIRouter()


async def check_analytics_access(current_user: User, school_id: int = None, student_id: int = None):
    """Check if user has access to analytics features"""
    # Students can always view their own analytics data
    if current_user.role == "student" and student_id is not None and current_user.id == student_id:
        return True
    
    if current_user.role == "owner":
        return True  # Owner has access to everything
    
    if not school_id:
        school_id = await get_school_id_for_user(current_user)
    
    if not school_id:
        raise HTTPException(status_code=403, detail="No school access")
    
    # Check if school has analytics entitlement
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT pe.entitlement_value
                FROM school_subscriptions ss
                JOIN plan_entitlements pe ON ss.plan_id = pe.plan_id
                WHERE ss.school_id = %s 
                AND pe.entitlement_key = 'analytics.full'
                AND ss.status IN ('active', 'trial')
            """, (school_id,))
            
            result = await cursor.fetchone()
            if not result or result["entitlement_value"].lower() != "true":
                raise HTTPException(
                    status_code=402, 
                    detail="Analytics features require Pro or Enterprise plan"
                )
    finally:
        conn.close()


class GradeCreate(BaseModel):
    student_id: int
    subject: str
    value: int
    date: str


class GradeBulkCreate(BaseModel):
    subject: str
    date: str
    grades: List[GradeCreate]


class AttendanceEntry(BaseModel):
    student_id: int
    status: str  # "present" | "late" | "absent"


class AttendanceBulkCreate(BaseModel):
    date: str
    class_id: int
    lesson_number: int
    subject_id: int
    entries: List[AttendanceEntry]


class HomeworkCreate(BaseModel):
    class_id: int
    subject_id: int
    due_date: str
    description: str


class HomeworkSubmissionUpdate(BaseModel):
    grade: Optional[int] = None
    is_reviewed: Optional[bool] = None
    comment: Optional[str] = None


class ClassTeacherAssign(BaseModel):
    teacher_id: int
    subject_id: int


class StudentTransfer(BaseModel):
    student_id: int
    from_class_id: int
    to_class_id: int


class BulkStudentTransfer(BaseModel):
    from_class_id: int
    to_class_id: int
    student_ids: List[int]


def _require_admin(current_user):
    role = current_user.get("role", "") if isinstance(current_user, dict) else getattr(current_user, "role", "")
    if role not in ("admin", "owner", "superadmin"):
        raise HTTPException(status_code=403, detail="Только админ может выполнить это действие")


@router.get("/classes", response_model=List[ClassItem])
async def get_classes(
    include_archived: bool = False,
    current_user=Depends(get_current_user)
):
    """Get all classes with student count. For admin - only classes of their school. By default excludes archived."""
    conn = await get_db_connection()
    try:
        school_id = await get_school_id_for_user(current_user)
        async with conn.cursor() as cursor:
            archive_filter = "" if include_archived else " AND COALESCE(c.is_archived, 0) = 0"
            if school_id is not None:
                await cursor.execute(f"""
                    SELECT c.id, c.name,
                           (SELECT COUNT(*) FROM class_students cs WHERE cs.class_id = c.id) AS student_count,
                           COALESCE(c.is_archived, 0) AS is_archived
                    FROM classes c
                    WHERE c.school_id = %s{archive_filter}
                    ORDER BY COALESCE(c.is_archived, 0), c.name
                """, (school_id,))
            else:
                await cursor.execute(f"""
                    SELECT c.id, c.name,
                           (SELECT COUNT(*) FROM class_students cs WHERE cs.class_id = c.id) AS student_count,
                           COALESCE(c.is_archived, 0) AS is_archived
                    FROM classes c
                    WHERE 1=1{archive_filter}
                    ORDER BY COALESCE(c.is_archived, 0), c.name
                """)
            return await cursor.fetchall()
    finally:
        conn.close()


@router.put("/classes/{class_id}/archive")
async def archive_class(class_id: int, archived: bool = True, current_user=Depends(get_current_user)):
    """Archive or unarchive a class. Admin only."""
    role = current_user.get("role", "") if isinstance(current_user, dict) else getattr(current_user, "role", "")
    if role not in ("admin", "owner", "superadmin"):
        raise HTTPException(403, "Forbidden")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE classes SET is_archived = %s WHERE id = %s",
                (1 if archived else 0, class_id)
            )
            await conn.commit()
            if cursor.rowcount == 0:
                raise HTTPException(404, "Class not found")
            try:
                uid = current_user.get("id") if isinstance(current_user, dict) else getattr(current_user, "id", None)
                await cursor.execute("SELECT school_id FROM classes WHERE id = %s", (class_id,))
                row = await cursor.fetchone()
                school_id = row.get("school_id") if row and isinstance(row, dict) else (row[0] if row else None)
                await cursor.execute(
                    """INSERT INTO audit_log (school_id, user_id, action, entity_type, entity_id, details)
                       VALUES (%s, %s, %s, 'class', %s, NULL)""",
                    (school_id, uid, "class_archived" if archived else "class_unarchived", class_id)
                )
                await conn.commit()
            except Exception:
                pass
            return {"status": "ok", "archived": archived}
    finally:
        conn.close()


@router.get("/subjects", response_model=List[str])
async def list_subjects(current_user=Depends(get_current_user)):
    """Return list of all subject names."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT name FROM subjects ORDER BY name")
            rows = await cursor.fetchall()
            return [r["name"] if isinstance(r, dict) else r[0] for r in rows]
    finally:
        conn.close()


@router.get("/subjects-with-coefficients", response_model=List[Subject])
async def list_subjects_with_coefficients(current_user=Depends(get_current_user)):
    """Return list of all subjects with their coefficients."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT id, name, coefficient FROM subjects ORDER BY name")
            rows = await cursor.fetchall()
            return [
                Subject(
                    id=r["id"] if isinstance(r, dict) else r[0],
                    name=r["name"] if isinstance(r, dict) else r[1],
                    coefficient=float(r["coefficient"] if isinstance(r, dict) else r[2])
                ) for r in rows
            ]
    finally:
        conn.close()


class SubjectCoefficientUpdate(BaseModel):
    coefficient: float


class SubjectCreate(BaseModel):
    name: str
    coefficient: float = 1.0


@router.post("/subjects", status_code=201)
async def create_subject(
    subject_data: SubjectCreate,
    current_user=Depends(get_current_user)
):
    """Create a new subject with coefficient"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем, существует ли предмет
            await cursor.execute("SELECT id FROM subjects WHERE name = %s", (subject_data.name,))
            existing = await cursor.fetchone()
            
            if existing:
                raise HTTPException(status_code=400, detail=f"Subject '{subject_data.name}' already exists")
            
            # Создаем предмет
            await cursor.execute(
                "INSERT INTO subjects (name, coefficient) VALUES (%s, %s)",
                (subject_data.name, subject_data.coefficient)
            )
            await conn.commit()
            
            return {"message": f"Subject '{subject_data.name}' created with coefficient {subject_data.coefficient}"}
    finally:
        conn.close()


@router.put("/subjects/{subject_name}/coefficient")
async def update_subject_coefficient(
    subject_name: str, 
    update_data: SubjectCoefficientUpdate,
    current_user=Depends(get_current_user)
):
    """Update coefficient for a specific subject."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем, существует ли предмет
            await cursor.execute("SELECT id FROM subjects WHERE name = %s", (subject_name,))
            subject = await cursor.fetchone()
            
            if not subject:
                raise HTTPException(status_code=404, detail="Subject not found")
            
            # Обновляем коэффициент
            await cursor.execute(
                "UPDATE subjects SET coefficient = %s WHERE name = %s",
                (update_data.coefficient, subject_name)
            )
            await conn.commit()
            
            return {"message": f"Coefficient for subject '{subject_name}' updated to {update_data.coefficient}"}
    finally:
        conn.close()


@router.get("/points/table")
async def get_points_table(current_user=Depends(get_current_user)):
    """Get the points table showing how grades are converted to points."""
    from app.utils.points_calculator import PointsCalculator
    
    return {
        "points_table": PointsCalculator.get_points_table(),
        "description": "Баллы рассчитываются как: базовый_балл × коэффициент_предмета"
    }


@router.get("/students/points-leaderboard", response_model=List[dict])
async def get_students_points_leaderboard(current_user=Depends(get_current_user)):
    """Get students leaderboard based on calculated points from grades with subject coefficients."""
    from app.utils.points_calculator import PointsCalculator
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Получаем все оценки студентов с предметами
            await cursor.execute("""
                SELECT 
                    u.id AS student_id,
                    p.full_name AS student_name,
                    s.name AS subject,
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
                        'student_name': row['student_name'],
                        'grades': []
                    }
                student_grades[student_id]['grades'].append({
                    'subject': row['subject'],
                    'grade': row['grade']
                })
            
            # Рассчитываем баллы для каждого студента
            result = []
            for student_id, data in student_grades.items():
                total_points = 0
                grades_data = []
                
                for grade_info in data['grades']:
                    # Получаем коэффициент предмета
                    coefficient = await PointsCalculator.get_subject_coefficient(grade_info['subject'])
                    
                    # Рассчитываем баллы
                    points = PointsCalculator.calculate_points_for_grade(grade_info['grade'], coefficient)
                    total_points += points
                    
                    grades_data.append({
                        'subject': grade_info['subject'],
                        'grade': grade_info['grade'],
                        'coefficient': coefficient,
                        'points': points
                    })
                
                result.append({
                    'student_id': student_id,
                    'student_name': data['student_name'],
                    'total_points': total_points,
                    'grades': grades_data
                })
            
            # Сортируем по убыванию баллов
            result.sort(key=lambda x: x['total_points'], reverse=True)
            
            return result
            
    finally:
        conn.close()


@router.get("/teachers/rating-by-grades", response_model=List[dict])
async def get_teachers_rating_by_grades(current_user=Depends(get_current_user)):
    """Рейтинг учителей по среднему баллу выставленных оценок."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT 
                    u.id AS teacher_id,
                    COALESCE(p.full_name, CONCAT('Учитель ', u.id)) AS teacher_name,
                    ROUND(AVG(g.value), 2) AS average_grade,
                    COUNT(*) AS grade_count
                FROM grades g
                JOIN users u ON u.id = g.teacher_id AND u.role = 'teacher'
                LEFT JOIN profiles p ON p.user_id = u.id
                GROUP BY u.id, p.full_name
                HAVING COUNT(*) > 0
                ORDER BY average_grade DESC, grade_count DESC
            """)
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/teacher/classes")
async def get_teacher_classes(current_user=Depends(get_current_user)):
    """Classes + subjects assigned to teacher (class_subject_teachers). Fallback to class_teachers if empty."""
    teacher_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Сначала пробуем class_subject_teachers (учитель — предмет по классу)
            await cursor.execute(
                """
                SELECT c.id, c.name, s.id AS subject_id, s.name AS subject_name,
                       (SELECT COUNT(*) FROM class_students cs WHERE cs.class_id = c.id) AS student_count,
                       (SELECT ROUND(AVG(g.value), 2)
                        FROM grades g
                        JOIN class_students cs ON g.student_id = cs.student_id
                        WHERE cs.class_id = c.id AND g.subject_id = s.id
                        AND g.date >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)) AS avg_grade
                FROM class_subject_teachers cst
                JOIN classes c ON c.id = cst.class_id
                JOIN subjects s ON s.id = cst.subject_id
                WHERE cst.teacher_id = %s
                ORDER BY (avg_grade IS NULL), avg_grade DESC, c.name, s.name
                """,
                (teacher_id,)
            )
            rows = await cursor.fetchall()
            if rows:
                result = []
                for r in rows:
                    avg = r.get("avg_grade") if isinstance(r, dict) else (r[4] if len(r) > 4 else None)
                    result.append({
                        "id": r["id"] if isinstance(r, dict) else r[0],
                        "name": r["name"] if isinstance(r, dict) else r[1],
                        "subject_id": r["subject_id"] if isinstance(r, dict) else r[2],
                        "subject_name": r["subject_name"] if isinstance(r, dict) else r[3],
                        "student_count": r["student_count"] if isinstance(r, dict) else r[4],
                        "avg_grade": float(avg) if avg is not None else None,
                    })
                return result
            # Fallback 2: class_teachers без предметов (старые данные)
            await cursor.execute(
                """
                SELECT c.id, c.name,
                       (SELECT COUNT(*) FROM class_students cs WHERE cs.class_id = c.id) AS student_count,
                       (SELECT ROUND(AVG(g.value), 2)
                        FROM grades g
                        JOIN class_students cs ON g.student_id = cs.student_id
                        WHERE cs.class_id = c.id AND g.date >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)) AS avg_grade
                FROM class_teachers ct
                JOIN classes c ON c.id = ct.class_id
                WHERE ct.teacher_id = %s
                ORDER BY (avg_grade IS NULL), avg_grade DESC, c.name
                """,
                (teacher_id,)
            )
            rows = await cursor.fetchall()
            if rows:
                result = []
                for r in rows:
                    avg = r.get("avg_grade") if isinstance(r, dict) else (r[3] if len(r) > 3 else None)
                    result.append({
                        "id": r["id"] if isinstance(r, dict) else r[0],
                        "name": r["name"] if isinstance(r, dict) else r[1],
                        "subject_id": None,
                        "subject_name": None,
                        "student_count": r["student_count"] if isinstance(r, dict) else r[2],
                        "avg_grade": float(avg) if avg is not None else None,
                    })
                return result
            # Fallback 3: teacher_subjects + class_subjects — классы, где есть предметы учителя
            await cursor.execute(
                """
                SELECT DISTINCT c.id, c.name, s.id AS subject_id, s.name AS subject_name,
                       (SELECT COUNT(*) FROM class_students cs WHERE cs.class_id = c.id) AS student_count,
                       (SELECT ROUND(AVG(g.value), 2)
                        FROM grades g
                        JOIN class_students cs ON g.student_id = cs.student_id
                        WHERE cs.class_id = c.id AND g.subject_id = s.id
                        AND g.date >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)) AS avg_grade
                FROM teacher_subjects ts
                JOIN class_subjects csb ON csb.subject_id = ts.subject_id
                JOIN classes c ON c.id = csb.class_id
                JOIN subjects s ON s.id = ts.subject_id
                WHERE ts.teacher_id = %s
                ORDER BY (avg_grade IS NULL), avg_grade DESC, c.name, s.name
                """,
                (teacher_id,)
            )
            rows = await cursor.fetchall()
            result = []
            seen = set()
            for r in rows:
                cid = r["id"] if isinstance(r, dict) else r[0]
                sid = r["subject_id"] if isinstance(r, dict) else r[2]
                key = (cid, sid)
                if key in seen:
                    continue
                seen.add(key)
                avg = r.get("avg_grade") if isinstance(r, dict) else (r[5] if len(r) > 5 else None)
                result.append({
                    "id": cid,
                    "name": r["name"] if isinstance(r, dict) else r[1],
                    "subject_id": sid,
                    "subject_name": r["subject_name"] if isinstance(r, dict) else r[3],
                    "student_count": r["student_count"] if isinstance(r, dict) else r[4],
                    "avg_grade": float(avg) if avg is not None else None,
                })
            return result
    finally:
        conn.close()


def _default_semester_bounds():
    """По умолчанию: 1-й семестр янв—июнь, 2-й семестр сен—янв."""
    today = date_type.today()
    if today.month <= 6:
        return f"{today.year}-01-01", f"{today.year}-06-01"
    return f"{today.year}-09-01", f"{today.year + 1}-01-01"


async def _get_current_academic_period_bounds():
    """Возвращает (start_date, end_date). Если период в прошлом — умолчание."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT start_date, end_date FROM academic_periods
                WHERE is_active = TRUE ORDER BY end_date DESC LIMIT 1
                """
            )
            row = await cursor.fetchone()
            if not row:
                await cursor.execute(
                    "SELECT start_date, end_date FROM academic_periods ORDER BY end_date DESC LIMIT 1"
                )
                row = await cursor.fetchone()
            if row:
                start = str(row["start_date"])
                end = str(row["end_date"])
                end_val = row["end_date"]
                if end_val is not None:
                    try:
                        d = end_val if isinstance(end_val, date_type) else date_type.fromisoformat(str(end_val)[:10])
                        if d < date_type.today():
                            return _default_semester_bounds()
                    except (TypeError, ValueError):
                        pass
                return start, end
    finally:
        conn.close()
    return _default_semester_bounds()


@router.get("/classes/{class_id}/grades-export")
async def get_class_grades_export(
    class_id: int,
    subject_id: Optional[int] = Query(None, description="Фильтр по предмету"),
    date_from: Optional[str] = Query(None, description="Начало периода (yyyy-MM-dd)"),
    date_to: Optional[str] = Query(None, description="Конец периода (yyyy-MM-dd)"),
    current_user=Depends(get_current_user)
):
    """Оценки класса для экспорта (ученик, дата, оценка). Для учителя — только свои предметы.
    При фильтре «Все»: даты ограничены текущим семестром (конец задаёт админ)."""
    teacher_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    # Для «всех» оценок — ограничиваем периодом семестра
    if date_from is None and date_to is None:
        period_start, period_end = await _get_current_academic_period_bounds()
        if period_start:
            date_from = period_start
        if period_end:
            date_to = period_end
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            subject_filter = "AND g.subject_id = %s" if subject_id else ""
            date_filter = ""
            params = [class_id]
            if subject_id:
                params.append(subject_id)
            if date_from:
                date_filter += " AND g.date >= %s"
                params.append(date_from)
            if date_to:
                date_filter += " AND g.date <= %s"
                params.append(date_to)
            # Проверка доступа учителя (class_subject_teachers)
            role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
            if role == "teacher":
                await cursor.execute(
                    """SELECT 1 FROM class_subject_teachers cst
                       WHERE cst.class_id = %s AND cst.teacher_id = %s
                       """ + ("AND cst.subject_id = %s" if subject_id else ""),
                    [class_id, teacher_id] + ([subject_id] if subject_id else [])
                )
                if not await cursor.fetchone():
                    raise HTTPException(status_code=403, detail="Нет доступа к этому классу/предмету")
            await cursor.execute(
                f"""
                SELECT p.full_name as student_name, cs.student_id, g.id as grade_id, g.date, g.value, s.name as subject_name
                FROM class_students cs
                JOIN profiles p ON p.user_id = cs.student_id
                JOIN grades g ON g.student_id = cs.student_id
                JOIN subjects s ON s.id = g.subject_id
                WHERE cs.class_id = %s {subject_filter} {date_filter}
                ORDER BY p.full_name, g.date
                """,
                tuple(params)
            )
            rows = await cursor.fetchall()
            by_student = {}
            for r in rows:
                name = r["student_name"] or "—"
                student_id = r.get("student_id") or r["student_id"]
                if name not in by_student:
                    by_student[name] = {"student_name": name, "student_id": student_id, "grades": [], "values": []}
                by_student[name]["grades"].append({
                    "id": r.get("grade_id"),
                    "date": str(r["date"]) if r["date"] else "",
                    "value": r["value"],
                    "subject": r.get("subject_name", ""),
                })
                by_student[name]["values"].append(r["value"])
            result = []
            for name, data in sorted(by_student.items()):
                avg = round(sum(data["values"]) / len(data["values"]), 2) if data["values"] else None
                result.append({
                    "student_name": name,
                    "grades": data["grades"],
                    "average": avg,
                })
            return result
    finally:
        conn.close()


@router.get("/classes/{class_id}/students-performance")
async def get_class_students_performance(
    class_id: int,
    subject_id: Optional[int] = Query(None, description="Фильтр по предмету"),
    current_user=Depends(get_current_user)
):
    """Get performance data (current and previous average) for all students in a class, optionally by subject"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # First get all students in the class (from class_students table)
            # Use LEFT JOIN for profiles in case student doesn't have profile
            await cursor.execute("""
                SELECT 
                    cs.student_id as student_id,
                    COALESCE(p.full_name, CONCAT('Student ', cs.student_id)) as student_name,
                    COALESCE(p.photo_url, CONCAT('/profile_photo/', cs.student_id)) AS photo_url,
                    c.name as class_name
                FROM class_students cs
                JOIN classes c ON c.id = cs.class_id
                LEFT JOIN users u ON u.id = cs.student_id
                LEFT JOIN profiles p ON p.user_id = cs.student_id
                WHERE cs.class_id = %s
                ORDER BY COALESCE(p.full_name, CONCAT('Student ', cs.student_id))
            """, (class_id,))
            students = await cursor.fetchall()
            
            # Debug: Check what students are in class_students for this class
            await cursor.execute("""
                SELECT student_id FROM class_students WHERE class_id = %s
            """, (class_id,))
            class_student_ids = await cursor.fetchall()
            print(f"[DEBUG] Students in class_students for class {class_id}: {[s['student_id'] for s in class_student_ids]}")
            
            # Debug: Check what grades exist for this class
            await cursor.execute("""
                SELECT g.student_id, g.date, g.value, COUNT(*) as cnt
                FROM grades g
                JOIN class_students cs ON g.student_id = cs.student_id
                WHERE cs.class_id = %s
                GROUP BY g.student_id, g.date, g.value
                ORDER BY g.date DESC
                LIMIT 20
            """, (class_id,))
            debug_grades = await cursor.fetchall()
            print(f"[DEBUG] Found {len(debug_grades)} grade records for class {class_id}")
            for g in debug_grades[:5]:  # Show first 5
                print(f"  - Student {g['student_id']}: date={g['date']}, value={g['value']}")
            
            # Debug: Check which students will be returned by the query
            print(f"[DEBUG] Students returned by query: {[s['student_id'] for s in students]}")
            
            # Use EXACT same logic as admin_analytics for class-level calculation
            # Only count grades from students (u.role = 'student'), not teachers
            await cursor.execute("""
                SELECT 
                    AVG(g.value) as current_avg,
                    (SELECT AVG(g2.value)
                     FROM grades g2
                     JOIN class_students cs2 ON g2.student_id = cs2.student_id
                     JOIN users u2 ON u2.id = g2.student_id AND u2.role = 'student'
                     WHERE cs2.class_id = %s
                     AND g2.date >= DATE_SUB(CURDATE(), INTERVAL 60 DAY)
                     AND g2.date < DATE_SUB(CURDATE(), INTERVAL 30 DAY)) as previous_avg
                FROM classes c
                JOIN class_students cs ON c.id = cs.class_id
                JOIN grades g ON cs.student_id = g.student_id
                JOIN users u ON u.id = g.student_id AND u.role = 'student'
                WHERE c.id = %s
                AND g.date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                GROUP BY c.id
            """, (class_id, class_id))
            class_avg_row = await cursor.fetchone()
            print(f"[DEBUG] Class-level: current_avg={class_avg_row.get('current_avg') if class_avg_row else None}, previous_avg={class_avg_row.get('previous_avg') if class_avg_row else None}")
            
            result = []
            print(f"[DEBUG] Processing {len(students)} students for class {class_id}")
            for student in students:
                student_id = student["student_id"]
                print(f"[DEBUG] Processing student {student_id} ({student['student_name']})")
                
                # Get current period average (last 30 days)
                grade_filter = "AND g.subject_id = %s" if subject_id else ""
                params_curr = [class_id, student_id]
                if subject_id:
                    params_curr.append(subject_id)
                await cursor.execute(
                    f"""
                    SELECT AVG(g.value) as avg_value, COUNT(*) as count
                    FROM grades g
                    JOIN class_students cs ON g.student_id = cs.student_id
                    WHERE cs.class_id = %s
                    AND g.student_id = %s
                    AND g.date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                    {grade_filter}
                    """,
                    tuple(params_curr)
                )
                current_row = await cursor.fetchone()
                current_avg_val = current_row.get("avg_value") if current_row else None
                current_avg = float(current_avg_val) if current_avg_val is not None else None
                current_count = current_row.get("count", 0) if current_row else 0
                
                # Get previous period average (30-60 days ago)
                params_prev = [class_id, student_id]
                if subject_id:
                    params_prev.append(subject_id)
                await cursor.execute(
                    f"""
                    SELECT AVG(g.value) as avg_value, COUNT(*) as count
                    FROM grades g
                    JOIN class_students cs ON g.student_id = cs.student_id
                    WHERE cs.class_id = %s
                    AND g.student_id = %s
                    AND g.date >= DATE_SUB(CURDATE(), INTERVAL 60 DAY)
                    AND g.date < DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                    {grade_filter}
                    """,
                    tuple(params_prev)
                )
                previous_row = await cursor.fetchone()
                
                previous_avg_val = previous_row.get("avg_value") if previous_row else None
                previous_avg = float(previous_avg_val) if previous_avg_val is not None else None
                previous_count = previous_row.get("count", 0) if previous_row else 0
                
                # Debug logging
                print(f"[DEBUG] Student {student_id} ({student['student_name']}): "
                      f"current_avg={current_avg} (count={current_count}), "
                      f"previous_avg={previous_avg} (count={previous_count})")
                
                # Calculate change (decline/improvement) if both periods have data
                # decline can be positive (decline), negative (improvement), or 0 (no change)
                decline = None
                if current_avg is not None and previous_avg is not None:
                    decline = previous_avg - current_avg  # positive = decline, negative = improvement
                    print(f"[DEBUG] Student {student_id} decline calculated: {decline}")
                
                # Include all students with their change values (even if 0 or negative)
                result.append({
                    "student_id": student_id,
                    "student_name": student["student_name"],
                    "photo_url": student["photo_url"],
                    "class_name": student.get("class_name"),
                    "current_avg": current_avg,
                    "previous_avg": previous_avg,
                    "decline": decline  # Can be positive, negative, 0, or None
                })
            
            return result
    finally:
        conn.close()


@router.get("/subjects/{subject_id}/teachers")
async def get_teachers_by_subject(subject_id: int, current_user=Depends(get_current_user)):
    """Список учителей, ведущих предмет (для назначения на класс)."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT u.id as teacher_id, TRIM(BOTH '"' FROM p.full_name) as teacher_name
                FROM teacher_subjects ts
                JOIN users u ON u.id = ts.teacher_id AND u.role = 'teacher'
                JOIN profiles p ON p.user_id = u.id
                WHERE ts.subject_id = %s
                ORDER BY p.full_name
                """,
                (subject_id,),
            )
            rows = await cursor.fetchall()
            return [{"teacher_id": r["teacher_id"], "teacher_name": r.get("teacher_name", "")} for r in rows]
    finally:
        conn.close()


@router.get("/classes/{class_id}/teachers")
async def get_class_teachers(class_id: int, current_user=Depends(get_current_user)):
    """Список учителей класса с предметами (для админа)."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT cst.teacher_id, cst.subject_id,
                       TRIM(BOTH '"' FROM p.full_name) as teacher_name, s.name as subject_name
                FROM class_subject_teachers cst
                JOIN profiles p ON p.user_id = cst.teacher_id
                JOIN subjects s ON s.id = cst.subject_id
                WHERE cst.class_id = %s
                ORDER BY s.name, p.full_name
                """,
                (class_id,),
            )
            rows = await cursor.fetchall()
            return [{"teacher_id": r["teacher_id"], "subject_id": r["subject_id"], "teacher_name": r.get("teacher_name", ""), "subject_name": r.get("subject_name", "")} for r in rows]
    finally:
        conn.close()


@router.post("/classes/{class_id}/teachers")
async def assign_teacher_to_class(
    class_id: int,
    payload: ClassTeacherAssign,
    current_user=Depends(get_current_user),
):
    """Назначить учителя на предмет в классе (только админ)."""
    _require_admin(current_user)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT 1 FROM class_subjects WHERE class_id = %s AND subject_id = %s",
                (class_id, payload.subject_id),
            )
            if not await cursor.fetchone():
                raise HTTPException(status_code=400, detail="Предмет не добавлен в класс")
            await cursor.execute(
                "SELECT 1 FROM teacher_subjects WHERE teacher_id = %s AND subject_id = %s",
                (payload.teacher_id, payload.subject_id),
            )
            if not await cursor.fetchone():
                raise HTTPException(status_code=400, detail="Учитель не ведёт этот предмет")
            await cursor.execute(
                "INSERT IGNORE INTO class_subject_teachers (class_id, subject_id, teacher_id) VALUES (%s, %s, %s)",
                (class_id, payload.subject_id, payload.teacher_id),
            )
            await conn.commit()
        return {"status": "ok", "message": "Учитель назначен"}
    finally:
        conn.close()


@router.delete("/classes/{class_id}/teachers/{teacher_id}/subjects/{subject_id}")
async def remove_teacher_from_class(
    class_id: int, teacher_id: int, subject_id: int,
    current_user=Depends(get_current_user),
):
    """Снять учителя с предмета в классе (только админ)."""
    _require_admin(current_user)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM class_subject_teachers WHERE class_id = %s AND teacher_id = %s AND subject_id = %s",
                (class_id, teacher_id, subject_id),
            )
            await conn.commit()
        return {"status": "ok", "message": "Назначение снято"}
    finally:
        conn.close()


@router.post("/classes/transfer-student")
async def transfer_student_between_classes(
    payload: StudentTransfer,
    current_user=Depends(get_current_user),
):
    """Перевести ученика из одного класса в другой (только админ)."""
    _require_admin(current_user)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT 1 FROM class_students WHERE class_id = %s AND student_id = %s",
                (payload.from_class_id, payload.student_id),
            )
            if not await cursor.fetchone():
                raise HTTPException(status_code=400, detail="Ученик не найден в исходном классе")
            await cursor.execute(
                "DELETE FROM class_students WHERE class_id = %s AND student_id = %s",
                (payload.from_class_id, payload.student_id),
            )
            await cursor.execute(
                "INSERT IGNORE INTO class_students (class_id, student_id) VALUES (%s, %s)",
                (payload.to_class_id, payload.student_id),
            )
            await conn.commit()
        return {"status": "ok", "message": "Ученик переведён"}
    finally:
        conn.close()


@router.post("/classes/transfer-students-bulk")
async def transfer_students_bulk(
    payload: BulkStudentTransfer,
    current_user=Depends(get_current_user),
):
    """Массовый перевод учеников из одного класса в другой (только админ)."""
    _require_admin(current_user)
    if payload.from_class_id == payload.to_class_id:
        raise HTTPException(status_code=400, detail="Исходный и целевой класс должны отличаться")
    if not payload.student_ids:
        raise HTTPException(status_code=400, detail="Укажите хотя бы одного ученика")
    conn = await get_db_connection()
    transferred = 0
    try:
        async with conn.cursor() as cursor:
            for sid in payload.student_ids:
                await cursor.execute(
                    "SELECT 1 FROM class_students WHERE class_id = %s AND student_id = %s",
                    (payload.from_class_id, sid),
                )
                if not await cursor.fetchone():
                    continue
                await cursor.execute(
                    "DELETE FROM class_students WHERE class_id = %s AND student_id = %s",
                    (payload.from_class_id, sid),
                )
                await cursor.execute(
                    "INSERT IGNORE INTO class_students (class_id, student_id) VALUES (%s, %s)",
                    (payload.to_class_id, sid),
                )
                transferred += 1
            await conn.commit()
        return {"status": "ok", "message": f"Переведено {transferred} учеников", "transferred": transferred}
    finally:
        conn.close()


@router.get("/classes/{class_id}/students")
async def get_class_students(class_id: int, current_user=Depends(get_current_user)):
    """Students in the given class"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT u.id, p.full_name, p.photo_url, c.name as class_name
                FROM class_students cs
                JOIN users u ON u.id = cs.student_id AND u.role = 'student'
                JOIN profiles p ON p.user_id = u.id
                JOIN classes c ON c.id = cs.class_id
                WHERE cs.class_id = %s
                ORDER BY p.full_name
                """,
                (class_id,)
            )
            return await cursor.fetchall()
    finally:
        conn.close()


@router.get("/teacher/subjects", response_model=List[str])
async def get_teacher_subjects(current_user=Depends(get_current_user)):
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT s.name FROM teacher_subjects ts
                JOIN subjects s ON s.id = ts.subject_id
                WHERE ts.teacher_id = %s
                ORDER BY s.name
                """,
                (current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None),)
            )
            rows = await cursor.fetchall()
            return [r["name"] if isinstance(r, dict) else r[0] for r in rows]
    finally:
        conn.close()


@router.get("/classes/{class_id}/subjects-with-ids")
async def get_class_subjects_with_ids(class_id: int, current_user=Depends(get_current_user)):
    """Предметы класса с ID (для выбора при переходе в журнал)."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT s.id, s.name FROM class_subjects cs
                JOIN subjects s ON s.id = cs.subject_id
                WHERE cs.class_id = %s
                ORDER BY s.name
                """,
                (class_id,),
            )
            rows = await cursor.fetchall()
            return [{"id": r["id"], "name": r["name"]} for r in rows]
    finally:
        conn.close()


@router.get("/classes/{class_id}/subjects", response_model=List[str])
async def get_class_subjects(class_id: int, current_user=Depends(get_current_user)):
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT s.name FROM class_subjects cs
                JOIN subjects s ON s.id = cs.subject_id
                WHERE cs.class_id = %s
                ORDER BY s.name
                """,
                (class_id,)
            )
            rows = await cursor.fetchall()
            return [r["name"] if isinstance(r, dict) else r[0] for r in rows]
    finally:
        conn.close()


@router.get("/classes/{class_id}/subjects/for-me", response_model=List[str])
async def get_class_subjects_for_me(class_id: int, current_user=Depends(get_current_user)):
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT s.name FROM class_subject_teachers cst
                JOIN subjects s ON s.id = cst.subject_id
                WHERE cst.class_id = %s AND cst.teacher_id = %s
                ORDER BY s.name
                """,
                (class_id, current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None))
            )
            rows = await cursor.fetchall()
            return [r["name"] if isinstance(r, dict) else r[0] for r in rows]
    finally:
        conn.close()


@router.post("/grades/bulk")
async def add_grades_bulk(payload: GradeBulkCreate, current_user=Depends(get_current_user)):
    """Insert multiple grades for a given subject and date."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # validate teacher allowed for subject in this class: we need class id; infer from students
            # collect distinct class ids for provided students
            await cursor.execute(
                "SELECT DISTINCT cs.class_id FROM class_students cs WHERE cs.student_id IN ({})".format(
                    ",".join(["%s"] * len(payload.grades))
                ),
                tuple(g.student_id for g in payload.grades)
            )
            class_rows = await cursor.fetchall()
            class_ids = [r["class_id"] if isinstance(r, dict) else r[0] for r in class_rows]
            class_id = class_ids[0] if class_ids else None
            teacher_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)

            # resolve subject id
            await cursor.execute("SELECT id FROM subjects WHERE name=%s", (payload.subject,))
            row = await cursor.fetchone()
            if not row:
                await cursor.execute("INSERT INTO subjects(name) VALUES(%s)", (payload.subject,))
                await conn.commit()
                subject_id = cursor.lastrowid
            else:
                subject_id = row["id"]

            # permission check: admin/owner/root — всегда; teacher — class_subject_teachers или class_teachers
            role = (current_user.get("role", "") if isinstance(current_user, dict) else getattr(current_user, "role", "") or "")
            role_lower = (role or "").lower().strip()
            if role_lower in ("admin", "owner", "superadmin", "root"):
                allowed = True
            elif class_id is not None:
                await cursor.execute(
                    "SELECT 1 FROM class_subject_teachers WHERE class_id=%s AND subject_id=%s AND teacher_id=%s",
                    (class_id, subject_id, teacher_id)
                )
                allowed = await cursor.fetchone()
                if not allowed:
                    await cursor.execute(
                        "SELECT 1 FROM class_teachers WHERE class_id=%s AND teacher_id=%s",
                        (class_id, teacher_id)
                    )
                    allowed = await cursor.fetchone()
                if not allowed:
                    # Доп. проверка: учитель ведёт предмет (teacher_subjects) и класс имеет предмет (class_subjects)
                    await cursor.execute(
                        """SELECT 1 FROM teacher_subjects ts
                           JOIN class_subjects cs ON cs.subject_id = ts.subject_id AND cs.class_id = %s
                           WHERE ts.teacher_id = %s AND ts.subject_id = %s""",
                        (class_id, teacher_id, subject_id)
                    )
                    allowed = await cursor.fetchone()
                if not allowed:
                    raise HTTPException(status_code=403, detail="Not allowed to grade this subject for this class")

            # insert rows
            for g in payload.grades:
                await cursor.execute(
                    """
                    INSERT INTO grades (student_id, subject_id, value, date, teacher_id)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (g.student_id, subject_id, g.value, payload.date, teacher_id)
                )
            await conn.commit()
        return {"status": "ok", "inserted": len(payload.grades)}
    finally:
        conn.close()


class GradeUpdate(BaseModel):
    value: int


@router.get("/grades/{grade_id}")
async def get_grade_info(grade_id: int, current_user=Depends(get_current_user)):
    """Информация об оценке: кто и когда выставил (для истории)."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT g.id, g.value, g.date, g.teacher_id,
                       s.name as subject_name,
                       TRIM(BOTH '"' FROM pt.full_name) as teacher_name
                FROM grades g
                LEFT JOIN subjects s ON s.id = g.subject_id
                LEFT JOIN profiles pt ON pt.user_id = g.teacher_id
                WHERE g.id = %s
                """,
                (grade_id,),
            )
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Оценка не найдена")
            return {
                "id": row["id"],
                "value": row["value"],
                "date": str(row["date"]) if row.get("date") else None,
                "subject_name": row.get("subject_name") or "",
                "teacher_name": row.get("teacher_name") or "Неизвестно",
                "teacher_id": row.get("teacher_id"),
            }
    finally:
        conn.close()


@router.put("/grades/{grade_id}")
async def update_grade(
    grade_id: int,
    payload: GradeUpdate,
    current_user=Depends(get_current_user)
):
    """Обновить оценку. Только учитель, ведущий предмет в классе ученика."""
    if payload.value < 2 or payload.value > 5:
        raise HTTPException(status_code=400, detail="Оценка должна быть от 2 до 5")
    teacher_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """SELECT g.student_id, g.subject_id FROM grades g WHERE g.id = %s""",
                (grade_id,)
            )
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Оценка не найдена")
            student_id = row["student_id"]
            subject_id = row["subject_id"]
            await cursor.execute(
                """SELECT cs.class_id FROM class_students cs WHERE cs.student_id = %s""",
                (student_id,)
            )
            cr = await cursor.fetchone()
            if cr:
                await cursor.execute(
                    """SELECT 1 FROM class_subject_teachers
                       WHERE class_id=%s AND subject_id=%s AND teacher_id=%s""",
                    (cr["class_id"], subject_id, teacher_id)
                )
                if not await cursor.fetchone():
                    raise HTTPException(status_code=403, detail="Нет доступа")
            await cursor.execute(
                "UPDATE grades SET value = %s WHERE id = %s",
                (payload.value, grade_id)
            )
            await conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()


@router.delete("/grades/{grade_id}")
async def delete_grade(grade_id: int, current_user=Depends(get_current_user)):
    """Удалить оценку. Только учитель, ведущий предмет в классе ученика."""
    teacher_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """SELECT g.student_id, g.subject_id FROM grades g WHERE g.id = %s""",
                (grade_id,)
            )
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Оценка не найдена")
            student_id = row["student_id"]
            subject_id = row["subject_id"]
            await cursor.execute(
                """SELECT cs.class_id FROM class_students cs WHERE cs.student_id = %s""",
                (student_id,)
            )
            cr = await cursor.fetchone()
            if cr:
                await cursor.execute(
                    """SELECT 1 FROM class_subject_teachers
                       WHERE class_id=%s AND subject_id=%s AND teacher_id=%s""",
                    (cr["class_id"], subject_id, teacher_id)
                )
                if not await cursor.fetchone():
                    raise HTTPException(status_code=403, detail="Нет доступа")
            await cursor.execute("DELETE FROM grades WHERE id = %s", (grade_id,))
            await conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()


class TeacherStudentNoteUpdate(BaseModel):
    note: str = ""


@router.get("/teachers/student-notes/{student_id}")
async def get_teacher_student_note(student_id: int, current_user=Depends(get_current_user)):
    """Заметка учителя об ученике. Только учитель."""
    role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role != "teacher":
        raise HTTPException(status_code=403, detail="Только для учителей")
    teacher_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """SELECT note FROM teacher_student_notes
                   WHERE teacher_id = %s AND student_id = %s""",
                (teacher_id, student_id)
            )
            row = await cursor.fetchone()
            return {"note": (row["note"] or "") if row else ""}
    finally:
        conn.close()


@router.put("/teachers/student-notes/{student_id}")
async def update_teacher_student_note(
    student_id: int,
    payload: TeacherStudentNoteUpdate,
    current_user=Depends(get_current_user)
):
    """Сохранить заметку об ученике. Только учитель, ведущий его класс."""
    role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role != "teacher":
        raise HTTPException(status_code=403, detail="Только для учителей")
    teacher_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """SELECT 1 FROM class_students cs
                   JOIN class_subject_teachers cst ON cst.class_id = cs.class_id
                   WHERE cs.student_id = %s AND cst.teacher_id = %s""",
                (student_id, teacher_id)
            )
            if not await cursor.fetchone():
                raise HTTPException(status_code=403, detail="Нет доступа к этому ученику")
            await cursor.execute(
                """INSERT INTO teacher_student_notes (teacher_id, student_id, note)
                   VALUES (%s, %s, %s)
                   ON DUPLICATE KEY UPDATE note = VALUES(note)""",
                (teacher_id, student_id, payload.note or "")
            )
            await conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()


class AttendanceQrMark(BaseModel):
    """Данные из QR-кода урока для отметки посещаемости"""
    date: str
    class_id: int
    subject_id: int
    lesson_number: int


@router.get("/attendance/class")
async def get_class_attendance_for_date(
    date: str = Query(..., description="Date YYYY-MM-DD"),
    class_id: int = Query(..., description="Class ID"),
    current_user=Depends(get_current_user),
):
    """Посещаемость класса за дату (для подсветки пропусков в журнале)."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT a.student_id, a.status, a.lesson_number, a.subject_id
                FROM attendance a
                JOIN class_students cs ON a.student_id = cs.student_id AND cs.class_id = %s
                WHERE a.date = %s
                """,
                (class_id, date),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "student_id": r["student_id"] if isinstance(r, dict) else r[0],
                    "status": r["status"] if isinstance(r, dict) else r[1],
                    "lesson_number": r.get("lesson_number") if isinstance(r, dict) else r[2],
                    "subject_id": r.get("subject_id") if isinstance(r, dict) else r[3],
                }
                for r in rows
            ]
    finally:
        conn.close()


@router.post("/attendance/qr-mark")
async def mark_attendance_by_qr(payload: AttendanceQrMark, current_user=Depends(get_current_user)):
    """Ученик сканирует QR урока — отмечает себя присутствующим."""
    user_role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if user_role != "student":
        raise HTTPException(status_code=403, detail="Только ученики могут отмечаться по QR")
    
    student_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем, что ученик в этом классе
            await cursor.execute(
                "SELECT 1 FROM class_students WHERE class_id=%s AND student_id=%s",
                (payload.class_id, student_id)
            )
            if not await cursor.fetchone():
                raise HTTPException(status_code=403, detail="Вы не ученик этого класса")
            
            # Получаем teacher_id для класса и предмета
            await cursor.execute(
                """SELECT teacher_id FROM class_subject_teachers 
                   WHERE class_id=%s AND subject_id=%s LIMIT 1""",
                (payload.class_id, payload.subject_id)
            )
            teacher_row = await cursor.fetchone()
            if not teacher_row:
                raise HTTPException(status_code=400, detail="Урок не найден")
            teacher_id = teacher_row["teacher_id"] if isinstance(teacher_row, dict) else teacher_row[0]
            
            await cursor.execute(
                """
                INSERT INTO attendance (student_id, date, status, lesson_number, subject_id, teacher_id)
                VALUES (%s, %s, 'present', %s, %s, %s)
                ON DUPLICATE KEY UPDATE status = 'present', subject_id = VALUES(subject_id), teacher_id = VALUES(teacher_id)
                """,
                (student_id, payload.date, payload.lesson_number, payload.subject_id, teacher_id)
            )
            await conn.commit()
        return {"status": "ok", "message": "Посещаемость отмечена"}
    finally:
        conn.close()


@router.post("/attendance/bulk")
async def add_attendance_bulk(payload: AttendanceBulkCreate, current_user=Depends(get_current_user)):
    """Отметить посещаемость по уроку (дата, класс, номер урока, список учеников и статус)."""
    teacher_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT 1 FROM class_subject_teachers WHERE class_id=%s AND subject_id=%s AND teacher_id=%s",
                (payload.class_id, payload.subject_id, teacher_id)
            )
            if not await cursor.fetchone():
                raise HTTPException(status_code=403, detail="Нет доступа к этому классу/предмету")
            for e in payload.entries:
                if e.status not in ("present", "late", "absent"):
                    raise HTTPException(status_code=400, detail=f"Недопустимый статус: {e.status}")
                await cursor.execute(
                    """
                    INSERT INTO attendance (student_id, date, status, lesson_number, subject_id, teacher_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE status = VALUES(status), subject_id = VALUES(subject_id), teacher_id = VALUES(teacher_id)
                    """,
                    (e.student_id, payload.date, e.status, payload.lesson_number, payload.subject_id, teacher_id)
                )
            await conn.commit()
        return {"status": "ok", "inserted": len(payload.entries)}
    finally:
        conn.close()


@router.get("/homeworks/student")
async def list_student_homeworks(current_user=Depends(get_current_user)):
    """Список ДЗ для ученика (по классам, в которых он учится)."""
    role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role != "student":
        raise HTTPException(status_code=403, detail="Только для учеников")
    student_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """SELECT h.id, h.class_id, h.subject_id, h.due_date, h.description,
                          c.name as class_name, s.name as subject_name
                   FROM homeworks h
                   JOIN class_students cs ON h.class_id = cs.class_id AND cs.student_id = %s
                   JOIN classes c ON c.id = h.class_id
                   JOIN subjects s ON s.id = h.subject_id
                   WHERE h.due_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                   ORDER BY h.due_date ASC, h.id ASC""",
                (student_id,)
            )
            rows = await cursor.fetchall()
            return [{"id": r["id"], "class_id": r["class_id"], "subject_id": r["subject_id"],
                     "due_date": str(r["due_date"]) if r["due_date"] else None,
                     "description": r["description"], "class_name": r["class_name"],
                     "subject_name": r["subject_name"]} for r in rows]
    finally:
        conn.close()


@router.get("/homeworks")
async def list_homeworks(
    class_id: int = Query(None, description="Фильтр по классу"),
    subject_id: int = Query(None, description="Фильтр по предмету"),
    current_user=Depends(get_current_user)
):
    """Список ДЗ для учителя (по своим классам/предметам)."""
    teacher_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            if class_id is not None:
                if subject_id is not None:
                    await cursor.execute(
                        """SELECT h.id, h.class_id, h.subject_id, h.due_date, h.description, h.teacher_id,
                                  c.name as class_name, s.name as subject_name
                           FROM homeworks h
                           JOIN classes c ON c.id = h.class_id
                           JOIN subjects s ON s.id = h.subject_id
                           WHERE h.teacher_id = %s AND h.class_id = %s AND h.subject_id = %s
                           ORDER BY h.due_date DESC""",
                        (teacher_id, class_id, subject_id)
                    )
                else:
                    await cursor.execute(
                        """SELECT h.id, h.class_id, h.subject_id, h.due_date, h.description, h.teacher_id,
                                  c.name as class_name, s.name as subject_name
                           FROM homeworks h
                           JOIN classes c ON c.id = h.class_id
                           JOIN subjects s ON s.id = h.subject_id
                           WHERE h.teacher_id = %s AND h.class_id = %s
                           ORDER BY h.due_date DESC""",
                        (teacher_id, class_id)
                    )
            else:
                await cursor.execute(
                    """SELECT h.id, h.class_id, h.subject_id, h.due_date, h.description, h.teacher_id,
                              c.name as class_name, s.name as subject_name
                       FROM homeworks h
                       JOIN classes c ON c.id = h.class_id
                       JOIN subjects s ON s.id = h.subject_id
                       WHERE h.teacher_id = %s
                       ORDER BY h.due_date DESC, h.id DESC""",
                    (teacher_id,)
                )
            rows = await cursor.fetchall()
            return [{"id": r["id"], "class_id": r["class_id"], "subject_id": r["subject_id"],
                     "due_date": str(r["due_date"]) if r["due_date"] else None,
                     "description": r["description"], "class_name": r["class_name"],
                     "subject_name": r["subject_name"]} for r in rows]
    finally:
        conn.close()


class HomeworkUpdate(BaseModel):
    due_date: str
    description: str


@router.put("/homeworks/{homework_id}")
async def update_homework(
    homework_id: int,
    payload: HomeworkUpdate,
    current_user=Depends(get_current_user)
):
    """Редактировать ДЗ."""
    teacher_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT id FROM homeworks WHERE id = %s AND teacher_id = %s",
                (homework_id, teacher_id)
            )
            if not await cursor.fetchone():
                raise HTTPException(status_code=404, detail="ДЗ не найдено")
            await cursor.execute(
                "UPDATE homeworks SET due_date = %s, description = %s WHERE id = %s",
                (payload.due_date, payload.description, homework_id)
            )
            await conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()


@router.delete("/homeworks/{homework_id}")
async def delete_homework(homework_id: int, current_user=Depends(get_current_user)):
    """Удалить ДЗ."""
    teacher_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            result = await cursor.execute(
                "DELETE FROM homeworks WHERE id = %s AND teacher_id = %s",
                (homework_id, teacher_id)
            )
            await conn.commit()
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="ДЗ не найдено")
        return {"status": "ok"}
    finally:
        conn.close()


@router.post("/homeworks")
async def create_homework(payload: HomeworkCreate, current_user=Depends(get_current_user)):
    """Создать домашнее задание на следующую пару (класс, предмет, дата сдачи, описание)."""
    teacher_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT 1 FROM class_subject_teachers WHERE class_id=%s AND subject_id=%s AND teacher_id=%s",
                (payload.class_id, payload.subject_id, teacher_id)
            )
            if not await cursor.fetchone():
                raise HTTPException(status_code=403, detail="Нет доступа к этому классу/предмету")
            await cursor.execute(
                """
                INSERT INTO homeworks (class_id, subject_id, due_date, description, teacher_id)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (payload.class_id, payload.subject_id, payload.due_date, payload.description, teacher_id)
            )
            lid = cursor.lastrowid
            await conn.commit()
        return {"status": "ok", "id": lid}
    finally:
        conn.close()


# ========== Homework submissions (фото/файлы от учеников) ==========

_ALLOWED_HW_SUBMISSION_EXTS = {"jpg", "jpeg", "png", "pdf", "doc", "docx"}


@router.post("/homeworks/{homework_id}/submissions")
async def upload_homework_submission(
    homework_id: int,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user)
):
    """Ученик сдаёт работу (фото или файл)."""
    role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
    student_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    if role != "student":
        raise HTTPException(status_code=403, detail="Только для учеников")
    if not file.filename:
        raise HTTPException(status_code=400, detail="Файл не указан")
    ext = (file.filename.split(".")[-1] or "").lower()
    if ext not in _ALLOWED_HW_SUBMISSION_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Допустимые форматы: {', '.join(_ALLOWED_HW_SUBMISSION_EXTS)}"
        )
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """SELECT h.id FROM homeworks h
                   JOIN class_students cs ON h.class_id = cs.class_id AND cs.student_id = %s
                   WHERE h.id = %s""",
                (student_id, homework_id)
            )
            if not await cursor.fetchone():
                raise HTTPException(status_code=404, detail="ДЗ не найдено или вы не в этом классе")
        fname = f"{homework_id}_{student_id}_{uuid.uuid4().hex[:12]}.{ext}"
        dir_path = os.path.join(settings.HOMEWORK_SUBMISSIONS_DIR, str(homework_id))
        os.makedirs(dir_path, exist_ok=True)
        file_path = os.path.join(dir_path, fname)
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)
        rel_path = f"{homework_id}/{fname}"
        async with conn.cursor() as cursor:
            await cursor.execute(
                """INSERT INTO homework_submissions (homework_id, student_id, file_path, file_name)
                   VALUES (%s, %s, %s, %s)""",
                (homework_id, student_id, rel_path, file.filename or fname)
            )
            sid = cursor.lastrowid
            await conn.commit()
        return {"status": "ok", "id": sid, "file_name": file.filename}
    finally:
        conn.close()


@router.get("/homeworks/{homework_id}/submissions")
async def list_homework_submissions(
    homework_id: int,
    current_user=Depends(get_current_user)
):
    """Список сданных работ по ДЗ. Учитель — все, ученик — свои."""
    uid = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            if role == "student":
                await cursor.execute(
                    """SELECT hs.id, hs.student_id, hs.file_path, hs.file_name, hs.created_at,
                              hs.grade, hs.is_reviewed, hs.comment,
                              TRIM(BOTH '"' FROM p.full_name) AS student_name
                       FROM homework_submissions hs
                       JOIN profiles p ON p.user_id = hs.student_id
                       JOIN homeworks h ON h.id = hs.homework_id
                       JOIN class_students cs ON h.class_id = cs.class_id AND cs.student_id = %s
                       WHERE hs.homework_id = %s AND hs.student_id = %s
                       ORDER BY hs.created_at DESC""",
                    (uid, homework_id, uid)
                )
            else:
                await cursor.execute(
                    "SELECT 1 FROM homeworks WHERE id = %s AND teacher_id = %s",
                    (homework_id, uid)
                )
                if not await cursor.fetchone() and role not in ("admin", "owner", "superadmin"):
                    raise HTTPException(status_code=404, detail="ДЗ не найдено")
                await cursor.execute(
                    """SELECT hs.id, hs.student_id, hs.file_path, hs.file_name, hs.created_at,
                              hs.grade, hs.is_reviewed, hs.comment,
                              TRIM(BOTH '"' FROM p.full_name) AS student_name
                       FROM homework_submissions hs
                       JOIN profiles p ON p.user_id = hs.student_id
                       WHERE hs.homework_id = %s
                       ORDER BY hs.created_at DESC""",
                    (homework_id,)
                )
            rows = await cursor.fetchall()
        return [
            {
                "id": r["id"],
                "student_id": r["student_id"],
                "student_name": r.get("student_name") or "",
                "file_path": r["file_path"],
                "file_name": r["file_name"],
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                "grade": r.get("grade"),
                "is_reviewed": bool(r.get("is_reviewed")) if r.get("is_reviewed") is not None else False,
                "comment": r.get("comment") or "",
            }
            for r in (rows if isinstance(rows, list) else list(rows))
        ]
    finally:
        conn.close()


@router.get("/homework-submissions/{submission_id}/file")
async def get_homework_submission_file(
    submission_id: int,
    current_user=Depends(get_current_user)
):
    """Скачать файл сдачи."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """SELECT hs.file_path, hs.file_name, hs.student_id, h.teacher_id
                   FROM homework_submissions hs
                   JOIN homeworks h ON h.id = hs.homework_id
                   WHERE hs.id = %s""",
                (submission_id,)
            )
            row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Файл не найден")
        uid = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
        role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
        if role in ("admin", "owner", "superadmin"):
            pass
        elif role == "teacher" and row["teacher_id"] == uid:
            pass
        elif role == "student" and row["student_id"] == uid:
            pass
        else:
            raise HTTPException(status_code=403, detail="Нет доступа")
        full_path = os.path.join(settings.HOMEWORK_SUBMISSIONS_DIR, row["file_path"])
        if not os.path.isfile(full_path):
            raise HTTPException(status_code=404, detail="Файл не найден на диске")
        media = "application/octet-stream"
        if full_path.lower().endswith((".jpg", ".jpeg")):
            media = "image/jpeg"
        elif full_path.lower().endswith(".png"):
            media = "image/png"
        elif full_path.lower().endswith(".pdf"):
            media = "application/pdf"
        return FileResponse(full_path, media_type=media, filename=row["file_name"])
    finally:
        conn.close()


@router.patch("/homework-submissions/{submission_id}")
async def update_homework_submission(
    submission_id: int,
    body: HomeworkSubmissionUpdate,
    current_user=Depends(get_current_user)
):
    """Учитель: оценка, «Проверено», комментарий к сдаче."""
    uid = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("teacher", "admin", "owner", "superadmin"):
        raise HTTPException(status_code=403, detail="Только для учителей")
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """SELECT h.teacher_id FROM homework_submissions hs
                   JOIN homeworks h ON h.id = hs.homework_id WHERE hs.id = %s""",
                (submission_id,)
            )
            row = await cursor.fetchone()
        if not row or (row["teacher_id"] != uid and role not in ("admin", "owner", "superadmin")):
            raise HTTPException(status_code=404, detail="Не найдено")
        updates, params = [], []
        if body.grade is not None:
            if body.grade < 2 or body.grade > 5:
                raise HTTPException(status_code=400, detail="Оценка от 2 до 5")
            updates.append("grade = %s")
            params.append(body.grade)
        if body.is_reviewed is not None:
            updates.append("is_reviewed = %s")
            params.append(bool(body.is_reviewed))
        if body.comment is not None:
            updates.append("comment = %s")
            params.append(body.comment[:2000] if body.comment else "")
        if not updates:
            return {"status": "ok"}
        params.append(submission_id)
        async with conn.cursor() as cursor:
            await cursor.execute(
                f"UPDATE homework_submissions SET {', '.join(updates)} WHERE id = %s",
                params
            )
            await conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()


@router.delete("/homework-submissions/{submission_id}")
async def delete_homework_submission(
    submission_id: int,
    current_user=Depends(get_current_user)
):
    """Удалить сдачу (учитель). Файл остаётся на диске."""
    uid = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("teacher", "admin", "owner", "superadmin"):
        raise HTTPException(status_code=403, detail="Только для учителей")
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """SELECT h.teacher_id FROM homework_submissions hs
                   JOIN homeworks h ON h.id = hs.homework_id WHERE hs.id = %s""",
                (submission_id,)
            )
            row = await cursor.fetchone()
        if not row or (row["teacher_id"] != uid and role not in ("admin", "owner", "superadmin")):
            raise HTTPException(status_code=404, detail="Не найдено")
        async with conn.cursor() as cursor:
            await cursor.execute("DELETE FROM homework_submissions WHERE id = %s", (submission_id,))
            await conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()


@router.get("/homeworks/{homework_id}/submission-status")
async def get_homework_submission_status(
    homework_id: int,
    current_user=Depends(get_current_user)
):
    """Учитель: кто сдал, кто не сдал (для сводки «Сдано: N из M»)."""
    uid = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("teacher", "admin", "owner", "superadmin"):
        raise HTTPException(status_code=403, detail="Только для учителей")
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT h.class_id, h.teacher_id FROM homeworks h WHERE h.id = %s",
                (homework_id,)
            )
            hw = await cursor.fetchone()
        if not hw or (hw["teacher_id"] != uid and role not in ("admin", "owner", "superadmin")):
            raise HTTPException(status_code=404, detail="ДЗ не найдено")
        class_id = hw["class_id"]
        async with conn.cursor() as cursor:
            await cursor.execute(
                """SELECT cs.student_id, TRIM(BOTH '"' FROM p.full_name) AS full_name
                   FROM class_students cs
                   JOIN profiles p ON p.user_id = cs.student_id
                   WHERE cs.class_id = %s ORDER BY full_name""",
                (class_id,)
            )
            all_students = await cursor.fetchall()
            submitted_ids = set()
            await cursor.execute(
                "SELECT student_id FROM homework_submissions WHERE homework_id = %s",
                (homework_id,)
            )
            for r in await cursor.fetchall():
                submitted_ids.add(r["student_id"])
        not_submitted = [
            {"student_id": s["student_id"], "full_name": s.get("full_name") or ""}
            for s in (all_students or [])
            if s["student_id"] not in submitted_ids
        ]
        return {
            "total": len(all_students) if all_students else 0,
            "submitted_count": len(submitted_ids),
            "not_submitted": not_submitted,
        }
    finally:
        conn.close()


@router.get("/subject-scores/{subject_name}", response_model=List[dict])
async def get_student_scores_by_subject(subject_name: str, current_user=Depends(get_current_user)):
    """Get student scores for specific subject"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT id FROM subjects WHERE name = %s", (subject_name,))
            subject = await cursor.fetchone()
            if not subject:
                raise HTTPException(status_code=404, detail="Subject not found")

            await cursor.execute("""
                SELECT 
                    u.id AS student_id,
                    p.full_name AS student_name,
                    GROUP_CONCAT(g.value ORDER BY g.date) AS scores
                FROM users u
                JOIN profiles p ON u.id = p.user_id
                LEFT JOIN grades g ON u.id = g.student_id AND g.subject_id = %s
                WHERE u.role = 'student'
                GROUP BY u.id, p.full_name
            """, (subject['id'],))
            students = await cursor.fetchall()

            result = []
            for s in students:
                scores = list(map(int, s['scores'].split(','))) if s['scores'] else []
                avg = round(sum(scores) / len(scores), 2) if scores else 0.0
                result.append({
                    "student_id": s['student_id'],
                    "student_name": s['student_name'],
                    "scores": scores,
                    "average_score": avg
                })
            result.sort(key=lambda x: x["average_score"], reverse=True)
            return result
    finally:
        conn.close()


@router.get("/student-scores-full", response_model=List[dict])
async def get_all_student_scores(current_user=Depends(get_current_user)):
    """Get all student scores"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT 
                    u.id AS student_id,
                    p.full_name AS student_name,
                    s.name AS subject,
                    g.value,
                    g.date
                FROM users u
                JOIN profiles p ON u.id = p.user_id
                JOIN grades g ON u.id = g.student_id
                JOIN subjects s ON s.id = g.subject_id
                WHERE u.role = 'student'
                ORDER BY u.id, g.date
            """)
            rows = await cursor.fetchall()

            result, current, last_id = [], None, None
            for row in rows:
                if row['student_id'] != last_id:
                    if current:
                        result.append(current)
                    current = {"student_id": row['student_id'], "student_name": row['student_name'], "grades": []}
                    last_id = row['student_id']
                current["grades"].append({
                    "subject": row["subject"],
                    "value": row["value"],
                    "date": row["date"].isoformat() if hasattr(row["date"], "isoformat") else str(row["date"])
                })
            if current:
                result.append(current)
            return result
    finally:
        conn.close()


@router.get("/attendance-stats/{student_id}", response_model=AttendanceStats)
async def get_attendance_stats(student_id: int, current_user: User = Depends(get_current_user)):
    """Get attendance statistics for a student"""
    await check_analytics_access(current_user, student_id=student_id)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Получаем статистику посещаемости
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
            
            result = await cursor.fetchone()
            if not result:
                return AttendanceStats(present=0, late=0, absent=0, total_lessons=0)
            
            return AttendanceStats(
                present=result['present'] or 0,
                late=result['late'] or 0,
                absent=result['absent'] or 0,
                total_lessons=result['total_lessons'] or 0
            )
    finally:
        conn.close()


@router.get("/attendance-heatmap/{student_id}", response_model=List[AttendanceHeatmapData])
async def get_attendance_heatmap(student_id: int, current_user: User = Depends(get_current_user)):
    """Get attendance heatmap data for a student (last 5 weeks)"""
    await check_analytics_access(current_user, student_id=student_id)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Получаем данные посещаемости за последние 5 недель
            # Используем правильный расчет недели относительно текущей даты
            # Группируем по дате и выбираем самый "плохой" статус (absent > late > present)
            await cursor.execute("""
                SELECT 
                    date,
                    FLOOR(DATEDIFF(CURDATE(), date) / 7) + 1 as week,
                    WEEKDAY(date) + 1 as day,
                    MAX(CASE 
                        WHEN status = 'present' THEN 1
                        WHEN status = 'late' THEN 2
                        WHEN status = 'absent' THEN 3
                        ELSE 0
                    END) as status
                FROM attendance 
                WHERE student_id = %s 
                AND date >= DATE_SUB(CURDATE(), INTERVAL 35 DAY)
                AND date <= CURDATE()
                GROUP BY date
                ORDER BY date
            """, (student_id,))
            
            rows = await cursor.fetchall()
            result = []
            for row in rows:
                week = row['week']
                # Ограничиваем недели от 1 до 5
                if week >= 1 and week <= 5:
                    result.append(AttendanceHeatmapData(
                        week=week,
                        day=row['day'],
                        status=row['status']
                    ))
            
            return result
    finally:
        conn.close()


@router.get("/attendance-detail/{student_id}", response_model=List[AttendanceDetail])
async def get_attendance_detail(
    student_id: int, 
    date: str = Query(..., description="Date for attendance detail (YYYY-MM-DD)"),
    current_user: User = Depends(get_current_user)
):
    """Get detailed attendance information for a student on a specific date"""
    await check_analytics_access(current_user, student_id=student_id)
    print(f"get_attendance_detail: student_id={student_id}, date={date}")
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Получаем детальную информацию о посещаемости за конкретную дату
            # Включаем все записи, даже если lesson_number NULL (для дней полного отсутствия)
            await cursor.execute("""
                SELECT 
                    COALESCE(a.lesson_number, 0) as lesson_number,
                    COALESCE(s.name, 'Не указан') as subject_name,
                    a.status,
                    COALESCE(p.full_name, 'Не указан') as teacher_name,
                    a.date
                FROM attendance a
                LEFT JOIN subjects s ON s.id = a.subject_id
                LEFT JOIN profiles p ON p.user_id = a.teacher_id
                WHERE a.student_id = %s 
                AND a.date = %s
                ORDER BY COALESCE(a.lesson_number, 0)
            """, (student_id, date))
            
            rows = await cursor.fetchall()
            print(f"get_attendance_detail: found {len(rows)} rows for student_id={student_id}, date={date}")
            
            # Если записей нет, проверяем, есть ли записи для этого дня в тепловой карте
            if len(rows) == 0:
                print(f"get_attendance_detail: No records found, checking heatmap data...")
                await cursor.execute("""
                    SELECT 
                        date,
                        status,
                        COUNT(*) as count
                    FROM attendance 
                    WHERE student_id = %s 
                    AND date = %s
                    GROUP BY date, status
                """, (student_id, date))
                heatmap_check = await cursor.fetchall()
                print(f"get_attendance_detail: heatmap check result: {heatmap_check}")
                
                # Также проверяем близкие даты
                await cursor.execute("""
                    SELECT 
                        date,
                        status,
                        COUNT(*) as count
                    FROM attendance 
                    WHERE student_id = %s 
                    AND date >= DATE_SUB(%s, INTERVAL 3 DAY)
                    AND date <= DATE_ADD(%s, INTERVAL 3 DAY)
                    GROUP BY date, status
                    ORDER BY date
                """, (student_id, date, date))
                nearby_check = await cursor.fetchall()
                print(f"get_attendance_detail: nearby dates (3 days): {nearby_check}")
            
            result = []
            for row in rows:
                print(f"get_attendance_detail: row={row}")
                result.append(AttendanceDetail(
                    lesson_number=row['lesson_number'] or 0,
                    subject_name=row['subject_name'] or 'Не указан',
                    status=row['status'],
                    teacher_name=row['teacher_name']
                ))
            
            return result
    finally:
        conn.close()


@router.get("/grade-distribution/{student_id}", response_model=GradeDistribution)
async def get_grade_distribution(student_id: int, current_user: User = Depends(get_current_user)):
    """Get grade distribution for a student"""
    await check_analytics_access(current_user, student_id=student_id)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT 
                    COUNT(CASE WHEN value = 2 THEN 1 END) as grade_2,
                    COUNT(CASE WHEN value = 3 THEN 1 END) as grade_3,
                    COUNT(CASE WHEN value = 4 THEN 1 END) as grade_4,
                    COUNT(CASE WHEN value = 5 THEN 1 END) as grade_5,
                    COUNT(*) as total_grades
                FROM grades 
                WHERE student_id = %s
                AND date >= DATE_SUB(CURDATE(), INTERVAL 3 MONTH)
            """, (student_id,))
            
            result = await cursor.fetchone()
            if not result:
                return GradeDistribution(grade_2=0, grade_3=0, grade_4=0, grade_5=0, total_grades=0)
            
            return GradeDistribution(
                grade_2=result['grade_2'] or 0,
                grade_3=result['grade_3'] or 0,
                grade_4=result['grade_4'] or 0,
                grade_5=result['grade_5'] or 0,
                total_grades=result['total_grades'] or 0
            )
    finally:
        conn.close()


@router.get("/classes/{class_id}/grade-distribution", response_model=dict)
async def get_class_grade_distribution(
    class_id: int,
    subject_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user)
):
    """Распределение оценок 2-3-4-5 по классу (за последние 3 месяца)."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            subject_filter = "AND g.subject_id = %s" if subject_id else ""
            params = [class_id]
            if subject_id:
                params.append(subject_id)
            await cursor.execute(
                f"""
                SELECT
                    COUNT(CASE WHEN g.value = 2 THEN 1 END) AS grade_2,
                    COUNT(CASE WHEN g.value = 3 THEN 1 END) AS grade_3,
                    COUNT(CASE WHEN g.value = 4 THEN 1 END) AS grade_4,
                    COUNT(CASE WHEN g.value = 5 THEN 1 END) AS grade_5,
                    COUNT(*) AS total_grades
                FROM grades g
                JOIN class_students cs ON g.student_id = cs.student_id
                WHERE cs.class_id = %s {subject_filter}
                AND g.date >= DATE_SUB(CURDATE(), INTERVAL 3 MONTH)
                """,
                tuple(params)
            )
            r = await cursor.fetchone()
            if not r:
                return {"grade_2": 0, "grade_3": 0, "grade_4": 0, "grade_5": 0, "total_grades": 0}
            return {
                "grade_2": r["grade_2"] or 0,
                "grade_3": r["grade_3"] or 0,
                "grade_4": r["grade_4"] or 0,
                "grade_5": r["grade_5"] or 0,
                "total_grades": r["total_grades"] or 0,
            }
    finally:
        conn.close()


@router.get("/classes/{class_id}/performance-trends", response_model=List[dict])
async def get_class_performance_trends(
    class_id: int,
    subject_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user)
):
    """Средний балл класса по неделям (последние 8 недель)."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            subject_filter = "AND g.subject_id = %s" if subject_id else ""
            params = [class_id]
            if subject_id:
                params.append(subject_id)
            await cursor.execute(
                f"""
                SELECT YEARWEEK(g.date) AS week, AVG(g.value) AS avg_val
                FROM grades g
                JOIN class_students cs ON g.student_id = cs.student_id
                WHERE cs.class_id = %s {subject_filter}
                AND g.date >= DATE_SUB(CURDATE(), INTERVAL 8 WEEK)
                GROUP BY YEARWEEK(g.date)
                ORDER BY week
                """,
                tuple(params)
            )
            rows = await cursor.fetchall()
            return [
                {"week": str(r["week"]), "average_score": round(float(r["avg_val"]), 2)}
                for r in rows
            ]
    finally:
        conn.close()


@router.get("/performance-trends/{student_id}", response_model=List[PerformanceTrend])
async def get_performance_trends(student_id: int, current_user: User = Depends(get_current_user)):
    """Get performance trends for a student (last 2 months)"""
    await check_analytics_access(current_user, student_id=student_id)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT 
                    DATE(date) as date,
                    AVG(value) as average_score
                FROM grades 
                WHERE student_id = %s
                AND date >= DATE_SUB(CURDATE(), INTERVAL 2 MONTH)
                GROUP BY DATE(date)
                ORDER BY date
            """, (student_id,))
            
            rows = await cursor.fetchall()
            result = []
            for row in rows:
                result.append(PerformanceTrend(
                    date=row['date'].isoformat() if hasattr(row['date'], 'isoformat') else str(row['date']),
                    average_score=round(float(row['average_score']), 2)
                ))
            
            return result
    finally:
        conn.close()


@router.get("/subject-performance/{student_id}/{subject_id}", response_model=SubjectPerformance)
async def get_subject_performance(student_id: int, subject_id: int, current_user: User = Depends(get_current_user)):
    """Get performance data for a specific subject"""
    await check_analytics_access(current_user, student_id=student_id)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Получаем название предмета
            await cursor.execute("SELECT name FROM subjects WHERE id = %s", (subject_id,))
            subject = await cursor.fetchone()
            if not subject:
                raise HTTPException(status_code=404, detail="Subject not found")
            
            # Получаем распределение оценок
            await cursor.execute("""
                SELECT 
                    COUNT(CASE WHEN value = 2 THEN 1 END) as grade_2,
                    COUNT(CASE WHEN value = 3 THEN 1 END) as grade_3,
                    COUNT(CASE WHEN value = 4 THEN 1 END) as grade_4,
                    COUNT(CASE WHEN value = 5 THEN 1 END) as grade_5,
                    COUNT(*) as total_grades,
                    AVG(value) as average_score
                FROM grades 
                WHERE student_id = %s AND subject_id = %s
                AND date >= DATE_SUB(CURDATE(), INTERVAL 3 MONTH)
            """, (student_id, subject_id))
            
            grade_result = await cursor.fetchone()
            if not grade_result:
                return SubjectPerformance(
                    subject=subject['name'],
                    average_score=0.0,
                    grade_distribution=GradeDistribution(grade_2=0, grade_3=0, grade_4=0, grade_5=0, total_grades=0),
                    trends=[]
                )
            
            # Получаем тренды
            await cursor.execute("""
                SELECT 
                    DATE(date) as date,
                    AVG(value) as average_score
                FROM grades 
                WHERE student_id = %s AND subject_id = %s
                AND date >= DATE_SUB(CURDATE(), INTERVAL 2 MONTH)
                GROUP BY DATE(date)
                ORDER BY date
            """, (student_id, subject_id))
            
            trend_rows = await cursor.fetchall()
            trends = []
            for row in trend_rows:
                trends.append(PerformanceTrend(
                    date=row['date'].isoformat() if hasattr(row['date'], 'isoformat') else str(row['date']),
                    average_score=round(float(row['average_score']), 2)
                ))
            
            return SubjectPerformance(
                subject=subject['name'],
                average_score=round(float(grade_result['average_score']), 2),
                grade_distribution=GradeDistribution(
                    grade_2=grade_result['grade_2'] or 0,
                    grade_3=grade_result['grade_3'] or 0,
                    grade_4=grade_result['grade_4'] or 0,
                    grade_5=grade_result['grade_5'] or 0,
                    total_grades=grade_result['total_grades'] or 0
                ),
                trends=trends
            )
    finally:
        conn.close()


# Модель для создания класса
class ClassCreate(BaseModel):
    parallel: str
    class_name: str
    academic_year: str


@router.post("/classes", status_code=201)
async def create_class(payload: ClassCreate, current_user=Depends(get_current_user)):
    """Create a new class. Class name must be unique within each school."""
    # Проверяем права доступа - только админы и владельцы могут создавать классы
    if current_user["role"] not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Forbidden")
    
    if not payload.parallel.strip():
        raise HTTPException(status_code=400, detail="Parallel is required")
    
    if not payload.class_name.strip():
        raise HTTPException(status_code=400, detail="Class name is required")
    
    if not payload.academic_year.strip():
        raise HTTPException(status_code=400, detail="Academic year is required")
    
    school_id = await get_school_id_for_user(current_user)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            # Уникальность названия класса в пределах школы
            if school_id is not None:
                await cur.execute(
                    """SELECT id FROM classes 
                       WHERE `parallel`=%s AND class_name=%s AND academic_year=%s AND school_id=%s""",
                    (payload.parallel.strip(), payload.class_name.strip(), 
                     payload.academic_year.strip(), school_id)
                )
            else:
                await cur.execute(
                    """SELECT id FROM classes 
                       WHERE `parallel`=%s AND class_name=%s AND academic_year=%s AND school_id IS NULL""",
                    (payload.parallel.strip(), payload.class_name.strip(), 
                     payload.academic_year.strip())
                )
            existing = await cur.fetchone()
            if existing:
                raise HTTPException(
                    status_code=400,
                    detail="Класс с таким названием уже существует в этой школе"
                )
            
            # Создаем новый класс (name = parallel + class_name для отображения)
            display_name = payload.parallel.strip() + payload.class_name.strip()
            if school_id is not None:
                await cur.execute(
                    """INSERT INTO classes (`parallel`, class_name, academic_year, name, school_id) 
                       VALUES (%s, %s, %s, %s, %s)""",
                    (payload.parallel.strip(), payload.class_name.strip(), 
                     payload.academic_year.strip(), display_name, school_id)
                )
            else:
                await cur.execute(
                    """INSERT INTO classes (`parallel`, class_name, academic_year, name) 
                       VALUES (%s, %s, %s, %s)""",
                    (payload.parallel.strip(), payload.class_name.strip(), 
                     payload.academic_year.strip(), display_name)
                )
            await conn.commit()
            new_id = cur.lastrowid
            
            return {
                "id": new_id,
                "parallel": payload.parallel.strip(),
                "class_name": payload.class_name.strip(),
                "academic_year": payload.academic_year.strip(),
                "status": "created"
            }
    finally:
        conn.close()
