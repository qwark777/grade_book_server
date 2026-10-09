import os
import json
import uuid
import logging
from datetime import date as date_type, datetime, time
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from starlette.responses import FileResponse

from app.core.security import get_current_user
from app.core.config import settings
from app.core.entitlements import (
    require_entitlement,
    get_school_id_for_user,
    resolve_school_id_for_user,
    require_school_feature,
)
from app.core.file_uploads import (
    DOCUMENT_EXTS,
    IMAGE_EXTS,
    MAX_DOCUMENT_BYTES,
    safe_original_filename,
    safe_storage_path,
    save_upload_limited,
    validate_upload_metadata,
)
from app.core.subscription_limits import (
    change_school_storage,
    consume_school_quota,
    ensure_school_capacity,
    ensure_school_user_membership,
)
from app.db.connection import get_db_connection
from app.services.notifications import notify_many
from app.services.achievement_service import evaluate_class_podium
from app.models.grade import (
    ClassItem, AttendanceStats, AttendanceHeatmapData, AttendanceDetail,
    GradeDistribution, PerformanceTrend, SubjectPerformance, Subject
)
from app.models.user import User
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)


async def _get_parent_ids_for_students(cursor, student_ids: list[int]) -> list[int]:
    if not student_ids:
        return []
    await cursor.execute(
        "SELECT DISTINCT parent_id FROM parent_students WHERE student_id IN ({})".format(
            ",".join(["%s"] * len(student_ids))
        ),
        tuple(student_ids),
    )
    rows = await cursor.fetchall()
    return [int(row["parent_id"]) for row in rows]


def _current_user_role(current_user) -> str:
    return current_user.get("role", "") if isinstance(current_user, dict) else getattr(current_user, "role", "")


def _current_user_id(current_user) -> Optional[int]:
    uid = current_user.get("id") if isinstance(current_user, dict) else getattr(current_user, "id", None)
    try:
        return int(uid) if uid is not None else None
    except (TypeError, ValueError):
        return None


def _row_value(row, key: str, index: int = 0, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    return row[index] if index < len(row) else default


async def _ensure_staff_can_access_homework_row(row, current_user) -> None:
    role = _current_user_role(current_user)
    uid = _current_user_id(current_user)
    if role in ("owner", "superadmin", "root"):
        return
    if role == "teacher" and int(_row_value(row, "teacher_id", 0, -1) or -1) == uid:
        return
    if role == "admin":
        admin_school_id = await get_school_id_for_user(current_user)
        homework_school_id = _row_value(row, "school_id", 1)
        if admin_school_id is not None and homework_school_id is not None and int(admin_school_id) == int(homework_school_id):
            return
    raise HTTPException(status_code=403, detail="Нет доступа")


async def _ensure_can_read_class(cursor, current_user, class_id: int) -> None:
    role = _current_user_role(current_user).lower().strip()
    uid = _current_user_id(current_user)
    if role in ("owner", "superadmin", "root"):
        return
    if uid is None:
        raise HTTPException(status_code=403, detail="Нет доступа")

    if role == "admin":
        school_id = await get_school_id_for_user(current_user)
        if school_id is None:
            raise HTTPException(status_code=403, detail="Нет доступа")
        await cursor.execute(
            "SELECT 1 FROM classes WHERE id = %s AND school_id = %s LIMIT 1",
            (class_id, school_id),
        )
    elif role == "teacher":
        await cursor.execute(
            """
            SELECT 1 FROM class_teachers
            WHERE class_id = %s AND teacher_id = %s
            UNION
            SELECT 1 FROM class_subject_teachers
            WHERE class_id = %s AND teacher_id = %s
            LIMIT 1
            """,
            (class_id, uid, class_id, uid),
        )
    elif role == "student":
        await cursor.execute(
            "SELECT 1 FROM class_students WHERE class_id = %s AND student_id = %s LIMIT 1",
            (class_id, uid),
        )
    elif role == "parent":
        await cursor.execute(
            """
            SELECT 1
            FROM parent_students ps
            JOIN class_students cs ON cs.student_id = ps.student_id
            WHERE ps.parent_id = %s AND cs.class_id = %s
            LIMIT 1
            """,
            (uid, class_id),
        )
    else:
        raise HTTPException(status_code=403, detail="Нет доступа")

    if await cursor.fetchone() is None:
        raise HTTPException(status_code=403, detail="Нет доступа")


async def check_analytics_access(current_user: User, school_id: int = None, student_id: int = None):
    """Check if user has access to analytics features"""
    uid = current_user.id if hasattr(current_user, "id") else current_user.get("id")
    # Students can always view their own analytics data
    if current_user.role == "student" and student_id is not None and uid == student_id:
        return True
    # Parents can view their linked children's analytics
    if current_user.role == "parent" and student_id is not None:
        conn = await get_db_connection()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT 1 FROM parent_students WHERE parent_id = %s AND student_id = %s",
                    (uid, student_id),
                )
                if await cursor.fetchone():
                    return True
        finally:
            conn.close()
        raise HTTPException(status_code=403, detail="Not your child")

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
    lesson_topic: Optional[str] = None
    grade_reason: Optional[str] = None
    comment: Optional[str] = None


class FinalGradeEntry(BaseModel):
    student_id: int
    value: int
    source_average: Optional[float] = None


class FinalGradesBulkUpdate(BaseModel):
    subject_id: int
    academic_period_id: Optional[int] = None
    grades: List[FinalGradeEntry]


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
    submission_deadline_at: Optional[str] = None
    description: str
    need_submission: bool = False
    assessment_type: Optional[str] = None
    prep_checklist: List[str] = []
    attachment_name: Optional[str] = None
    attachment_path: Optional[str] = None


class HomeworkSubmissionUpdate(BaseModel):
    grade: Optional[int] = None
    is_reviewed: Optional[bool] = None
    review_status: Optional[str] = None
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


class ClassUpdate(BaseModel):
    name: str
    academic_year: Optional[str] = None


def _require_admin(current_user):
    role = current_user.get("role", "") if isinstance(current_user, dict) else getattr(current_user, "role", "")
    if role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Только админ может выполнить это действие")


def _user_id(current_user):
    return current_user.get("id") if isinstance(current_user, dict) else getattr(current_user, "id", None)


async def _get_class_for_admin(cursor, class_id: int, current_user):
    school_id = await get_school_id_for_user(current_user)
    if school_id is not None:
        await cursor.execute(
            "SELECT id, name, academic_year, school_id FROM classes WHERE id = %s AND school_id = %s",
            (class_id, school_id),
        )
    else:
        await cursor.execute(
            "SELECT id, name, academic_year, school_id FROM classes WHERE id = %s",
            (class_id,),
        )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Класс не найден")
    return row


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
    _require_admin(current_user)

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            cls = await _get_class_for_admin(cursor, class_id, current_user)
            school_id = cls.get("school_id") if isinstance(cls, dict) else cls[3]
            await cursor.execute(
                "UPDATE classes SET is_archived = %s WHERE id = %s",
                (1 if archived else 0, class_id)
            )
            await conn.commit()
            if cursor.rowcount == 0:
                raise HTTPException(404, "Class not found")
            try:
                uid = current_user.get("id") if isinstance(current_user, dict) else getattr(current_user, "id", None)
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


@router.put("/classes/{class_id}")
async def update_class(class_id: int, payload: ClassUpdate, current_user=Depends(get_current_user)):
    """Rename a class and optionally update academic year. Admin only."""
    _require_admin(current_user)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название класса обязательно")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            cls = await _get_class_for_admin(cursor, class_id, current_user)
            school_id = cls.get("school_id") if isinstance(cls, dict) else cls[3]
            academic_year = payload.academic_year.strip() if payload.academic_year else None

            if school_id is not None:
                await cursor.execute(
                    """SELECT id FROM classes
                       WHERE id <> %s AND school_id = %s AND name = %s
                       LIMIT 1""",
                    (class_id, school_id, name),
                )
            else:
                await cursor.execute(
                    """SELECT id FROM classes
                       WHERE id <> %s AND school_id IS NULL AND name = %s
                       LIMIT 1""",
                    (class_id, name),
                )
            if await cursor.fetchone():
                raise HTTPException(
                    status_code=400,
                    detail="Класс с таким названием уже существует",
                )

            if academic_year:
                await cursor.execute(
                    """UPDATE classes
                       SET name = %s, academic_year = %s
                       WHERE id = %s""",
                    (name, academic_year, class_id),
                )
            else:
                await cursor.execute(
                    "UPDATE classes SET name = %s WHERE id = %s",
                    (name, class_id),
                )
            await cursor.execute(
                """INSERT INTO audit_log (school_id, user_id, action, entity_type, entity_id, details)
                   VALUES (%s, %s, 'class_updated', 'class', %s, %s)""",
                (school_id, _user_id(current_user), class_id, json.dumps({"name": name}, ensure_ascii=False)),
            )
            await conn.commit()
            return {"status": "ok", "id": class_id, "name": name}
    except HTTPException:
        raise
    except Exception:
        await conn.rollback()
        raise
    finally:
        conn.close()


@router.delete("/classes/{class_id}")
async def delete_class(class_id: int, current_user=Depends(get_current_user)):
    """Delete an empty class, or archive it when grade history exists."""
    _require_admin(current_user)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            cls = await _get_class_for_admin(cursor, class_id, current_user)
            school_id = cls.get("school_id") if isinstance(cls, dict) else cls[3]
            await cursor.execute(
                "SELECT COUNT(*) AS count FROM grades WHERE class_id = %s",
                (class_id,),
            )
            grade_count_row = await cursor.fetchone()
            grade_count = int(
                grade_count_row.get("count", 0)
                if isinstance(grade_count_row, dict)
                else grade_count_row[0]
            )
            if grade_count > 0:
                await cursor.execute(
                    "UPDATE classes SET is_archived = 1 WHERE id = %s",
                    (class_id,),
                )
                await cursor.execute(
                    """INSERT INTO audit_log (school_id, user_id, action, entity_type, entity_id, details)
                       VALUES (%s, %s, 'class_delete_archived', 'class', %s, %s)""",
                    (
                        school_id,
                        _user_id(current_user),
                        class_id,
                        json.dumps({"grade_count": grade_count}, ensure_ascii=False),
                    ),
                )
                await conn.commit()
                return {
                    "status": "archived",
                    "id": class_id,
                    "grade_count": grade_count,
                }
            await cursor.execute("SELECT id FROM homeworks WHERE class_id = %s", (class_id,))
            homework_rows = await cursor.fetchall()
            homework_ids = [
                row.get("id") if isinstance(row, dict) else row[0]
                for row in homework_rows
            ]
            if homework_ids:
                placeholders = ",".join(["%s"] * len(homework_ids))
                await cursor.execute(
                    f"DELETE FROM homework_submissions WHERE homework_id IN ({placeholders})",
                    tuple(homework_ids),
                )
                await cursor.execute(
                    f"DELETE FROM homework_done_marks WHERE homework_id IN ({placeholders})",
                    tuple(homework_ids),
                )
            for table in (
                "teacher_lesson_feedback",
                "timetable_changes",
                "timetable_templates",
                "curriculum",
                "class_subject_teachers",
                "class_subjects",
                "class_teachers",
                "class_students",
                "homeworks",
            ):
                try:
                    await cursor.execute(f"DELETE FROM {table} WHERE class_id = %s", (class_id,))
                except Exception:
                    pass
            try:
                await cursor.execute(
                    "UPDATE announcements SET target_class_id = NULL, target_type = 'all' WHERE target_class_id = %s",
                    (class_id,),
                )
            except Exception:
                pass
            await cursor.execute(
                """INSERT INTO audit_log (school_id, user_id, action, entity_type, entity_id, details)
                   VALUES (%s, %s, 'class_deleted', 'class', %s, NULL)""",
                (school_id, _user_id(current_user), class_id),
            )
            await cursor.execute("DELETE FROM classes WHERE id = %s", (class_id,))
            await conn.commit()
            return {"status": "deleted", "id": class_id}
    except HTTPException:
        raise
    except Exception:
        await conn.rollback()
        raise
    finally:
        conn.close()


@router.get("/subjects", response_model=List[str])
async def list_subjects(
    school_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user),
):
    """Return list of all subject names."""
    resolved_school_id = await resolve_school_id_for_user(
        current_user,
        school_id,
    )
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT s.name
                FROM school_subjects ss
                JOIN subjects s ON s.id = ss.subject_id
                WHERE ss.school_id = %s
                ORDER BY s.name
                """,
                (resolved_school_id,),
            )
            rows = await cursor.fetchall()
            return [r["name"] if isinstance(r, dict) else r[0] for r in rows]
    finally:
        conn.close()


@router.get("/subjects-with-coefficients", response_model=List[Subject])
async def list_subjects_with_coefficients(
    school_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user),
):
    """Return list of all subjects with their coefficients."""
    resolved_school_id = await resolve_school_id_for_user(
        current_user,
        school_id,
    )
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT s.id, s.name, ss.coefficient
                FROM school_subjects ss
                JOIN subjects s ON s.id = ss.subject_id
                WHERE ss.school_id = %s
                ORDER BY s.name
                """,
                (resolved_school_id,),
            )
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
    school_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user)
):
    """Create a new subject with coefficient"""
    _require_admin(current_user)
    resolved_school_id = await resolve_school_id_for_user(
        current_user,
        school_id,
    )
    name = subject_data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название обязательно")
    if subject_data.coefficient <= 0 or subject_data.coefficient > 10:
        raise HTTPException(
            status_code=400,
            detail="Коэффициент должен быть больше 0 и не больше 10",
        )
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT id FROM subjects WHERE name = %s",
                (name,),
            )
            subject = await cursor.fetchone()
            if subject:
                subject_id = int(
                    subject["id"] if isinstance(subject, dict) else subject[0]
                )
            else:
                await cursor.execute(
                    "INSERT INTO subjects (name, coefficient) VALUES (%s, %s)",
                    (name, subject_data.coefficient),
                )
                subject_id = int(cursor.lastrowid)
            await cursor.execute(
                """
                INSERT INTO school_subjects
                    (school_id, subject_id, coefficient)
                VALUES (%s, %s, %s)
                """,
                (resolved_school_id, subject_id, subject_data.coefficient),
            )
            await conn.commit()
            return {
                "message": (
                    f"Subject '{name}' created with coefficient "
                    f"{subject_data.coefficient}"
                )
            }
    except Exception as exc:
        await conn.rollback()
        if getattr(exc, "args", [None])[0] == 1062:
            raise HTTPException(
                status_code=400,
                detail=f"Subject '{name}' already exists",
            )
        raise
    finally:
        conn.close()


@router.put("/subjects/{subject_name}/coefficient")
async def update_subject_coefficient(
    subject_name: str, 
    update_data: SubjectCoefficientUpdate,
    school_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user)
):
    """Update coefficient for a specific subject."""
    _require_admin(current_user)
    resolved_school_id = await resolve_school_id_for_user(
        current_user,
        school_id,
    )
    if update_data.coefficient <= 0 or update_data.coefficient > 10:
        raise HTTPException(
            status_code=400,
            detail="Коэффициент должен быть больше 0 и не больше 10",
        )
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем, существует ли предмет
            await cursor.execute(
                """
                SELECT s.id
                FROM school_subjects ss
                JOIN subjects s ON s.id = ss.subject_id
                WHERE ss.school_id = %s AND s.name = %s
                """,
                (resolved_school_id, subject_name),
            )
            subject = await cursor.fetchone()
            
            if not subject:
                raise HTTPException(status_code=404, detail="Subject not found")
            
            # Обновляем коэффициент
            await cursor.execute(
                """
                UPDATE school_subjects
                SET coefficient = %s
                WHERE school_id = %s AND subject_id = %s
                """,
                (
                    update_data.coefficient,
                    resolved_school_id,
                    subject["id"] if isinstance(subject, dict) else subject[0],
                ),
            )
            await conn.commit()
            
            return {"message": f"Coefficient for subject '{subject_name}' updated to {update_data.coefficient}"}
    finally:
        conn.close()


@router.get("/points/table")
async def get_points_table(current_user=Depends(get_current_user)):
    await require_school_feature(
        current_user,
        "ratings_enabled",
        detail="Рейтинги отключены в настройках школы",
    )
    """Get the points table showing how grades are converted to points."""
    from app.utils.points_calculator import PointsCalculator
    
    return {
        "points_table": PointsCalculator.get_points_table(),
        "description": "Баллы рассчитываются как: базовый_балл × коэффициент_предмета"
    }


@router.get("/students/points-leaderboard", response_model=List[dict])
async def get_students_points_leaderboard(
    school_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user),
):
    resolved_school_id = await resolve_school_id_for_user(
        current_user,
        school_id,
    )
    await require_school_feature(
        current_user,
        "ratings_enabled",
        school_id=resolved_school_id,
        detail="Рейтинги отключены в настройках школы",
    )
    """Get students leaderboard based on calculated points from grades with subject coefficients."""
    from app.utils.points_calculator import PointsCalculator
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Получаем все оценки студентов с предметами
            await cursor.execute("""
                SELECT 
                    u.id AS student_id,
                    TRIM(BOTH '"' FROM p.full_name) AS student_name,
                    s.name AS subject,
                    g.value AS grade,
                    ss.coefficient
                FROM school_users su
                JOIN users u ON u.id = su.user_id AND u.role = 'student'
                LEFT JOIN profiles p ON u.id = p.user_id
                JOIN grades g ON u.id = g.student_id
                JOIN subjects s ON s.id = g.subject_id
                JOIN school_subjects ss
                  ON ss.school_id = su.school_id
                 AND ss.subject_id = g.subject_id
                WHERE su.school_id = %s
                ORDER BY u.id, g.date
            """, (resolved_school_id,))
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
                    'grade': row['grade'],
                    'coefficient': float(row['coefficient']),
                })
            
            # Рассчитываем баллы для каждого студента
            result = []
            for student_id, data in student_grades.items():
                total_points = 0
                grades_data = []
                
                for grade_info in data['grades']:
                    coefficient = grade_info['coefficient']
                    
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
async def get_teachers_rating_by_grades(
    school_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user),
):
    resolved_school_id = await resolve_school_id_for_user(
        current_user,
        school_id,
    )
    await require_school_feature(
        current_user,
        "ratings_enabled",
        school_id=resolved_school_id,
        detail="Рейтинги отключены в настройках школы",
    )
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
                JOIN school_users teacher_school
                  ON teacher_school.user_id = u.id
                 AND teacher_school.school_id = %s
                JOIN school_users student_school
                  ON student_school.user_id = g.student_id
                 AND student_school.school_id = %s
                LEFT JOIN profiles p ON p.user_id = u.id
                GROUP BY u.id, p.full_name
                HAVING COUNT(*) > 0
                ORDER BY average_grade DESC, grade_count DESC
            """, (resolved_school_id, resolved_school_id))
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
            result = []
            if rows:
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
            # Добавляем классы из старой связи, если для них нет предметной
            # привязки. Раньше наличие хотя бы одной предметной записи полностью
            # скрывало остальные классы учителя.
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
                  AND NOT EXISTS (
                      SELECT 1
                      FROM class_subject_teachers cst
                      WHERE cst.teacher_id = ct.teacher_id
                        AND cst.class_id = ct.class_id
                  )
                ORDER BY (avg_grade IS NULL), avg_grade DESC, c.name
                """,
                (teacher_id,)
            )
            rows = await cursor.fetchall()
            if rows:
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
            if result:
                return result
            # Последний fallback: предметы учителя, назначенные классу.
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


async def _get_current_academic_period_bounds(school_id: int):
    """Возвращает (start_date, end_date). Если период в прошлом — умолчание."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT start_date, end_date FROM academic_periods
                WHERE school_id = %s AND is_active = TRUE
                ORDER BY end_date DESC LIMIT 1
                """,
                (school_id,),
            )
            row = await cursor.fetchone()
            if not row:
                await cursor.execute(
                    """
                    SELECT start_date, end_date FROM academic_periods
                    WHERE school_id = %s
                    ORDER BY end_date DESC LIMIT 1
                    """,
                    (school_id,),
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


async def _get_current_academic_period_id(cursor, class_id: int) -> int:
    today = date_type.today().isoformat()
    await cursor.execute(
        """
        SELECT id FROM academic_periods
        WHERE school_id = (SELECT school_id FROM classes WHERE id = %s)
          AND is_active = TRUE
          AND start_date <= %s AND end_date >= %s
        ORDER BY end_date DESC
        LIMIT 1
        """,
        (class_id, today, today),
    )
    row = await cursor.fetchone()
    if not row:
        await cursor.execute(
            """
            SELECT id FROM academic_periods
            WHERE school_id = (SELECT school_id FROM classes WHERE id = %s)
            ORDER BY end_date DESC LIMIT 1
            """,
            (class_id,),
        )
        row = await cursor.fetchone()
    if not row:
        raise HTTPException(
            status_code=400,
            detail="Учебный период не настроен",
        )
    return int(row["id"] if isinstance(row, dict) else row[0])


async def _ensure_final_grade_access(cursor, current_user, class_id: int, subject_id: int):
    role = _current_user_role(current_user).lower().strip()
    if role in ("owner", "superadmin", "root"):
        return
    if role == "admin":
        await _get_class_for_admin(cursor, class_id, current_user)
        return
    if role != "teacher":
        raise HTTPException(status_code=403, detail="Нет доступа")
    teacher_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    await cursor.execute(
        "SELECT 1 FROM class_subject_teachers WHERE class_id=%s AND subject_id=%s AND teacher_id=%s",
        (class_id, subject_id, teacher_id),
    )
    allowed = await cursor.fetchone()
    if not allowed:
        await cursor.execute(
            "SELECT 1 FROM class_teachers WHERE class_id=%s AND teacher_id=%s",
            (class_id, teacher_id),
        )
        allowed = await cursor.fetchone()
    if not allowed:
        await cursor.execute(
            """SELECT 1 FROM teacher_subjects ts
               JOIN class_subjects cs ON cs.subject_id = ts.subject_id AND cs.class_id = %s
               WHERE ts.teacher_id = %s AND ts.subject_id = %s""",
            (class_id, teacher_id, subject_id),
        )
        allowed = await cursor.fetchone()
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="Нет доступа к итоговым оценкам этого класса и предмета",
        )


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
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await _ensure_can_read_class(cursor, current_user, class_id)
            await cursor.execute(
                "SELECT school_id FROM classes WHERE id = %s",
                (class_id,),
            )
            export_class = await cursor.fetchone()
            export_school_id = (
                export_class.get("school_id")
                if isinstance(export_class, dict)
                else export_class[0] if export_class else None
            )
            if export_school_id is None:
                raise HTTPException(status_code=404, detail="Школа класса не найдена")
            # Для «всех» оценок — ограничиваем периодом семестра этой школы.
            if date_from is None and date_to is None:
                period_start, period_end = await _get_current_academic_period_bounds(
                    int(export_school_id)
                )
                date_from = period_start or date_from
                date_to = period_end or date_to
            if export_school_id is not None:
                await consume_school_quota(
                    cursor,
                    school_id=export_school_id,
                    entitlement_key="exports.csv",
                    user_id=teacher_id,
                )
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
                SELECT TRIM(BOTH '"' FROM p.full_name) as student_name, cs.student_id, g.id as grade_id, g.date, g.value, s.name as subject_name
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
                    "student_id": data["student_id"],
                    "grades": data["grades"],
                    "average": avg,
                })
            await conn.commit()
            return result
    finally:
        conn.close()


@router.get("/classes/{class_id}/final-grades")
async def get_class_final_grades(
    class_id: int,
    subject_id: int = Query(..., description="Предмет итоговой оценки"),
    academic_period_id: Optional[int] = Query(None, description="Учебный период"),
    current_user=Depends(get_current_user),
):
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await _ensure_final_grade_access(cursor, current_user, class_id, subject_id)
            period_id = academic_period_id or await _get_current_academic_period_id(
                cursor,
                class_id,
            )
            await cursor.execute(
                """
                SELECT 1
                FROM academic_periods ap
                JOIN classes c ON c.school_id = ap.school_id
                WHERE ap.id = %s AND c.id = %s
                """,
                (period_id, class_id),
            )
            if not await cursor.fetchone():
                raise HTTPException(
                    status_code=400,
                    detail="Учебный период не относится к школе класса",
                )
            await cursor.execute(
                """
                SELECT
                    fg.id,
                    fg.student_id,
                    fg.value,
                    fg.source_average,
                    fg.teacher_id,
                    fg.updated_at,
                    COALESCE(NULLIF(TRIM(BOTH '"' FROM p.full_name), ''), u.username) AS teacher_name
                FROM final_grades fg
                LEFT JOIN users u ON u.id = fg.teacher_id
                LEFT JOIN profiles p ON p.user_id = fg.teacher_id
                WHERE fg.class_id = %s
                  AND fg.subject_id = %s
                  AND fg.academic_period_id = %s
                ORDER BY fg.student_id
                """,
                (class_id, subject_id, period_id),
            )
            rows = await cursor.fetchall()
            return {
                "academic_period_id": period_id,
                "items": [
                    {
                        "id": row["id"],
                        "student_id": row["student_id"],
                        "value": row["value"],
                        "source_average": float(row["source_average"]) if row.get("source_average") is not None else None,
                        "teacher_id": row["teacher_id"],
                        "teacher_name": row.get("teacher_name") or "Учитель",
                        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
                    }
                    for row in rows
                ],
            }
    finally:
        conn.close()


@router.post("/classes/{class_id}/final-grades")
async def save_class_final_grades(
    class_id: int,
    payload: FinalGradesBulkUpdate,
    current_user=Depends(get_current_user),
):
    if not payload.grades:
        raise HTTPException(status_code=400, detail="Нет итоговых оценок")
    invalid = [g.value for g in payload.grades if g.value < 2 or g.value > 5]
    if invalid:
        raise HTTPException(status_code=400, detail="Итоговая оценка должна быть от 2 до 5")

    teacher_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    student_ids = [g.student_id for g in payload.grades]
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await _ensure_final_grade_access(cursor, current_user, class_id, payload.subject_id)
            period_id = payload.academic_period_id or await _get_current_academic_period_id(
                cursor,
                class_id,
            )
            await cursor.execute(
                """
                SELECT 1
                FROM academic_periods ap
                JOIN classes c ON c.school_id = ap.school_id
                WHERE ap.id = %s AND c.id = %s
                """,
                (period_id, class_id),
            )
            if not await cursor.fetchone():
                raise HTTPException(
                    status_code=400,
                    detail="Учебный период не относится к школе класса",
                )
            await cursor.execute(
                "SELECT student_id FROM class_students WHERE class_id = %s AND student_id IN ({})".format(
                    ",".join(["%s"] * len(student_ids))
                ),
                tuple([class_id, *student_ids]),
            )
            rows = await cursor.fetchall()
            allowed_student_ids = {
                int(row["student_id"] if isinstance(row, dict) else row[0])
                for row in rows
            }
            missing = [sid for sid in student_ids if sid not in allowed_student_ids]
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail="Не все ученики относятся к этому классу",
                )

            for item in payload.grades:
                await cursor.execute(
                    """
                    INSERT INTO final_grades (
                        student_id, class_id, subject_id, academic_period_id,
                        value, source_average, teacher_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        value = VALUES(value),
                        source_average = VALUES(source_average),
                        teacher_id = VALUES(teacher_id),
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        item.student_id,
                        class_id,
                        payload.subject_id,
                        period_id,
                        item.value,
                        round(item.source_average, 2)
                        if item.source_average is not None
                        else None,
                        teacher_id,
                    ),
                )
            await conn.commit()
            return {
                "status": "ok",
                "academic_period_id": period_id,
                "updated": len(payload.grades),
            }
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
            await _ensure_can_read_class(cursor, current_user, class_id)
            subject_join = "AND g.subject_id = %s" if subject_id else ""
            params = [subject_id, class_id] if subject_id else [class_id]
            await cursor.execute(
                f"""
                SELECT
                    cs.student_id,
                    COALESCE(
                        NULLIF(TRIM(BOTH '"' FROM p.full_name), ''),
                        u.username,
                        CONCAT('Student ', cs.student_id)
                    ) AS student_name,
                    COALESCE(
                        p.photo_url,
                        CONCAT('/api/v1/profile_photo/', cs.student_id)
                    ) AS photo_url,
                    c.name AS class_name,
                    AVG(
                        CASE
                            WHEN g.date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                            THEN g.value
                        END
                    ) AS current_avg,
                    AVG(
                        CASE
                            WHEN g.date >= DATE_SUB(CURDATE(), INTERVAL 60 DAY)
                             AND g.date < DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                            THEN g.value
                        END
                    ) AS previous_avg
                FROM class_students cs
                JOIN classes c ON c.id = cs.class_id
                LEFT JOIN users u ON u.id = cs.student_id
                LEFT JOIN profiles p ON p.user_id = cs.student_id
                LEFT JOIN grades g
                  ON g.student_id = cs.student_id
                 {subject_join}
                WHERE cs.class_id = %s
                GROUP BY
                    cs.student_id,
                    p.full_name,
                    p.photo_url,
                    u.username,
                    c.name
                ORDER BY student_name
                """,
                tuple(params),
            )
            rows = await cursor.fetchall()
            result = []
            for row in rows:
                current_value = row.get("current_avg")
                previous_value = row.get("previous_avg")
                current_avg = (
                    float(current_value) if current_value is not None else None
                )
                previous_avg = (
                    float(previous_value) if previous_value is not None else None
                )
                decline = (
                    previous_avg - current_avg
                    if current_avg is not None and previous_avg is not None
                    else None
                )
                result.append({
                    "student_id": row["student_id"],
                    "student_name": row["student_name"],
                    "photo_url": row["photo_url"],
                    "class_name": row.get("class_name"),
                    "current_avg": current_avg,
                    "previous_avg": previous_avg,
                    "decline": decline,
                })
            return result
    finally:
        conn.close()


@router.get("/subjects/{subject_id}/teachers")
async def get_teachers_by_subject(
    subject_id: int,
    school_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user),
):
    """Список учителей, ведущих предмет (для назначения на класс)."""
    resolved_school_id = await resolve_school_id_for_user(
        current_user,
        school_id,
    )
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT u.id as teacher_id, TRIM(BOTH '"' FROM p.full_name) as teacher_name
                FROM teacher_subjects ts
                JOIN users u ON u.id = ts.teacher_id AND u.role = 'teacher'
                JOIN profiles p ON p.user_id = u.id
                JOIN school_users su
                  ON su.user_id = u.id AND su.school_id = %s
                JOIN school_subjects ss
                  ON ss.subject_id = ts.subject_id AND ss.school_id = %s
                WHERE ts.subject_id = %s
                ORDER BY p.full_name
                """,
                (resolved_school_id, resolved_school_id, subject_id),
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
            await _ensure_can_read_class(cursor, current_user, class_id)
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
            class_row = await _get_class_for_admin(cursor, class_id, current_user)
            school_id = (
                class_row.get("school_id")
                if isinstance(class_row, dict)
                else None
            )
            if school_id is None:
                await cursor.execute(
                    "SELECT school_id FROM classes WHERE id = %s",
                    (class_id,),
                )
                school_row = await cursor.fetchone()
                school_id = (
                    school_row.get("school_id")
                    if isinstance(school_row, dict)
                    else school_row[0] if school_row else None
                )
            if school_id is None:
                raise HTTPException(status_code=400, detail="У класса не указана школа")
            await ensure_school_user_membership(
                cursor,
                school_id=school_id,
                user_id=payload.teacher_id,
                role="teacher",
            )
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
            await _get_class_for_admin(cursor, class_id, current_user)
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
            await _get_class_for_admin(cursor, payload.from_class_id, current_user)
            await _get_class_for_admin(cursor, payload.to_class_id, current_user)
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
            await _get_class_for_admin(cursor, payload.from_class_id, current_user)
            await _get_class_for_admin(cursor, payload.to_class_id, current_user)
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
            await _ensure_can_read_class(cursor, current_user, class_id)
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
            await _ensure_can_read_class(cursor, current_user, class_id)
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
            await _ensure_can_read_class(cursor, current_user, class_id)
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
                "SELECT DISTINCT cs.student_id, cs.class_id FROM class_students cs WHERE cs.student_id IN ({})".format(
                    ",".join(["%s"] * len(payload.grades))
                ),
                tuple(g.student_id for g in payload.grades)
            )
            class_rows = await cursor.fetchall()
            resolved_student_ids = {
                int(r["student_id"] if isinstance(r, dict) else r[0])
                for r in class_rows
            }
            requested_student_ids = {int(g.student_id) for g in payload.grades}
            if resolved_student_ids != requested_student_ids:
                raise HTTPException(status_code=400, detail="Не все ученики найдены в классах")
            class_ids = sorted({
                int(r["class_id"] if isinstance(r, dict) else r[1])
                for r in class_rows
            })
            if len(class_ids) != 1:
                raise HTTPException(status_code=400, detail="Оценки можно выставлять только ученикам одного класса")
            class_id = class_ids[0] if class_ids else None
            await cursor.execute(
                "SELECT school_id FROM classes WHERE id = %s",
                (class_id,),
            )
            class_row = await cursor.fetchone()
            class_school_id = _row_value(class_row, "school_id", 0)
            if class_school_id is None:
                raise HTTPException(
                    status_code=400,
                    detail="У класса не указана школа",
                )
            teacher_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
            await cursor.execute(
                """
                SELECT COALESCE(NULLIF(TRIM(BOTH '"' FROM p.full_name), ''), u.username) AS teacher_name
                FROM users u
                LEFT JOIN profiles p ON p.user_id = u.id
                WHERE u.id = %s
                """,
                (teacher_id,),
            )
            teacher_row = await cursor.fetchone()
            teacher_name = teacher_row["teacher_name"] if teacher_row else "Учитель"

            # resolve subject id
            await cursor.execute(
                """
                SELECT s.id
                FROM school_subjects ss
                JOIN subjects s ON s.id = ss.subject_id
                WHERE ss.school_id = %s AND s.name = %s
                """,
                (class_school_id, payload.subject),
            )
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(
                    status_code=400,
                    detail="Предмет не добавлен в школу",
                )
            subject_id = row["id"]

            # permission check: owner/root — always; admin — own school; teacher — assigned subject/class
            role = (current_user.get("role", "") if isinstance(current_user, dict) else getattr(current_user, "role", "") or "")
            role_lower = (role or "").lower().strip()
            if role_lower in ("owner", "superadmin", "root"):
                allowed = True
            elif role_lower == "admin":
                await _get_class_for_admin(cursor, class_id, current_user)
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
            else:
                raise HTTPException(status_code=403, detail="Not allowed to grade this subject for this class")

            # insert rows
            for g in payload.grades:
                await cursor.execute(
                    """
                    INSERT INTO grades (
                        student_id, subject_id, value, date, teacher_id,
                        lesson_topic, grade_reason, comment
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        g.student_id,
                        subject_id,
                        g.value,
                        payload.date,
                        teacher_id,
                        (payload.lesson_topic or "").strip()[:255] or None,
                        (payload.grade_reason or "").strip()[:255] or None,
                        (payload.comment or "").strip()[:2000] or None,
                    )
                )
            await conn.commit()
            parent_ids = await _get_parent_ids_for_students(
                cursor,
                [g.student_id for g in payload.grades],
            )
        for g in payload.grades:
            await notify_many(
                [g.student_id],
                "Новая оценка",
                f"{teacher_name} выставил(а) {g.value} по предмету «{payload.subject}».",
                notification_type="grade",
                sender_id=teacher_id,
                entity_type="grade",
                route_name="performance",
            )
        for parent_id in parent_ids:
            await notify_many(
                [parent_id],
                "У ребенка новая оценка",
                f"Добавлены новые оценки по предмету «{payload.subject}».",
                notification_type="grade_parent",
                sender_id=teacher_id,
                entity_type="grade",
                route_name="performance",
            )
        try:
            await evaluate_class_podium(class_id)
        except Exception:
            logger.exception("Failed to evaluate podium achievements")
        return {"status": "ok", "inserted": len(payload.grades)}
    finally:
        conn.close()


class GradeUpdate(BaseModel):
    value: int


async def _ensure_can_modify_grade(
    cursor,
    current_user,
    student_id: int,
    subject_id: int,
) -> int:
    await cursor.execute(
        "SELECT class_id FROM class_students WHERE student_id = %s LIMIT 1",
        (student_id,),
    )
    class_row = await cursor.fetchone()
    if not class_row:
        raise HTTPException(
            status_code=409,
            detail="Ученик не состоит в классе; изменение оценки недоступно",
        )
    class_id = int(class_row["class_id"])
    role = _current_user_role(current_user).lower().strip()
    user_id = _current_user_id(current_user)

    if role in ("owner", "superadmin", "root"):
        return class_id
    if role == "admin":
        await _get_class_for_admin(cursor, class_id, current_user)
        return class_id
    if role != "teacher" or user_id is None:
        raise HTTPException(status_code=403, detail="Нет доступа")

    await cursor.execute(
        """
        SELECT 1
        FROM class_subject_teachers
        WHERE class_id = %s AND subject_id = %s AND teacher_id = %s
        UNION
        SELECT 1
        FROM class_teachers
        WHERE class_id = %s AND teacher_id = %s
        LIMIT 1
        """,
        (class_id, subject_id, user_id, class_id, user_id),
    )
    if not await cursor.fetchone():
        raise HTTPException(status_code=403, detail="Нет доступа")
    return class_id


async def _ensure_can_read_grade(
    cursor,
    current_user,
    student_id: int,
    subject_id: int,
) -> None:
    role = _current_user_role(current_user).lower().strip()
    user_id = _current_user_id(current_user)
    if role in ("owner", "superadmin", "root"):
        return
    if role == "student" and user_id == student_id:
        return
    if role == "parent" and user_id is not None:
        await cursor.execute(
            """
            SELECT 1
            FROM parent_students
            WHERE parent_id = %s AND student_id = %s
            LIMIT 1
            """,
            (user_id, student_id),
        )
        if await cursor.fetchone():
            return

    await cursor.execute(
        "SELECT class_id FROM class_students WHERE student_id = %s LIMIT 1",
        (student_id,),
    )
    class_row = await cursor.fetchone()
    if not class_row:
        raise HTTPException(status_code=403, detail="Нет доступа")
    class_id = int(class_row["class_id"])

    if role == "admin":
        await _get_class_for_admin(cursor, class_id, current_user)
        return
    if role == "teacher" and user_id is not None:
        await cursor.execute(
            """
            SELECT 1
            FROM class_subject_teachers
            WHERE class_id = %s AND subject_id = %s AND teacher_id = %s
            UNION
            SELECT 1
            FROM class_teachers
            WHERE class_id = %s AND teacher_id = %s
            LIMIT 1
            """,
            (class_id, subject_id, user_id, class_id, user_id),
        )
        if await cursor.fetchone():
            return
    raise HTTPException(status_code=403, detail="Нет доступа")


@router.get("/grades/{grade_id}")
async def get_grade_info(grade_id: int, current_user=Depends(get_current_user)):
    """Информация об оценке: кто и когда выставил (для истории)."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT g.id, g.value, g.date, g.teacher_id,
                       g.student_id, g.subject_id,
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
            await _ensure_can_read_grade(
                cursor,
                current_user,
                int(row["student_id"]),
                int(row["subject_id"]),
            )
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
    conn = await get_db_connection()
    class_id = None
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
            class_id = await _ensure_can_modify_grade(
                cursor,
                current_user,
                student_id,
                subject_id,
            )
            await cursor.execute(
                "UPDATE grades SET value = %s WHERE id = %s",
                (payload.value, grade_id)
            )
            await conn.commit()
        try:
            await evaluate_class_podium(class_id)
        except Exception:
            logger.exception("Failed to evaluate podium achievements")
        return {"status": "ok"}
    finally:
        conn.close()


@router.delete("/grades/{grade_id}")
async def delete_grade(grade_id: int, current_user=Depends(get_current_user)):
    """Удалить оценку. Только учитель, ведущий предмет в классе ученика."""
    conn = await get_db_connection()
    class_id = None
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
            class_id = await _ensure_can_modify_grade(
                cursor,
                current_user,
                student_id,
                subject_id,
            )
            await cursor.execute("DELETE FROM grades WHERE id = %s", (grade_id,))
            await conn.commit()
        try:
            await evaluate_class_podium(class_id)
        except Exception:
            logger.exception("Failed to evaluate podium achievements")
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
    await require_school_feature(current_user, "attendance_enabled", detail="Посещаемость отключена в настройках школы")
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
    await require_school_feature(current_user, "attendance_enabled", detail="Посещаемость отключена в настройках школы")
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
    await require_school_feature(current_user, "attendance_enabled", detail="Посещаемость отключена в настройках школы")
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
async def list_student_homeworks(
    student_id: Optional[int] = Query(None, description="ID ученика для родителя"),
    current_user=Depends(get_current_user),
):
    await require_school_feature(
        current_user,
        "homeworks_enabled",
        detail="Домашние задания отключены в настройках школы",
    )
    """Список ДЗ для ученика (по классам, в которых он учится)."""
    role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
    current_user_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    effective_student_id = current_user_id
    if role == "student":
        effective_student_id = current_user_id
    elif role == "parent":
        if student_id is None:
            raise HTTPException(status_code=400, detail="Укажите student_id")
        conn = await get_db_connection()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT 1 FROM parent_students WHERE parent_id = %s AND student_id = %s",
                    (current_user_id, student_id),
                )
                if not await cursor.fetchone():
                    raise HTTPException(status_code=403, detail="Это не ваш ребёнок")
        finally:
            conn.close()
        effective_student_id = student_id
    else:
        raise HTTPException(status_code=403, detail="Только для учеников и родителей")
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """SELECT h.id, h.class_id, h.subject_id, h.due_date, h.submission_deadline_at,
                          h.description, h.need_submission, h.assessment_type, h.prep_checklist_json,
                          h.attachment_name, h.attachment_path,
                          CASE WHEN hdm.student_id IS NULL THEN FALSE ELSE TRUE END AS is_done_without_file,
                          c.name as class_name, s.name as subject_name
                   FROM homeworks h
                   JOIN class_students cs ON h.class_id = cs.class_id AND cs.student_id = %s
                   JOIN classes c ON c.id = h.class_id
                   JOIN subjects s ON s.id = h.subject_id
                   LEFT JOIN homework_done_marks hdm ON hdm.homework_id = h.id AND hdm.student_id = %s
                   WHERE h.due_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                   ORDER BY h.due_date ASC, h.id ASC""",
                (effective_student_id, effective_student_id)
            )
            rows = await cursor.fetchall()
            return [{"id": r["id"], "class_id": r["class_id"], "subject_id": r["subject_id"],
                     "due_date": str(r["due_date"]) if r["due_date"] else None,
                     "submission_deadline_at": (
                         r["submission_deadline_at"].isoformat(sep=" ")
                         if r.get("submission_deadline_at") else None
                     ),
                     "description": r["description"], "class_name": r["class_name"],
                     "subject_name": r["subject_name"], "need_submission": bool(r.get("need_submission")),
                     "assessment_type": r.get("assessment_type"),
                     "prep_checklist": _parse_prep_checklist(r.get("prep_checklist_json")),
                     "attachment_name": r.get("attachment_name"), "attachment_path": r.get("attachment_path"),
                     "is_done_without_file": bool(r.get("is_done_without_file"))} for r in rows]
    finally:
        conn.close()


@router.get("/homeworks")
async def list_homeworks(
    class_id: int = Query(None, description="Фильтр по классу"),
    subject_id: int = Query(None, description="Фильтр по предмету"),
    current_user=Depends(get_current_user)
):
    await require_school_feature(
        current_user,
        "homeworks_enabled",
        detail="Домашние задания отключены в настройках школы",
    )
    """Список ДЗ для учителя (по своим классам/предметам)."""
    teacher_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            if class_id is not None:
                if subject_id is not None:
                    await cursor.execute(
                        """SELECT h.id, h.class_id, h.subject_id, h.due_date, h.submission_deadline_at,
                                  h.description, h.need_submission, h.teacher_id, h.assessment_type, h.prep_checklist_json,
                                  h.attachment_name, h.attachment_path,
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
                        """SELECT h.id, h.class_id, h.subject_id, h.due_date, h.submission_deadline_at,
                                  h.description, h.need_submission, h.teacher_id, h.assessment_type, h.prep_checklist_json,
                                  h.attachment_name, h.attachment_path,
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
                    """SELECT h.id, h.class_id, h.subject_id, h.due_date, h.submission_deadline_at,
                              h.description, h.need_submission, h.teacher_id, h.assessment_type, h.prep_checklist_json,
                              h.attachment_name, h.attachment_path,
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
                     "submission_deadline_at": (
                         r["submission_deadline_at"].isoformat(sep=" ")
                         if r.get("submission_deadline_at") else None
                     ),
                     "description": r["description"], "class_name": r["class_name"],
                     "subject_name": r["subject_name"], "need_submission": bool(r.get("need_submission")),
                     "assessment_type": r.get("assessment_type"),
                     "prep_checklist": _parse_prep_checklist(r.get("prep_checklist_json")),
                     "attachment_name": r.get("attachment_name"), "attachment_path": r.get("attachment_path"),
                     "is_done_without_file": False} for r in rows]
    finally:
        conn.close()


class HomeworkUpdate(BaseModel):
    due_date: str
    submission_deadline_at: Optional[str] = None
    description: str
    need_submission: bool = False
    assessment_type: Optional[str] = None
    prep_checklist: List[str] = []
    attachment_name: Optional[str] = None
    attachment_path: Optional[str] = None


class HomeworkDoneMarkPayload(BaseModel):
    marked_done: bool
    student_id: Optional[int] = None


def _normalize_assessment_type(value: Optional[str]) -> Optional[str]:
    normalized = (value or "").strip().lower()
    if normalized in {"control", "independent", "test"}:
        return normalized
    return None


def _normalize_prep_checklist(items: Optional[List[str]]) -> list[str]:
    cleaned: list[str] = []
    for raw in items or []:
        text = str(raw).strip()
        if text:
            cleaned.append(text[:255])
    return cleaned[:20]


def _parse_prep_checklist(raw_value) -> list[str]:
    if not raw_value:
        return []
    try:
        decoded = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
        if not isinstance(decoded, list):
            return []
        return _normalize_prep_checklist(decoded)
    except Exception:
        return []


@router.put("/homeworks/{homework_id}")
async def update_homework(
    homework_id: int,
    payload: HomeworkUpdate,
    current_user=Depends(get_current_user)
):
    await require_school_feature(
        current_user,
        "homeworks_enabled",
        detail="Домашние задания отключены в настройках школы",
    )
    """Редактировать ДЗ."""
    teacher_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    assessment_type = _normalize_assessment_type(payload.assessment_type)
    prep_checklist_json = json.dumps(
        _normalize_prep_checklist(payload.prep_checklist),
        ensure_ascii=False,
    ) if payload.prep_checklist else None
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
                """UPDATE homeworks
                   SET due_date = %s, submission_deadline_at = %s, description = %s, need_submission = %s,
                       assessment_type = %s, prep_checklist_json = %s,
                       attachment_name = %s, attachment_path = %s
                   WHERE id = %s""",
                (
                    payload.due_date,
                    payload.submission_deadline_at,
                    payload.description,
                    bool(payload.need_submission),
                    assessment_type,
                    prep_checklist_json,
                    payload.attachment_name,
                    payload.attachment_path,
                    homework_id,
                )
            )
            await conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()


@router.delete("/homeworks/{homework_id}")
async def delete_homework(homework_id: int, current_user=Depends(get_current_user)):
    await require_school_feature(
        current_user,
        "homeworks_enabled",
        detail="Домашние задания отключены в настройках школы",
    )
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
    await require_school_feature(
        current_user,
        "homeworks_enabled",
        detail="Домашние задания отключены в настройках школы",
    )
    """Создать домашнее задание на следующую пару (класс, предмет, дата сдачи, описание)."""
    teacher_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    assessment_type = _normalize_assessment_type(payload.assessment_type)
    prep_checklist_json = json.dumps(
        _normalize_prep_checklist(payload.prep_checklist),
        ensure_ascii=False,
    ) if payload.prep_checklist else None
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT COALESCE(NULLIF(TRIM(BOTH '"' FROM p.full_name), ''), u.username) AS teacher_name
                FROM users u
                LEFT JOIN profiles p ON p.user_id = u.id
                WHERE u.id = %s
                """,
                (teacher_id,),
            )
            teacher_row = await cursor.fetchone()
            teacher_name = teacher_row["teacher_name"] if teacher_row else "Учитель"
            await cursor.execute(
                "SELECT 1 FROM class_subject_teachers WHERE class_id=%s AND subject_id=%s AND teacher_id=%s",
                (payload.class_id, payload.subject_id, teacher_id)
            )
            if not await cursor.fetchone():
                raise HTTPException(status_code=403, detail="Нет доступа к этому классу/предмету")
            await cursor.execute("SELECT name FROM classes WHERE id = %s", (payload.class_id,))
            class_row = await cursor.fetchone()
            await cursor.execute("SELECT name FROM subjects WHERE id = %s", (payload.subject_id,))
            subject_row = await cursor.fetchone()
            await cursor.execute(
                """
                INSERT INTO homeworks (
                    class_id, subject_id, due_date, submission_deadline_at, description,
                    need_submission, assessment_type, prep_checklist_json,
                    attachment_name, attachment_path, teacher_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    payload.class_id,
                    payload.subject_id,
                    payload.due_date,
                    payload.submission_deadline_at,
                    payload.description,
                    bool(payload.need_submission),
                    assessment_type,
                    prep_checklist_json,
                    payload.attachment_name,
                    payload.attachment_path,
                    teacher_id,
                )
            )
            lid = cursor.lastrowid
            await conn.commit()
            await cursor.execute(
                "SELECT student_id FROM class_students WHERE class_id = %s",
                (payload.class_id,),
            )
            student_rows = await cursor.fetchall()
            student_ids = [int(row["student_id"]) for row in student_rows]
            parent_ids = await _get_parent_ids_for_students(cursor, student_ids)
        class_name = class_row["name"] if class_row else ""
        subject_name = subject_row["name"] if subject_row else ""
        title = "Новое домашнее задание"
        body = (
            f"{teacher_name} добавил(а) ДЗ по предмету «{subject_name}»"
            f"{' для ' + class_name if class_name else ''}. Срок: {payload.due_date}."
        )
        await notify_many(
            student_ids,
            title,
            body,
            notification_type="homework",
            sender_id=teacher_id,
            entity_type="homework",
            entity_id=lid,
            route_name="homework",
            route_params={"homework_id": lid},
        )
        await notify_many(
            parent_ids,
            "Новое ДЗ у ребенка",
            f"Появилось новое домашнее задание по предмету «{subject_name}». Срок: {payload.due_date}.",
            notification_type="homework_parent",
            sender_id=teacher_id,
            entity_type="homework",
            entity_id=lid,
            route_name="homework",
            route_params={"homework_id": lid},
        )
        return {"status": "ok", "id": lid}
    finally:
        conn.close()


@router.post("/homeworks/{homework_id}/attachment")
async def upload_homework_attachment(
    homework_id: int,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    await require_school_feature(
        current_user,
        "homeworks_enabled",
        detail="Домашние задания отключены в настройках школы",
    )
    """Учитель загружает вложение к ДЗ."""
    teacher_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("teacher", "admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Только для учителей")
    original_filename, ext = validate_upload_metadata(
        file,
        allowed_exts=_ALLOWED_HW_ATTACHMENT_EXTS,
    )

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT h.teacher_id, h.attachment_path, c.school_id
                FROM homeworks h
                JOIN classes c ON c.id = h.class_id
                WHERE h.id = %s
                """,
                (homework_id,),
            )
            hw = await cursor.fetchone()
            if not hw:
                raise HTTPException(status_code=404, detail="ДЗ не найдено")
            await _ensure_staff_can_access_homework_row(hw, current_user)

        fname = f"{homework_id}_{uuid.uuid4().hex[:12]}.{ext}"
        dir_path = os.path.join(settings.HOMEWORK_ATTACHMENTS_DIR, str(homework_id))
        os.makedirs(dir_path, exist_ok=True)
        file_path = os.path.join(dir_path, fname)
        written = await save_upload_limited(
            file,
            file_path,
            max_bytes=MAX_DOCUMENT_BYTES,
        )
        rel_path = f"{homework_id}/{fname}"

        old_rel_path = hw.get("attachment_path")
        old_full_path = None
        old_size = 0
        if old_rel_path:
            old_full_path = safe_storage_path(settings.HOMEWORK_ATTACHMENTS_DIR, old_rel_path)
            if os.path.isfile(old_full_path):
                old_size = os.path.getsize(old_full_path)

        try:
            async with conn.cursor() as cursor:
                await change_school_storage(
                    cursor,
                    school_id=hw["school_id"],
                    delta_bytes=written - old_size,
                )
                await cursor.execute(
                    "UPDATE homeworks SET attachment_name = %s, attachment_path = %s WHERE id = %s",
                    (original_filename, rel_path, homework_id),
                )
                await conn.commit()
        except Exception:
            if os.path.isfile(file_path):
                os.remove(file_path)
            await conn.rollback()
            raise
        if old_full_path and os.path.isfile(old_full_path):
            try:
                os.remove(old_full_path)
            except OSError:
                pass
        return {"status": "ok", "attachment_name": original_filename, "attachment_path": rel_path}
    finally:
        conn.close()


@router.get("/homeworks/{homework_id}/attachment/file")
async def download_homework_attachment(
    homework_id: int,
    current_user=Depends(get_current_user),
):
    await require_school_feature(
        current_user,
        "homeworks_enabled",
        detail="Домашние задания отключены в настройках школы",
    )
    """Скачать вложение к ДЗ."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """SELECT h.attachment_path, h.attachment_name, h.teacher_id, c.school_id
                   FROM homeworks h
                   JOIN classes c ON c.id = h.class_id
                   WHERE h.id = %s""",
                (homework_id,),
            )
            row = await cursor.fetchone()
        if not row or not row.get("attachment_path"):
            raise HTTPException(status_code=404, detail="Вложение не найдено")

        uid = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
        role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
        if role in ("admin", "owner", "superadmin", "root", "teacher"):
            await _ensure_staff_can_access_homework_row(row, current_user)
        elif role == "student":
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT 1
                       FROM homeworks h
                       JOIN class_students cs ON h.class_id = cs.class_id AND cs.student_id = %s
                       WHERE h.id = %s""",
                    (uid, homework_id),
                )
                if not await cursor.fetchone():
                    raise HTTPException(status_code=403, detail="Нет доступа")
        elif role == "parent":
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT 1
                       FROM homeworks h
                       JOIN class_students cs ON h.class_id = cs.class_id
                       JOIN parent_students ps ON ps.student_id = cs.student_id
                       WHERE h.id = %s AND ps.parent_id = %s""",
                    (homework_id, uid),
                )
                if not await cursor.fetchone():
                    raise HTTPException(status_code=403, detail="Нет доступа")
        else:
            raise HTTPException(status_code=403, detail="Нет доступа")

        full_path = safe_storage_path(settings.HOMEWORK_ATTACHMENTS_DIR, row["attachment_path"])
        if not os.path.isfile(full_path):
            raise HTTPException(status_code=404, detail="Файл не найден на диске")
        media = "application/octet-stream"
        lower_path = full_path.lower()
        if lower_path.endswith((".jpg", ".jpeg")):
            media = "image/jpeg"
        elif lower_path.endswith(".png"):
            media = "image/png"
        elif lower_path.endswith(".pdf"):
            media = "application/pdf"
        return FileResponse(full_path, media_type=media, filename=row["attachment_name"])
    finally:
        conn.close()


@router.delete("/homeworks/{homework_id}/attachment")
async def delete_homework_attachment(
    homework_id: int,
    current_user=Depends(get_current_user),
):
    await require_school_feature(
        current_user,
        "homeworks_enabled",
        detail="Домашние задания отключены в настройках школы",
    )
    """Удалить вложение у ДЗ."""
    teacher_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("teacher", "admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Только для учителей")
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT h.teacher_id, h.attachment_path, c.school_id
                FROM homeworks h
                JOIN classes c ON c.id = h.class_id
                WHERE h.id = %s
                """,
                (homework_id,),
            )
            hw = await cursor.fetchone()
            if not hw:
                raise HTTPException(status_code=404, detail="ДЗ не найдено")
            await _ensure_staff_can_access_homework_row(hw, current_user)
            old_rel_path = hw.get("attachment_path")
            old_full_path = (
                safe_storage_path(settings.HOMEWORK_ATTACHMENTS_DIR, old_rel_path)
                if old_rel_path
                else None
            )
            old_size = (
                os.path.getsize(old_full_path)
                if old_full_path and os.path.isfile(old_full_path)
                else 0
            )
            if old_size:
                await change_school_storage(
                    cursor,
                    school_id=hw["school_id"],
                    delta_bytes=-old_size,
                )
            await cursor.execute(
                "UPDATE homeworks SET attachment_name = NULL, attachment_path = NULL WHERE id = %s",
                (homework_id,),
            )
            await conn.commit()

        if old_full_path and os.path.isfile(old_full_path):
            try:
                os.remove(old_full_path)
            except OSError:
                pass
        return {"status": "ok"}
    finally:
        conn.close()


# ========== Homework submissions (фото/файлы от учеников) ==========

_ALLOWED_HW_SUBMISSION_EXTS = (IMAGE_EXTS - {"gif", "webp"}) | {"pdf", "doc", "docx"}
_ALLOWED_HW_ATTACHMENT_EXTS = (IMAGE_EXTS - {"gif", "webp"}) | DOCUMENT_EXTS
_ALLOWED_HW_REVIEW_STATUSES = {"pending", "reviewed", "rework"}
_DEFAULT_HW_REVIEW_WORKFLOW = {
    "rework_enabled": True,
    "labels": {
        "pending": "Ждёт проверки",
        "reviewed": "Проверено",
        "rework": "На доработку",
        "done_without_file": "Сделал",
        "done_without_file_marked": "Отмечено как сделано",
        "file_submitted": "Есть файл",
    },
}


def _normalize_homework_review_workflow(raw_settings: Optional[dict]) -> dict:
    merged = json.loads(json.dumps(_DEFAULT_HW_REVIEW_WORKFLOW))
    if not isinstance(raw_settings, dict):
        return merged
    merged["rework_enabled"] = bool(raw_settings.get("rework_enabled", True))
    labels = raw_settings.get("labels")
    if isinstance(labels, dict):
        for key, default_value in merged["labels"].items():
            value = labels.get(key)
            if isinstance(value, str) and value.strip():
                merged["labels"][key] = value.strip()[:80]
            else:
                merged["labels"][key] = default_value
    return merged


async def _get_homework_review_workflow(cursor, school_id: Optional[int]) -> dict:
    if not school_id:
        return json.loads(json.dumps(_DEFAULT_HW_REVIEW_WORKFLOW))
    await cursor.execute(
        "SELECT workflow_settings FROM school_settings WHERE school_id = %s",
        (school_id,),
    )
    row = await cursor.fetchone()
    raw = row.get("workflow_settings") if row and isinstance(row, dict) else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return _normalize_homework_review_workflow(raw.get("homework_review"))


def _resolve_homework_review_status(raw_status: Optional[str], is_reviewed: Optional[bool]) -> str:
    status = (raw_status or "").strip().lower()
    if status in _ALLOWED_HW_REVIEW_STATUSES:
        return status
    return "reviewed" if bool(is_reviewed) else "pending"


def _resolve_homework_submission_deadline(row: Optional[dict]) -> Optional[datetime]:
    if not row:
        return None
    raw_deadline = row.get("submission_deadline_at")
    if isinstance(raw_deadline, datetime):
        return raw_deadline
    if isinstance(raw_deadline, str) and raw_deadline.strip():
        return datetime.fromisoformat(raw_deadline.strip())

    raw_due_date = row.get("due_date")
    if isinstance(raw_due_date, datetime):
        return datetime.combine(raw_due_date.date(), time(23, 59, 59))
    if isinstance(raw_due_date, date_type):
        return datetime.combine(raw_due_date, time(23, 59, 59))
    if isinstance(raw_due_date, str) and raw_due_date.strip():
        return datetime.combine(
            datetime.fromisoformat(raw_due_date.strip()).date(),
            time(23, 59, 59),
        )
    return None


@router.post("/homeworks/{homework_id}/submissions")
async def upload_homework_submission(
    homework_id: int,
    file: UploadFile = File(...),
    student_comment: str = Form(""),
    student_id: Optional[int] = Form(None),
    current_user=Depends(get_current_user)
):
    await require_school_feature(
        current_user,
        "homeworks_enabled",
        detail="Домашние задания отключены в настройках школы",
    )
    """Ученик или родитель загружает работу (фото или файл)."""
    role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
    current_user_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    effective_student_id = current_user_id
    if role == "student":
        effective_student_id = current_user_id
    elif role == "parent":
        if student_id is None:
            raise HTTPException(status_code=400, detail="Укажите student_id")
        conn = await get_db_connection()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT 1 FROM parent_students WHERE parent_id = %s AND student_id = %s",
                    (current_user_id, student_id),
                )
                if not await cursor.fetchone():
                    raise HTTPException(status_code=403, detail="Это не ваш ребёнок")
        finally:
            conn.close()
        effective_student_id = student_id
    else:
        raise HTTPException(status_code=403, detail="Только для учеников и родителей")
    original_filename, ext = validate_upload_metadata(
        file,
        allowed_exts=_ALLOWED_HW_SUBMISSION_EXTS,
    )
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT h.id, h.due_date, h.submission_deadline_at, c.school_id
                FROM homeworks h
                JOIN classes c ON c.id = h.class_id
                WHERE h.id = %s
                """,
                (homework_id,),
            )
            deadline_row = await cursor.fetchone()
            if not deadline_row:
                raise HTTPException(status_code=404, detail="ДЗ не найдено")
            submission_deadline = _resolve_homework_submission_deadline(deadline_row)
            if submission_deadline and datetime.now() > submission_deadline:
                raise HTTPException(status_code=400, detail="Срок сдачи уже истёк")
            await cursor.execute(
                """SELECT h.id FROM homeworks h
                   JOIN class_students cs ON h.class_id = cs.class_id AND cs.student_id = %s
                   WHERE h.id = %s""",
                (effective_student_id, homework_id)
            )
            if not await cursor.fetchone():
                raise HTTPException(status_code=404, detail="ДЗ не найдено или вы не в этом классе")
        fname = f"{homework_id}_{effective_student_id}_{uuid.uuid4().hex[:12]}.{ext}"
        dir_path = os.path.join(settings.HOMEWORK_SUBMISSIONS_DIR, str(homework_id))
        os.makedirs(dir_path, exist_ok=True)
        file_path = os.path.join(dir_path, fname)
        written = await save_upload_limited(
            file,
            file_path,
            max_bytes=MAX_DOCUMENT_BYTES,
        )
        rel_path = f"{homework_id}/{fname}"
        try:
            async with conn.cursor() as cursor:
                await change_school_storage(
                    cursor,
                    school_id=deadline_row["school_id"],
                    delta_bytes=written,
                )
                await cursor.execute(
                    """INSERT INTO homework_submissions (homework_id, student_id, file_path, file_name, student_comment)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (
                        homework_id,
                        effective_student_id,
                        rel_path,
                        safe_original_filename(original_filename, fname),
                        (student_comment or "")[:2000],
                    )
                )
                sid = cursor.lastrowid
                await conn.commit()
        except Exception:
            if os.path.isfile(file_path):
                os.remove(file_path)
            await conn.rollback()
            raise
        return {
            "status": "ok",
            "id": sid,
            "file_name": file.filename,
            "student_comment": (student_comment or "")[:2000],
        }
    finally:
        conn.close()


@router.put("/homeworks/{homework_id}/done-mark")
async def mark_homework_done_without_file(
    homework_id: int,
    payload: HomeworkDoneMarkPayload,
    current_user=Depends(get_current_user),
):
    await require_school_feature(
        current_user,
        "homeworks_enabled",
        detail="Домашние задания отключены в настройках школы",
    )
    """Отметить ДЗ как сделанное без прикрепления файла."""
    role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
    current_user_id = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    effective_student_id = current_user_id

    if role == "student":
        effective_student_id = current_user_id
    elif role == "parent":
        if payload.student_id is None:
            raise HTTPException(status_code=400, detail="Укажите student_id")
        conn = await get_db_connection()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT 1 FROM parent_students WHERE parent_id = %s AND student_id = %s",
                    (current_user_id, payload.student_id),
                )
                if not await cursor.fetchone():
                    raise HTTPException(status_code=403, detail="Это не ваш ребёнок")
        finally:
            conn.close()
        effective_student_id = payload.student_id
    else:
        raise HTTPException(status_code=403, detail="Только для учеников и родителей")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """SELECT h.id, h.need_submission, h.due_date, h.submission_deadline_at
                   FROM homeworks h
                   JOIN class_students cs ON h.class_id = cs.class_id AND cs.student_id = %s
                   WHERE h.id = %s""",
                (effective_student_id, homework_id),
            )
            hw = await cursor.fetchone()
            if not hw:
                raise HTTPException(status_code=404, detail="ДЗ не найдено")
            if bool(hw.get("need_submission")):
                raise HTTPException(status_code=400, detail="Для этого ДЗ нужен файл")
            submission_deadline = _resolve_homework_submission_deadline(hw)
            if submission_deadline and datetime.now() > submission_deadline:
                raise HTTPException(status_code=400, detail="Срок сдачи уже истёк")

            if payload.marked_done:
                await cursor.execute(
                    """INSERT INTO homework_done_marks (homework_id, student_id)
                       VALUES (%s, %s)
                       ON DUPLICATE KEY UPDATE marked_at = CURRENT_TIMESTAMP""",
                    (homework_id, effective_student_id),
                )
            else:
                await cursor.execute(
                    "DELETE FROM homework_done_marks WHERE homework_id = %s AND student_id = %s",
                    (homework_id, effective_student_id),
                )
            await conn.commit()
        return {"status": "ok", "marked_done": bool(payload.marked_done)}
    finally:
        conn.close()


@router.get("/homeworks/{homework_id}/submissions")
async def list_homework_submissions(
    homework_id: int,
    student_id: Optional[int] = Query(None, description="ID ученика для родителя"),
    current_user=Depends(get_current_user)
):
    await require_school_feature(
        current_user,
        "homeworks_enabled",
        detail="Домашние задания отключены в настройках школы",
    )
    """Список сданных работ по ДЗ. Учитель — все, ученик/родитель — свои."""
    uid = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            if role == "student":
                await cursor.execute(
                    """SELECT hs.id, hs.student_id, hs.file_path, hs.file_name, hs.created_at,
                              hs.grade, hs.is_reviewed, hs.review_status, hs.comment, hs.student_comment,
                              TRIM(BOTH '"' FROM p.full_name) AS student_name
                       FROM homework_submissions hs
                       JOIN profiles p ON p.user_id = hs.student_id
                       JOIN homeworks h ON h.id = hs.homework_id
                       JOIN class_students cs ON h.class_id = cs.class_id AND cs.student_id = %s
                       WHERE hs.homework_id = %s AND hs.student_id = %s
                       ORDER BY hs.created_at DESC""",
                    (uid, homework_id, uid)
                )
            elif role == "parent":
                if student_id is None:
                    raise HTTPException(status_code=400, detail="Укажите student_id")
                await cursor.execute(
                    "SELECT 1 FROM parent_students WHERE parent_id = %s AND student_id = %s",
                    (uid, student_id)
                )
                if not await cursor.fetchone():
                    raise HTTPException(status_code=403, detail="Это не ваш ребёнок")
                await cursor.execute(
                    """SELECT hs.id, hs.student_id, hs.file_path, hs.file_name, hs.created_at,
                              hs.grade, hs.is_reviewed, hs.review_status, hs.comment, hs.student_comment,
                              TRIM(BOTH '"' FROM p.full_name) AS student_name
                       FROM homework_submissions hs
                       JOIN profiles p ON p.user_id = hs.student_id
                       JOIN homeworks h ON h.id = hs.homework_id
                       JOIN class_students cs ON h.class_id = cs.class_id AND cs.student_id = %s
                       WHERE hs.homework_id = %s AND hs.student_id = %s
                       ORDER BY hs.created_at DESC""",
                    (student_id, homework_id, student_id)
                )
            else:
                await cursor.execute(
                    """
                    SELECT h.teacher_id, c.school_id
                    FROM homeworks h
                    JOIN classes c ON c.id = h.class_id
                    WHERE h.id = %s
                    """,
                    (homework_id,)
                )
                homework_row = await cursor.fetchone()
                if not homework_row:
                    raise HTTPException(status_code=404, detail="ДЗ не найдено")
                await _ensure_staff_can_access_homework_row(homework_row, current_user)
                await cursor.execute(
                    """SELECT hs.id, hs.student_id, hs.file_path, hs.file_name, hs.created_at,
                              hs.grade, hs.is_reviewed, hs.review_status, hs.comment, hs.student_comment,
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
                "review_status": _resolve_homework_review_status(
                    r.get("review_status"),
                    r.get("is_reviewed"),
                ),
                "comment": r.get("comment") or "",
                "student_comment": r.get("student_comment") or "",
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
    await require_school_feature(
        current_user,
        "homeworks_enabled",
        detail="Домашние задания отключены в настройках школы",
    )
    """Скачать файл сдачи."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """SELECT hs.file_path, hs.file_name, hs.student_id, h.teacher_id, c.school_id
                   FROM homework_submissions hs
                   JOIN homeworks h ON h.id = hs.homework_id
                   JOIN classes c ON c.id = h.class_id
                   WHERE hs.id = %s""",
                (submission_id,)
            )
            row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Файл не найден")
        uid = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
        role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
        if role in ("admin", "owner", "superadmin", "root", "teacher"):
            await _ensure_staff_can_access_homework_row(row, current_user)
        elif role == "student" and row["student_id"] == uid:
            pass
        elif role == "parent":
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT 1 FROM parent_students WHERE parent_id = %s AND student_id = %s",
                    (uid, row["student_id"])
                )
                if not await cursor.fetchone():
                    raise HTTPException(status_code=403, detail="Нет доступа")
        else:
            raise HTTPException(status_code=403, detail="Нет доступа")
        full_path = safe_storage_path(settings.HOMEWORK_SUBMISSIONS_DIR, row["file_path"])
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
    await require_school_feature(
        current_user,
        "homeworks_enabled",
        detail="Домашние задания отключены в настройках школы",
    )
    """Учитель: оценка, «Проверено», комментарий к сдаче."""
    uid = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("teacher", "admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Только для учителей")
    conn = await get_db_connection()
    try:
        notification_payload = None
        async with conn.cursor() as cursor:
            await cursor.execute(
                """SELECT h.teacher_id, hs.student_id, hs.homework_id, h.subject_id, c.school_id
                   FROM homework_submissions hs
                   JOIN homeworks h ON h.id = hs.homework_id
                   JOIN classes c ON c.id = h.class_id
                   WHERE hs.id = %s""",
                (submission_id,)
            )
            row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Не найдено")
        await _ensure_staff_can_access_homework_row(row, current_user)
        updates, params = [], []
        if body.grade is not None:
            if body.grade < 2 or body.grade > 5:
                raise HTTPException(status_code=400, detail="Оценка от 2 до 5")
            updates.append("grade = %s")
            params.append(body.grade)
        resolved_review_status = None
        if body.review_status is not None:
            workflow = None
            async with conn.cursor() as cursor:
                workflow = await _get_homework_review_workflow(
                    cursor,
                    row.get("school_id"),
                )
            normalized = body.review_status.strip().lower()
            if normalized not in _ALLOWED_HW_REVIEW_STATUSES:
                raise HTTPException(
                    status_code=400,
                    detail="Статус проверки должен быть pending, reviewed или rework",
                )
            if normalized == "rework" and not workflow.get("rework_enabled", True):
                raise HTTPException(
                    status_code=400,
                    detail="Статус 'На доработку' отключен в настройках школы",
                )
            resolved_review_status = normalized
        elif body.is_reviewed is not None:
            resolved_review_status = "reviewed" if bool(body.is_reviewed) else "pending"
        if resolved_review_status is not None:
            updates.append("review_status = %s")
            params.append(resolved_review_status)
            updates.append("is_reviewed = %s")
            params.append(resolved_review_status == "reviewed")
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
            if resolved_review_status is not None or body.comment is not None or body.grade is not None:
                await cursor.execute("SELECT name FROM subjects WHERE id = %s", (row["subject_id"],))
                subject_row = await cursor.fetchone()
                parent_ids = await _get_parent_ids_for_students(cursor, [row["student_id"]])
                notification_payload = {
                    "student_id": int(row["student_id"]),
                    "homework_id": int(row["homework_id"]),
                    "subject_name": subject_row["name"] if subject_row else "предмету",
                    "parent_ids": parent_ids,
                    "review_status": resolved_review_status,
                    "grade": body.grade,
                }
        if notification_payload:
            async with conn.cursor() as cursor:
                workflow = await _get_homework_review_workflow(
                    cursor,
                    row.get("school_id"),
                )
            labels = workflow.get("labels", {})
            status_label = labels.get("reviewed", "Проверено")
            if notification_payload["review_status"] == "rework":
                status_label = labels.get("rework", "На доработку")
            elif notification_payload["review_status"] == "pending":
                status_label = labels.get("pending", "Ждёт проверки")
            grade_suffix = (
                f" Оценка: {notification_payload['grade']}."
                if notification_payload["grade"] is not None
                else ""
            )
            body_text = (
                f"Статус вашей работы по предмету «{notification_payload['subject_name']}»: "
                f"{status_label}.{grade_suffix}"
            )
            await notify_many(
                [notification_payload["student_id"]],
                "Обновление по домашней работе",
                body_text,
                notification_type="homework_review",
                sender_id=uid,
                entity_type="homework",
                entity_id=notification_payload["homework_id"],
                route_name="homework",
                route_params={"homework_id": notification_payload["homework_id"]},
            )
            if notification_payload["parent_ids"]:
                await notify_many(
                    notification_payload["parent_ids"],
                    "Обновление по ДЗ у ребенка",
                    body_text,
                    notification_type="homework_review",
                    sender_id=uid,
                    entity_type="homework",
                    entity_id=notification_payload["homework_id"],
                    route_name="homework",
                    route_params={"homework_id": notification_payload["homework_id"]},
                )
        return {"status": "ok"}
    finally:
        conn.close()


@router.delete("/homework-submissions/{submission_id}")
async def delete_homework_submission(
    submission_id: int,
    current_user=Depends(get_current_user)
):
    await require_school_feature(
        current_user,
        "homeworks_enabled",
        detail="Домашние задания отключены в настройках школы",
    )
    """Удалить сдачу. Учитель может удалить любую свою, ученик — свою собственную."""
    uid = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """SELECT h.teacher_id, hs.student_id, hs.file_path, c.school_id
                   FROM homework_submissions hs
                   JOIN homeworks h ON h.id = hs.homework_id
                   JOIN classes c ON c.id = h.class_id
                   WHERE hs.id = %s""",
                (submission_id,)
            )
            row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Не найдено")
        allowed = False
        if role in ("admin", "owner", "superadmin", "root", "teacher"):
            await _ensure_staff_can_access_homework_row(row, current_user)
            allowed = True
        elif role == "student" and row["student_id"] == uid:
            allowed = True
        if not allowed:
            raise HTTPException(status_code=403, detail="Нет доступа")
        full_path = (
            safe_storage_path(settings.HOMEWORK_SUBMISSIONS_DIR, row["file_path"])
            if row.get("file_path")
            else None
        )
        file_size = (
            os.path.getsize(full_path)
            if full_path and os.path.isfile(full_path)
            else 0
        )
        async with conn.cursor() as cursor:
            if file_size:
                await change_school_storage(
                    cursor,
                    school_id=row["school_id"],
                    delta_bytes=-file_size,
                )
            await cursor.execute("DELETE FROM homework_submissions WHERE id = %s", (submission_id,))
            await conn.commit()
        if full_path and os.path.isfile(full_path):
            try:
                os.remove(full_path)
            except OSError:
                pass
        return {"status": "ok"}
    finally:
        conn.close()


@router.get("/homeworks/{homework_id}/submission-status")
async def get_homework_submission_status(
    homework_id: int,
    current_user=Depends(get_current_user)
):
    await require_school_feature(
        current_user,
        "homeworks_enabled",
        detail="Домашние задания отключены в настройках школы",
    )
    """Учитель: кто сдал, кто не сдал (для сводки «Сдано: N из M»)."""
    uid = current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None)
    role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("teacher", "admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Только для учителей")
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT h.class_id, h.teacher_id, h.need_submission, c.school_id
                FROM homeworks h
                JOIN classes c ON c.id = h.class_id
                WHERE h.id = %s
                """,
                (homework_id,)
            )
            hw = await cursor.fetchone()
        if not hw:
            raise HTTPException(status_code=404, detail="ДЗ не найдено")
        await _ensure_staff_can_access_homework_row(hw, current_user)
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
            done_without_file_ids = set()
            await cursor.execute(
                "SELECT student_id FROM homework_submissions WHERE homework_id = %s",
                (homework_id,)
            )
            for r in await cursor.fetchall():
                submitted_ids.add(r["student_id"])
            await cursor.execute(
                """SELECT hs.id, hs.student_id, hs.review_status, hs.is_reviewed
                   FROM homework_submissions hs
                   WHERE hs.homework_id = %s
                   ORDER BY hs.student_id, hs.created_at DESC, hs.id DESC""",
                (homework_id,),
            )
            latest_submissions = {}
            for r in await cursor.fetchall():
                student_id = r["student_id"]
                if student_id not in latest_submissions:
                    latest_submissions[student_id] = {
                        "submission_id": r["id"],
                        "review_status": _resolve_homework_review_status(
                            r.get("review_status"),
                            r.get("is_reviewed"),
                        ),
                    }
            if not bool(hw.get("need_submission")):
                await cursor.execute(
                    "SELECT student_id FROM homework_done_marks WHERE homework_id = %s",
                    (homework_id,),
                )
                for r in await cursor.fetchall():
                    done_without_file_ids.add(r["student_id"])
        not_submitted = [
            {"student_id": s["student_id"], "full_name": s.get("full_name") or ""}
            for s in (all_students or [])
            if s["student_id"] not in submitted_ids and s["student_id"] not in done_without_file_ids
        ]
        done_without_file = [
            {"student_id": s["student_id"], "full_name": s.get("full_name") or ""}
            for s in (all_students or [])
            if s["student_id"] in done_without_file_ids
        ]
        needs_review = []
        needs_rework = []
        reviewed_count = 0
        for s in (all_students or []):
            latest_submission = latest_submissions.get(s["student_id"])
            if latest_submission is None:
                continue
            review_item = {
                "student_id": s["student_id"],
                "full_name": s.get("full_name") or "",
                "submission_id": latest_submission["submission_id"],
            }
            if latest_submission["review_status"] == "rework":
                needs_rework.append(review_item)
            elif latest_submission["review_status"] == "reviewed":
                reviewed_count += 1
            else:
                needs_review.append(review_item)
        return {
            "total": len(all_students) if all_students else 0,
            "submitted_count": len(submitted_ids),
            "done_without_file_count": len(done_without_file_ids),
            "completed_count": len(submitted_ids) + len(done_without_file_ids),
            "needs_review_count": len(needs_review),
            "rework_count": len(needs_rework),
            "reviewed_count": reviewed_count,
            "not_submitted": not_submitted,
            "done_without_file": done_without_file,
            "needs_review": needs_review,
            "needs_rework": needs_rework,
        }
    finally:
        conn.close()


@router.get("/subject-scores/{subject_name}", response_model=List[dict])
async def get_student_scores_by_subject(subject_name: str, current_user=Depends(get_current_user)):
    """Get student scores for specific subject"""
    await require_school_feature(
        current_user,
        "analytics_enabled",
        detail="Аналитика отключена в настройках школы",
    )
    resolved_school_id = await resolve_school_id_for_user(current_user)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT s.id
                FROM school_subjects ss
                JOIN subjects s ON s.id = ss.subject_id
                WHERE ss.school_id = %s AND s.name = %s
                """,
                (resolved_school_id, subject_name),
            )
            subject = await cursor.fetchone()
            if not subject:
                raise HTTPException(status_code=404, detail="Subject not found")

            role = _current_user_role(current_user)
            school_filter = """
              AND EXISTS (
                SELECT 1
                FROM school_users su
                WHERE su.user_id = u.id AND su.school_id = %s
              )
            """
            params: list = [subject["id"]]
            params.append(resolved_school_id)

            await cursor.execute(f"""
                SELECT 
                    u.id AS student_id,
                    TRIM(BOTH '"' FROM p.full_name) AS student_name,
                    GROUP_CONCAT(g.value ORDER BY g.date) AS scores
                FROM users u
                JOIN profiles p ON u.id = p.user_id
                LEFT JOIN grades g ON u.id = g.student_id AND g.subject_id = %s
                WHERE u.role = 'student'
                  {school_filter}
                GROUP BY u.id, p.full_name
            """, tuple(params))
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
    await require_school_feature(
        current_user,
        "analytics_enabled",
        detail="Аналитика отключена в настройках школы",
    )
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            role = _current_user_role(current_user)
            school_id = await get_school_id_for_user(current_user)
            school_filter = ""
            params: tuple = ()
            if role not in ("owner", "superadmin", "root"):
                if school_id is None:
                    raise HTTPException(status_code=403, detail="No school access")
                school_filter = """
                  AND EXISTS (
                    SELECT 1
                    FROM class_students cs
                    JOIN classes c ON c.id = cs.class_id
                    WHERE cs.student_id = u.id AND c.school_id = %s
                  )
                """
                params = (school_id,)

            await cursor.execute(f"""
                SELECT 
                    u.id AS student_id,
                    TRIM(BOTH '"' FROM p.full_name) AS student_name,
                    g.id AS grade_id,
                    s.name AS subject,
                    g.value,
                    g.date,
                    g.lesson_topic,
                    g.grade_reason,
                    g.comment
                FROM users u
                JOIN profiles p ON u.id = p.user_id
                JOIN grades g ON u.id = g.student_id
                JOIN subjects s ON s.id = g.subject_id
                WHERE u.role = 'student'
                  {school_filter}
                ORDER BY u.id, g.date
            """, params)
            rows = await cursor.fetchall()

            result, current, last_id = [], None, None
            for row in rows:
                if row['student_id'] != last_id:
                    if current:
                        result.append(current)
                    current = {"student_id": row['student_id'], "student_name": row['student_name'], "grades": []}
                    last_id = row['student_id']
                current["grades"].append({
                    "id": row["grade_id"],
                    "subject": row["subject"],
                    "value": row["value"],
                    "date": row["date"].isoformat() if hasattr(row["date"], "isoformat") else str(row["date"]),
                    "lesson_topic": row.get("lesson_topic") or "",
                    "grade_reason": row.get("grade_reason") or "",
                    "comment": row.get("comment") or "",
                })
            if current:
                result.append(current)
            return result
    finally:
        conn.close()


@router.get("/attendance-stats/{student_id}", response_model=AttendanceStats)
async def get_attendance_stats(student_id: int, current_user: User = Depends(get_current_user)):
    """Get attendance statistics for a student"""
    await require_school_feature(current_user, "attendance_enabled", detail="Посещаемость отключена в настройках школы")
    await check_analytics_access(current_user, student_id=student_id)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Статистика за учебный год: с 1 сентября текущего учебного цикла.
            await cursor.execute("""
                SELECT 
                    COUNT(CASE WHEN status = 'present' THEN 1 END) as present,
                    COUNT(CASE WHEN status = 'late' THEN 1 END) as late,
                    COUNT(CASE WHEN status = 'absent' THEN 1 END) as absent,
                    COUNT(*) as total_lessons
                FROM attendance 
                WHERE student_id = %s 
                AND date >= STR_TO_DATE(
                    CONCAT(
                        IF(MONTH(CURDATE()) >= 9, YEAR(CURDATE()), YEAR(CURDATE()) - 1),
                        '-09-01'
                    ),
                    '%%Y-%%m-%%d'
                )
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
    await require_school_feature(current_user, "attendance_enabled", detail="Посещаемость отключена в настройках школы")
    await check_analytics_access(current_user, student_id=student_id)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Получаем данные за последние 5 недель относительно последней записи.
            # Так экран не становится пустым во время каникул или при демо-данных.
            # Группируем по дате и выбираем самый "плохой" статус (absent > late > present)
            await cursor.execute("""
                SELECT 
                    a.date,
                    FLOOR(DATEDIFF(anchor.max_date, a.date) / 7) + 1 as week,
                    WEEKDAY(a.date) + 1 as day,
                    MAX(CASE 
                        WHEN a.status = 'present' THEN 1
                        WHEN a.status = 'late' THEN 2
                        WHEN a.status = 'absent' THEN 3
                        ELSE 0
                    END) as status
                FROM attendance a
                JOIN (
                    SELECT MAX(date) AS max_date
                    FROM attendance
                    WHERE student_id = %s
                ) anchor ON anchor.max_date IS NOT NULL
                WHERE a.student_id = %s
                AND a.date >= DATE_SUB(anchor.max_date, INTERVAL 35 DAY)
                AND a.date <= anchor.max_date
                GROUP BY a.date, anchor.max_date
                ORDER BY a.date
            """, (student_id, student_id))
            
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
    await require_school_feature(current_user, "attendance_enabled", detail="Посещаемость отключена в настройках школы")
    await check_analytics_access(current_user, student_id=student_id)
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
            
            result = []
            for row in rows:
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
    await require_school_feature(current_user, "analytics_enabled", detail="Аналитика отключена в настройках школы")
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
    await require_school_feature(current_user, "analytics_enabled", detail="Аналитика отключена в настройках школы")
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
    await require_school_feature(current_user, "analytics_enabled", detail="Аналитика отключена в настройках школы")
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
    await require_school_feature(current_user, "analytics_enabled", detail="Аналитика отключена в настройках школы")
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
    await require_school_feature(current_user, "analytics_enabled", detail="Аналитика отключена в настройках школы")
    await check_analytics_access(current_user, student_id=student_id)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Получаем название предмета
            await cursor.execute(
                """
                SELECT s.name
                FROM school_users su
                JOIN school_subjects ss ON ss.school_id = su.school_id
                JOIN subjects s ON s.id = ss.subject_id
                WHERE su.user_id = %s AND s.id = %s
                LIMIT 1
                """,
                (student_id, subject_id),
            )
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
    school_id: Optional[int] = None


@router.post("/classes", status_code=201)
async def create_class(payload: ClassCreate, current_user=Depends(get_current_user)):
    """Create a new class. Class name must be unique within each school."""
    # Проверяем права доступа - только админы и владельцы могут создавать классы
    role = current_user.get("role", "") if isinstance(current_user, dict) else getattr(current_user, "role", "")
    if role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")
    
    if not payload.parallel.strip():
        raise HTTPException(status_code=400, detail="Parallel is required")
    
    if not payload.class_name.strip():
        raise HTTPException(status_code=400, detail="Class name is required")
    
    if not payload.academic_year.strip():
        raise HTTPException(status_code=400, detail="Academic year is required")
    
    school_id = await get_school_id_for_user(current_user) or payload.school_id
    if school_id is None:
        raise HTTPException(status_code=400, detail="Укажите школу для класса")
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await ensure_school_capacity(
                cur,
                school_id=school_id,
                resource="classes",
            )
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
