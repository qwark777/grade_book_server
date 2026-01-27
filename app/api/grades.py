from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.security import get_current_user
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



@router.get("/classes", response_model=List[ClassItem])
async def get_classes(current_user=Depends(get_current_user)):
    """Get all classes with student count"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT c.id, c.name,
                       (SELECT COUNT(*) FROM class_students cs WHERE cs.class_id = c.id) AS student_count
                FROM classes c
                ORDER BY c.name
            """)
            return await cursor.fetchall()
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
            await cursor.execute("SELECT name, coefficient FROM subjects ORDER BY name")
            rows = await cursor.fetchall()
            return [
                Subject(
                    name=r["name"] if isinstance(r, dict) else r[0],
                    coefficient=float(r["coefficient"] if isinstance(r, dict) else r[1])
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
    """Classes assigned to current teacher"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT c.id, c.name,
                       (SELECT COUNT(*) FROM class_students cs WHERE cs.class_id = c.id) AS student_count
                FROM class_teachers ct
                JOIN classes c ON c.id = ct.class_id
                WHERE ct.teacher_id = %s
                ORDER BY c.name
                """,
                (current_user["id"] if isinstance(current_user, dict) else getattr(current_user, "id", None),)
            )
            rows = await cursor.fetchall()
            return rows
    finally:
        conn.close()


@router.get("/classes/{class_id}/students-performance")
async def get_class_students_performance(class_id: int, current_user=Depends(get_current_user)):
    """Get performance data (current and previous average) for all students in a class"""
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
                
                # Get current period average (last 30 days) - use same logic as admin_analytics
                # Using JOIN to match exactly the admin_analytics query logic
                await cursor.execute("""
                    SELECT AVG(g.value) as avg_value, COUNT(*) as count
                    FROM grades g
                    JOIN class_students cs ON g.student_id = cs.student_id
                    WHERE cs.class_id = %s
                    AND g.student_id = %s
                    AND g.date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                """, (class_id, student_id))
                current_row = await cursor.fetchone()
                current_avg_val = current_row.get("avg_value") if current_row else None
                current_avg = float(current_avg_val) if current_avg_val is not None else None
                current_count = current_row.get("count", 0) if current_row else 0
                
                # Get previous period average (30-60 days ago) - use same logic as admin_analytics
                await cursor.execute("""
                    SELECT AVG(g.value) as avg_value, COUNT(*) as count
                    FROM grades g
                    JOIN class_students cs ON g.student_id = cs.student_id
                    WHERE cs.class_id = %s
                    AND g.student_id = %s
                    AND g.date >= DATE_SUB(CURDATE(), INTERVAL 60 DAY)
                    AND g.date < DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                """, (class_id, student_id))
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

            # permission check if we know class_id
            if class_id is not None:
                await cursor.execute(
                    "SELECT 1 FROM class_subject_teachers WHERE class_id=%s AND subject_id=%s AND teacher_id=%s",
                    (class_id, subject_id, teacher_id)
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
    """Create a new class"""
    # Проверяем права доступа - только админы и владельцы могут создавать классы
    if current_user["role"] not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Forbidden")
    
    if not payload.parallel.strip():
        raise HTTPException(status_code=400, detail="Parallel is required")
    
    if not payload.class_name.strip():
        raise HTTPException(status_code=400, detail="Class name is required")
    
    if not payload.academic_year.strip():
        raise HTTPException(status_code=400, detail="Academic year is required")
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            # Проверяем, не существует ли уже класс с такими параметрами
            await cur.execute(
                "SELECT id FROM classes WHERE `parallel`=%s AND class_name=%s AND academic_year=%s", 
                (payload.parallel.strip(), payload.class_name.strip(), payload.academic_year.strip())
            )
            existing = await cur.fetchone()
            if existing:
                raise HTTPException(status_code=400, detail="Class with this parallel, name and academic year already exists")
            
            # Создаем новый класс
            await cur.execute(
                "INSERT INTO classes (`parallel`, class_name, academic_year) VALUES (%s, %s, %s)",
                (payload.parallel.strip(), payload.class_name.strip(), payload.academic_year.strip())
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
