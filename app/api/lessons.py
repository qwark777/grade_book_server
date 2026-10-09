from datetime import datetime, time, timedelta
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.entitlements import get_school_id_for_user
from app.core.security import get_current_user
from app.db.connection import get_db_connection
from app.models.lesson import (
    Lesson,
    LessonBalanceIssueRequest,
    LessonReview,
    LessonReviewCreate,
    LessonBalanceWithDetails,
    LessonCreate,
    LessonEnrollment,
    LessonSessionCreate,
    LessonSessionUpdate,
    LessonSessionWithDetails,
    LessonUpdate,
    LessonWithDetails,
)
from app.models.user import UserInDB

router = APIRouter()


def _user_id(user: UserInDB) -> int:
    return user.id if hasattr(user, "id") else user["id"]


def _user_role(user: UserInDB) -> str:
    return user.role if hasattr(user, "role") else user["role"]


def _is_admin_like(role: str) -> bool:
    return role in {"admin", "owner", "superadmin", "root"}


def _mysql_time(value):
    if isinstance(value, timedelta):
        total_seconds = int(value.total_seconds())
        hours = (total_seconds // 3600) % 24
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        return time(hour=hours, minute=minutes, second=seconds)
    return value


async def _resolve_lesson_school_id(current_user: UserInDB, requested_school_id: Optional[int]) -> Optional[int]:
    role = _user_role(current_user)
    if role in {"owner", "superadmin", "root"}:
        return requested_school_id

    user_school_id = await get_school_id_for_user(current_user)
    if requested_school_id is not None:
        if user_school_id is None or int(requested_school_id) != int(user_school_id):
            raise HTTPException(status_code=403, detail="Access denied")
        return requested_school_id
    return user_school_id


async def _get_lesson_row(cursor, lesson_id: int):
    await cursor.execute(
        """
        SELECT id, title, subject, tutor_id, school_id, price, max_students,
               duration_minutes, is_online, location, online_link, created_at, updated_at
        FROM additional_lessons
        WHERE id = %s
        """,
        (lesson_id,),
    )
    return await cursor.fetchone()


async def _assert_can_manage_lesson(lesson_row, current_user: UserInDB):
    role = _user_role(current_user)
    current_id = _user_id(current_user)
    if not lesson_row:
        raise HTTPException(status_code=404, detail="Lesson not found")
    if lesson_row["tutor_id"] == current_id:
        return
    if role in {"owner", "superadmin", "root"}:
        return
    if role == "admin":
        admin_school_id = await get_school_id_for_user(current_user)
        lesson_school_id = lesson_row.get("school_id") if isinstance(lesson_row, dict) else None
        if admin_school_id is not None and lesson_school_id is not None and int(admin_school_id) == int(lesson_school_id):
            return
    raise HTTPException(status_code=403, detail="Access denied")


async def _assert_student_in_lesson_school(cursor, student_id: int, lesson_school_id: Optional[int]):
    if lesson_school_id is None:
        return
    await cursor.execute(
        """
        SELECT 1
        FROM class_students cs
        JOIN classes c ON c.id = cs.class_id
        WHERE cs.student_id = %s AND c.school_id = %s
        LIMIT 1
        """,
        (student_id, lesson_school_id),
    )
    if not await cursor.fetchone():
        raise HTTPException(status_code=403, detail="Student does not belong to lesson school")


async def _assert_student_can_review_lesson(cursor, lesson_id: int, student_id: int):
    await cursor.execute(
        """
        SELECT e.id
        FROM lesson_enrollments e
        WHERE e.lesson_id = %s
          AND e.student_id = %s
          AND e.status IN ('enrolled', 'completed')
        LIMIT 1
        """,
        (lesson_id, student_id),
    )
    enrollment = await cursor.fetchone()
    if not enrollment:
        raise HTTPException(status_code=403, detail="You are not enrolled in this lesson")

    await cursor.execute(
        """
        SELECT id
        FROM lesson_student_sessions
        WHERE lesson_id = %s
          AND student_id = %s
          AND status IN ('scheduled', 'completed')
          AND session_date <= CURDATE()
        LIMIT 1
        """,
        (lesson_id, student_id),
    )
    session = await cursor.fetchone()
    if not session:
        raise HTTPException(status_code=400, detail="You can rate only started lessons")


async def _fetch_lesson_balances(cursor, lesson_id: int) -> List[LessonBalanceWithDetails]:
    await cursor.execute(
        """
        SELECT
            e.id AS enrollment_id,
            e.lesson_id,
            l.title AS lesson_title,
            l.subject AS lesson_subject,
            e.student_id,
            TRIM(BOTH '"' FROM sp.full_name) AS student_name,
            l.tutor_id,
            TRIM(BOTH '"' FROM tp.full_name) AS tutor_name,
            COALESCE(e.total_lessons, 1) AS total_lessons,
            COALESCE(e.remaining_lessons, COALESCE(e.total_lessons, 1), 1) AS remaining_lessons,
            e.purchase_price,
            e.purchase_date,
            e.valid_until,
            e.status,
            e.payment_status,
            e.payment_method,
            e.notes,
            MIN(
                CASE
                    WHEN s.status = 'scheduled' AND s.session_date >= CURDATE()
                    THEN s.session_date
                    ELSE NULL
                END
            ) AS next_session_date,
            MIN(
                CASE
                    WHEN s.status = 'scheduled' AND s.session_date >= CURDATE()
                    THEN s.start_time
                    ELSE NULL
                END
            ) AS next_session_start_time
        FROM lesson_enrollments e
        JOIN additional_lessons l ON l.id = e.lesson_id
        LEFT JOIN profiles sp ON sp.user_id = e.student_id
        LEFT JOIN profiles tp ON tp.user_id = l.tutor_id
        LEFT JOIN lesson_student_sessions s ON s.enrollment_id = e.id
        WHERE e.lesson_id = %s
        GROUP BY
            e.id, e.lesson_id, l.title, l.subject, e.student_id, sp.full_name,
            l.tutor_id, tp.full_name, e.total_lessons, e.remaining_lessons,
            e.purchase_price, e.purchase_date, e.valid_until, e.status,
            e.payment_status, e.payment_method, e.notes
        ORDER BY sp.full_name
        """,
        (lesson_id,),
    )
    rows = await cursor.fetchall()
    for row in rows:
        if isinstance(row, dict):
            row["next_session_start_time"] = _mysql_time(row.get("next_session_start_time"))
    return [LessonBalanceWithDetails(**row) for row in rows]


async def _fetch_lesson_sessions(
    cursor,
    lesson_id: int,
) -> List[LessonSessionWithDetails]:
    await cursor.execute(
        """
        SELECT
            s.id,
            s.lesson_id,
            s.enrollment_id,
            s.student_id,
            TRIM(BOTH '"' FROM sp.full_name) AS student_name,
            s.tutor_id,
            TRIM(BOTH '"' FROM tp.full_name) AS tutor_name,
            l.title AS lesson_title,
            l.subject AS lesson_subject,
            s.session_date,
            s.start_time,
            s.end_time,
            s.is_online,
            s.location,
            s.online_link,
            s.status,
            s.lessons_reserved,
            s.notes,
            COALESCE(e.remaining_lessons, 0) AS remaining_lessons_after,
            s.created_at
        FROM lesson_student_sessions s
        JOIN additional_lessons l ON l.id = s.lesson_id
        LEFT JOIN lesson_enrollments e ON e.id = s.enrollment_id
        LEFT JOIN profiles sp ON sp.user_id = s.student_id
        LEFT JOIN profiles tp ON tp.user_id = s.tutor_id
        WHERE s.lesson_id = %s
        ORDER BY s.session_date DESC, s.start_time DESC
        """,
        (lesson_id,),
    )
    rows = await cursor.fetchall()
    for row in rows:
        if isinstance(row, dict):
            row["start_time"] = _mysql_time(row.get("start_time"))
            row["end_time"] = _mysql_time(row.get("end_time"))
    return [LessonSessionWithDetails(**row) for row in rows]


async def _assert_lesson_capacity_available(cursor, lesson_id: int, student_id: int):
    await cursor.execute(
        """
        SELECT
            l.max_students,
            COUNT(e.id) AS active_students
        FROM additional_lessons l
        LEFT JOIN lesson_enrollments e
            ON e.lesson_id = l.id
           AND e.status = 'enrolled'
        WHERE l.id = %s
          AND (
            e.student_id IS NULL
            OR e.student_id <> %s
          )
        GROUP BY l.id, l.max_students
        """,
        (lesson_id, student_id),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Lesson not found")
    if int(row["active_students"] or 0) >= int(row["max_students"] or 0):
        raise HTTPException(status_code=400, detail="Lesson is full")


async def _assert_no_session_overlap(
    cursor,
    *,
    student_id: int,
    tutor_id: int,
    session_date,
    start_time,
    end_time,
    exclude_session_id: Optional[int] = None,
):
    exclusion_sql = ""
    params: List = [student_id, tutor_id, session_date, end_time, start_time]
    if exclude_session_id is not None:
        exclusion_sql = "AND id <> %s"
        params.append(exclude_session_id)

    await cursor.execute(
        f"""
        SELECT id, student_id, tutor_id, start_time, end_time
        FROM lesson_student_sessions
        WHERE status = 'scheduled'
          AND (student_id = %s OR tutor_id = %s)
          AND session_date = %s
          AND start_time < %s
          AND end_time > %s
          {exclusion_sql}
        LIMIT 1
        """,
        tuple(params),
    )
    conflict = await cursor.fetchone()
    if not conflict:
        return

    if conflict["student_id"] == student_id:
        raise HTTPException(
            status_code=400,
            detail="Student already has another paid lesson at this time",
        )
    raise HTTPException(
        status_code=400,
        detail="Tutor already has another paid lesson at this time",
    )


@router.get("/lessons", response_model=List[LessonWithDetails])
async def get_lessons(
    subject: Optional[str] = Query(None),
    tutor_id: Optional[int] = Query(None),
    school_id: Optional[int] = Query(None),
    is_online: Optional[bool] = Query(None),
    current_user: UserInDB = Depends(get_current_user),
):
    """Получить список дополнительных занятий с фильтрацией."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            resolved_school_id = await _resolve_lesson_school_id(current_user, school_id)
            query = """
                SELECT
                    l.*,
                    TRIM(BOTH '"' FROM p.full_name) as tutor_name,
                    s.name as school_name,
                    COUNT(e.id) as enrolled_count,
                    AVG(r.rating) as average_rating,
                    COUNT(r.id) as reviews_count
                FROM additional_lessons l
                LEFT JOIN profiles p ON l.tutor_id = p.user_id
                LEFT JOIN schools s ON l.school_id = s.id
                LEFT JOIN lesson_enrollments e ON l.id = e.lesson_id AND e.status = 'enrolled'
                LEFT JOIN lesson_reviews r ON l.id = r.lesson_id
                WHERE 1=1
            """
            params = []

            if _user_role(current_user) in {"tutor", "teacher"}:
                tutor_id = _user_id(current_user)
            if subject:
                query += " AND l.subject = %s"
                params.append(subject)
            if tutor_id:
                query += " AND l.tutor_id = %s"
                params.append(tutor_id)
            if resolved_school_id:
                query += " AND l.school_id = %s"
                params.append(resolved_school_id)
            if is_online is not None:
                query += " AND l.is_online = %s"
                params.append(is_online)

            query += " GROUP BY l.id ORDER BY l.created_at DESC"
            await cursor.execute(query, params)
            lessons = await cursor.fetchall()
            return [LessonWithDetails(**lesson) for lesson in lessons]
    finally:
        conn.close()


@router.post("/lessons", response_model=Lesson)
async def create_lesson(
    lesson: LessonCreate,
    current_user: UserInDB = Depends(get_current_user),
):
    """Создать новое дополнительное занятие."""
    if _user_role(current_user) not in ["tutor", "teacher", "admin", "owner", "superadmin", "root"]:
        raise HTTPException(status_code=403, detail="Only tutors can create lessons")

    lesson_school_id = await _resolve_lesson_school_id(current_user, lesson.school_id)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO additional_lessons
                (title, description, subject, tutor_id, school_id, price, max_students,
                 duration_minutes, is_online, location, online_link)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    lesson.title,
                    lesson.description,
                    lesson.subject,
                    _user_id(current_user),
                    lesson_school_id,
                    lesson.price,
                    lesson.max_students,
                    lesson.duration_minutes,
                    lesson.is_online,
                    lesson.location,
                    lesson.online_link,
                ),
            )
            lesson_id = cursor.lastrowid
            await conn.commit()

            await cursor.execute("SELECT * FROM additional_lessons WHERE id = %s", (lesson_id,))
            result = await cursor.fetchone()
            return Lesson(**result)
    finally:
        conn.close()


@router.get("/my-lessons", response_model=List[LessonWithDetails])
async def get_my_lessons(current_user: UserInDB = Depends(get_current_user)):
    """Получить мои дополнительные занятия."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            if _user_role(current_user) in ["tutor", "teacher"]:
                await cursor.execute(
                    """
                    SELECT
                        l.*,
                        TRIM(BOTH '"' FROM p.full_name) as tutor_name,
                        s.name as school_name,
                        COUNT(e.id) as enrolled_count,
                        AVG(r.rating) as average_rating,
                        COUNT(r.id) as reviews_count
                    FROM additional_lessons l
                    LEFT JOIN profiles p ON l.tutor_id = p.user_id
                    LEFT JOIN schools s ON l.school_id = s.id
                    LEFT JOIN lesson_enrollments e ON l.id = e.lesson_id AND e.status = 'enrolled'
                    LEFT JOIN lesson_reviews r ON l.id = r.lesson_id
                    WHERE l.tutor_id = %s
                    GROUP BY l.id
                    ORDER BY l.created_at DESC
                    """,
                    (_user_id(current_user),),
                )
            else:
                await cursor.execute(
                    """
                    SELECT
                        l.*,
                        TRIM(BOTH '"' FROM p.full_name) as tutor_name,
                        s.name as school_name,
                        COUNT(e.id) as enrolled_count,
                        AVG(r.rating) as average_rating,
                        COUNT(r.id) as reviews_count
                    FROM additional_lessons l
                    LEFT JOIN profiles p ON l.tutor_id = p.user_id
                    LEFT JOIN schools s ON l.school_id = s.id
                    LEFT JOIN lesson_enrollments e ON l.id = e.lesson_id AND e.status = 'enrolled'
                    LEFT JOIN lesson_reviews r ON l.id = r.lesson_id
                    INNER JOIN lesson_enrollments my_enrollment ON l.id = my_enrollment.lesson_id
                    WHERE my_enrollment.student_id = %s
                    GROUP BY l.id
                    ORDER BY l.created_at DESC
                    """,
                    (_user_id(current_user),),
                )

            lessons = await cursor.fetchall()
            return [LessonWithDetails(**lesson) for lesson in lessons]
    finally:
        conn.close()


@router.get("/my-students")
async def get_my_students(current_user: UserInDB = Depends(get_current_user)):
    """Students enrolled in lessons managed by the current tutor or teacher."""
    if _user_role(current_user) not in {"tutor", "teacher"}:
        raise HTTPException(status_code=403, detail="Access denied")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT
                    u.id,
                    TRIM(BOTH '"' FROM COALESCE(p.full_name, u.username)) AS full_name,
                    COUNT(DISTINCT e.lesson_id) AS lessons_count,
                    COALESCE(SUM(e.remaining_lessons), 0) AS remaining_lessons
                FROM additional_lessons l
                JOIN lesson_enrollments e
                  ON e.lesson_id = l.id AND e.status = 'enrolled'
                JOIN users u ON u.id = e.student_id
                LEFT JOIN profiles p ON p.user_id = u.id
                WHERE l.tutor_id = %s
                GROUP BY u.id, p.full_name, u.username
                ORDER BY full_name
                """,
                (_user_id(current_user),),
            )
            return await cursor.fetchall()
    finally:
        conn.close()


@router.get("/lessons/{lesson_id}", response_model=LessonWithDetails)
async def get_lesson(
    lesson_id: int,
    current_user: UserInDB = Depends(get_current_user),
):
    """Получить детали конкретного занятия."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT
                    l.*,
                    TRIM(BOTH '"' FROM p.full_name) as tutor_name,
                    s.name as school_name,
                    COUNT(e.id) as enrolled_count,
                    AVG(r.rating) as average_rating,
                    COUNT(r.id) as reviews_count
                FROM additional_lessons l
                LEFT JOIN profiles p ON l.tutor_id = p.user_id
                LEFT JOIN schools s ON l.school_id = s.id
                LEFT JOIN lesson_enrollments e ON l.id = e.lesson_id AND e.status = 'enrolled'
                LEFT JOIN lesson_reviews r ON l.id = r.lesson_id
                WHERE l.id = %s
                GROUP BY l.id
                """,
                (lesson_id,),
            )
            lesson = await cursor.fetchone()
            if not lesson:
                raise HTTPException(status_code=404, detail="Lesson not found")
            return LessonWithDetails(**lesson)
    finally:
        conn.close()


@router.post("/lessons/{lesson_id}/reviews", response_model=LessonReview)
async def submit_lesson_review(
    lesson_id: int,
    payload: LessonReviewCreate,
    current_user: UserInDB = Depends(get_current_user),
):
    """Оценить преподавателя платного занятия."""
    if _user_role(current_user) != "student":
        raise HTTPException(status_code=403, detail="Only students can review lessons")
    if payload.lesson_id != lesson_id:
        raise HTTPException(status_code=400, detail="Lesson id mismatch")
    if payload.student_id != _user_id(current_user):
        raise HTTPException(status_code=403, detail="You can submit only your own review")
    if payload.rating < 1 or payload.rating > 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            lesson_row = await _get_lesson_row(cursor, lesson_id)
            if not lesson_row:
                raise HTTPException(status_code=404, detail="Lesson not found")

            await _assert_student_can_review_lesson(cursor, lesson_id, _user_id(current_user))

            await cursor.execute(
                """
                INSERT INTO lesson_reviews (lesson_id, student_id, rating, comment)
                VALUES (%s, %s, %s, %s) AS new
                ON DUPLICATE KEY UPDATE
                    rating = new.rating,
                    comment = new.comment,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    lesson_id,
                    _user_id(current_user),
                    payload.rating,
                    payload.comment,
                ),
            )
            await conn.commit()

            await cursor.execute(
                """
                SELECT id, lesson_id, student_id, rating, comment, created_at
                FROM lesson_reviews
                WHERE lesson_id = %s AND student_id = %s
                """,
                (lesson_id, _user_id(current_user)),
            )
            review = await cursor.fetchone()
            return LessonReview(**review)
    finally:
        conn.close()


@router.put("/lessons/{lesson_id}", response_model=Lesson)
async def update_lesson(
    lesson_id: int,
    lesson_update: LessonUpdate,
    current_user: UserInDB = Depends(get_current_user),
):
    """Обновить занятие."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            lesson_row = await _get_lesson_row(cursor, lesson_id)
            await _assert_can_manage_lesson(lesson_row, current_user)

            update_fields = []
            params = []
            for field, value in lesson_update.dict(exclude_unset=True).items():
                update_fields.append(f"{field} = %s")
                params.append(value)

            if update_fields:
                params.append(lesson_id)
                await cursor.execute(
                    f"""
                    UPDATE additional_lessons
                    SET {', '.join(update_fields)}
                    WHERE id = %s
                    """,
                    params,
                )
                await conn.commit()

            await cursor.execute("SELECT * FROM additional_lessons WHERE id = %s", (lesson_id,))
            result = await cursor.fetchone()
            return Lesson(**result)
    finally:
        conn.close()


@router.delete("/lessons/{lesson_id}")
async def delete_lesson(
    lesson_id: int,
    current_user: UserInDB = Depends(get_current_user),
):
    """Удалить занятие."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            lesson_row = await _get_lesson_row(cursor, lesson_id)
            await _assert_can_manage_lesson(lesson_row, current_user)
            await cursor.execute("DELETE FROM additional_lessons WHERE id = %s", (lesson_id,))
            await conn.commit()
            return {"message": "Lesson deleted successfully"}
    finally:
        conn.close()


@router.post("/lessons/{lesson_id}/enroll", response_model=LessonEnrollment)
async def enroll_in_lesson(
    lesson_id: int,
    current_user: UserInDB = Depends(get_current_user),
):
    """Записаться на занятие (только студенты)."""
    if _user_role(current_user) != "student":
        raise HTTPException(status_code=403, detail="Only students can enroll in lessons")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT l.id, l.max_students, l.price, l.school_id, COUNT(e.id) as enrolled_count
                FROM additional_lessons l
                LEFT JOIN lesson_enrollments e ON l.id = e.lesson_id AND e.status = 'enrolled'
                WHERE l.id = %s
                GROUP BY l.id
                """,
                (lesson_id,),
            )
            lesson_info = await cursor.fetchone()
            if not lesson_info:
                raise HTTPException(status_code=404, detail="Lesson not found")
            if lesson_info["enrolled_count"] >= lesson_info["max_students"]:
                raise HTTPException(status_code=400, detail="Lesson is full")
            await _assert_student_in_lesson_school(
                cursor,
                _user_id(current_user),
                lesson_info.get("school_id"),
            )

            await cursor.execute(
                """
                SELECT id FROM lesson_enrollments
                WHERE lesson_id = %s AND student_id = %s
                """,
                (lesson_id, _user_id(current_user)),
            )
            if await cursor.fetchone():
                raise HTTPException(status_code=400, detail="Already enrolled")

            payment_status = "paid" if lesson_info.get("price") in (None, 0, 0.0) else "pending"
            await cursor.execute(
                """
                INSERT INTO lesson_enrollments
                (lesson_id, student_id, status, payment_status, total_lessons, remaining_lessons, purchase_price, purchase_date)
                VALUES (%s, %s, 'enrolled', %s, 1, 1, %s, NOW())
                """,
                (
                    lesson_id,
                    _user_id(current_user),
                    payment_status,
                    lesson_info.get("price"),
                ),
            )
            enrollment_id = cursor.lastrowid
            await conn.commit()

            await cursor.execute("SELECT * FROM lesson_enrollments WHERE id = %s", (enrollment_id,))
            result = await cursor.fetchone()
            return LessonEnrollment(**result)
    finally:
        conn.close()


@router.get("/lessons/{lesson_id}/enrollments", response_model=List[LessonEnrollment])
async def get_lesson_enrollments(
    lesson_id: int,
    current_user: UserInDB = Depends(get_current_user),
):
    """Получить список записавшихся на занятие."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            lesson_row = await _get_lesson_row(cursor, lesson_id)
            await _assert_can_manage_lesson(lesson_row, current_user)

            await cursor.execute(
                """
                SELECT * FROM lesson_enrollments
                WHERE lesson_id = %s
                ORDER BY enrolled_at DESC
                """,
                (lesson_id,),
            )
            enrollments = await cursor.fetchall()
            return [LessonEnrollment(**enrollment) for enrollment in enrollments]
    finally:
        conn.close()


@router.get("/lessons/{lesson_id}/balances", response_model=List[LessonBalanceWithDetails])
async def get_lesson_balances(
    lesson_id: int,
    current_user: UserInDB = Depends(get_current_user),
):
    """Получить остатки пакетов по занятию."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            lesson_row = await _get_lesson_row(cursor, lesson_id)
            await _assert_can_manage_lesson(lesson_row, current_user)
            return await _fetch_lesson_balances(cursor, lesson_id)
    finally:
        conn.close()


@router.post("/lessons/{lesson_id}/issue", response_model=LessonBalanceWithDetails)
async def issue_lesson_balance(
    lesson_id: int,
    payload: LessonBalanceIssueRequest,
    current_user: UserInDB = Depends(get_current_user),
):
    """Выдать пользователю пакет платных занятий."""
    if payload.total_lessons < 1:
        raise HTTPException(status_code=400, detail="total_lessons must be at least 1")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            lesson_row = await _get_lesson_row(cursor, lesson_id)
            await _assert_can_manage_lesson(lesson_row, current_user)

            await cursor.execute(
                "SELECT id, role FROM users WHERE id = %s",
                (payload.student_id,),
            )
            student_row = await cursor.fetchone()
            if not student_row or student_row["role"] != "student":
                raise HTTPException(status_code=404, detail="Student not found")
            await _assert_student_in_lesson_school(
                cursor,
                payload.student_id,
                lesson_row.get("school_id"),
            )

            await cursor.execute(
                """
                SELECT id, total_lessons, remaining_lessons
                FROM lesson_enrollments
                WHERE lesson_id = %s AND student_id = %s
                """,
                (lesson_id, payload.student_id),
            )
            existing = await cursor.fetchone()

            current_id = _user_id(current_user)
            if existing:
                if payload.add_to_existing:
                    await cursor.execute(
                        """
                        UPDATE lesson_enrollments
                        SET total_lessons = COALESCE(total_lessons, 1) + %s,
                            remaining_lessons = COALESCE(remaining_lessons, COALESCE(total_lessons, 1), 1) + %s,
                            purchase_price = COALESCE(%s, purchase_price),
                            purchase_date = COALESCE(purchase_date, NOW()),
                            payment_status = 'paid',
                            payment_method = COALESCE(%s, payment_method),
                            valid_until = %s,
                            notes = %s,
                            assigned_by = %s,
                            status = 'enrolled'
                        WHERE id = %s
                        """,
                        (
                            payload.total_lessons,
                            payload.total_lessons,
                            payload.purchase_price,
                            payload.payment_method,
                            payload.valid_until,
                            payload.notes,
                            current_id,
                            existing["id"],
                        ),
                    )
                    enrollment_id = existing["id"]
                else:
                    await cursor.execute(
                        """
                        UPDATE lesson_enrollments
                        SET total_lessons = %s,
                            remaining_lessons = %s,
                            purchase_price = %s,
                            purchase_date = NOW(),
                            payment_status = 'paid',
                            payment_method = %s,
                            valid_until = %s,
                            notes = %s,
                            assigned_by = %s,
                            status = 'enrolled'
                        WHERE id = %s
                        """,
                        (
                            payload.total_lessons,
                            payload.total_lessons,
                            payload.purchase_price,
                            payload.payment_method,
                            payload.valid_until,
                            payload.notes,
                            current_id,
                            existing["id"],
                        ),
                    )
                    enrollment_id = existing["id"]
            else:
                await _assert_lesson_capacity_available(
                    cursor,
                    lesson_id,
                    payload.student_id,
                )
                await cursor.execute(
                    """
                    INSERT INTO lesson_enrollments
                    (lesson_id, student_id, status, payment_status, purchase_price, purchase_date,
                     payment_method, total_lessons, remaining_lessons, assigned_by, valid_until, notes)
                    VALUES (%s, %s, 'enrolled', 'paid', %s, NOW(), %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        lesson_id,
                        payload.student_id,
                        payload.purchase_price,
                        payload.payment_method,
                        payload.total_lessons,
                        payload.total_lessons,
                        current_id,
                        payload.valid_until,
                        payload.notes,
                    ),
                )
                enrollment_id = cursor.lastrowid

            await conn.commit()
            balances = await _fetch_lesson_balances(cursor, lesson_id)
            for item in balances:
                if item.enrollment_id == enrollment_id:
                    return item
            raise HTTPException(status_code=500, detail="Failed to issue lesson balance")
    finally:
        conn.close()


@router.get("/lessons/{lesson_id}/sessions", response_model=List[LessonSessionWithDetails])
async def get_lesson_sessions(
    lesson_id: int,
    current_user: UserInDB = Depends(get_current_user),
):
    """Получить сеансы платного занятия."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            lesson_row = await _get_lesson_row(cursor, lesson_id)
            await _assert_can_manage_lesson(lesson_row, current_user)
            return await _fetch_lesson_sessions(cursor, lesson_id)
    finally:
        conn.close()


@router.post("/lessons/{lesson_id}/sessions", response_model=LessonSessionWithDetails)
async def create_lesson_session(
    lesson_id: int,
    payload: LessonSessionCreate,
    current_user: UserInDB = Depends(get_current_user),
):
    """Назначить конкретный сеанс платного занятия ученику."""
    if payload.lessons_reserved < 1:
        raise HTTPException(status_code=400, detail="lessons_reserved must be at least 1")
    if payload.end_time <= payload.start_time:
        raise HTTPException(status_code=400, detail="End time must be after start time")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            lesson_row = await _get_lesson_row(cursor, lesson_id)
            await _assert_can_manage_lesson(lesson_row, current_user)

            await cursor.execute(
                """
                SELECT id, total_lessons, remaining_lessons, status, valid_until
                FROM lesson_enrollments
                WHERE lesson_id = %s AND student_id = %s
                """,
                (lesson_id, payload.student_id),
            )
            enrollment = await cursor.fetchone()
            if not enrollment:
                raise HTTPException(status_code=400, detail="Student has no lesson balance")
            if enrollment["status"] == "cancelled":
                raise HTTPException(status_code=400, detail="Lesson balance is cancelled")
            if enrollment.get("valid_until") and enrollment["valid_until"] < datetime.combine(payload.session_date, payload.start_time):
                raise HTTPException(status_code=400, detail="Lesson balance is expired")

            remaining = int(enrollment.get("remaining_lessons") or 0)
            if remaining < payload.lessons_reserved:
                raise HTTPException(
                    status_code=400,
                    detail=f"Not enough lessons remaining ({remaining})",
                )

            await _assert_no_session_overlap(
                cursor,
                student_id=payload.student_id,
                tutor_id=lesson_row["tutor_id"],
                session_date=payload.session_date,
                start_time=payload.start_time,
                end_time=payload.end_time,
            )

            await cursor.execute(
                """
                INSERT INTO lesson_student_sessions
                (lesson_id, enrollment_id, student_id, tutor_id, school_id, session_date, start_time, end_time,
                 is_online, location, online_link, status, lessons_reserved, notes, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'scheduled', %s, %s, %s)
                """,
                (
                    lesson_id,
                    enrollment["id"],
                    payload.student_id,
                    lesson_row["tutor_id"],
                    lesson_row["school_id"],
                    payload.session_date,
                    payload.start_time,
                    payload.end_time,
                    lesson_row["is_online"],
                    lesson_row["location"],
                    payload.online_link or lesson_row["online_link"],
                    payload.lessons_reserved,
                    payload.notes,
                    _user_id(current_user),
                ),
            )
            session_id = cursor.lastrowid

            await cursor.execute(
                """
                UPDATE lesson_enrollments
                SET remaining_lessons = remaining_lessons - %s
                WHERE id = %s
                """,
                (payload.lessons_reserved, enrollment["id"]),
            )
            await conn.commit()

            sessions = await _fetch_lesson_sessions(cursor, lesson_id)
            for item in sessions:
                if item.id == session_id:
                    return item
            raise HTTPException(status_code=500, detail="Failed to create lesson session")
    finally:
        conn.close()


@router.patch("/lesson-sessions/{session_id}", response_model=LessonSessionWithDetails)
async def update_lesson_session(
    session_id: int,
    payload: LessonSessionUpdate,
    current_user: UserInDB = Depends(get_current_user),
):
    """Обновить сеанс платного занятия, в том числе вернуть занятие при отмене."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT s.*, l.tutor_id, l.school_id
                FROM lesson_student_sessions s
                JOIN additional_lessons l ON l.id = s.lesson_id
                WHERE s.id = %s
                """,
                (session_id,),
            )
            session = await cursor.fetchone()
            if not session:
                raise HTTPException(status_code=404, detail="Lesson session not found")

            await _assert_can_manage_lesson(session, current_user)

            new_status = payload.status or session["status"]
            updates = []
            params: List = []

            target_session_date = payload.session_date or session["session_date"]
            target_start_time = payload.start_time or session["start_time"]
            target_end_time = payload.end_time or session["end_time"]

            if target_end_time <= target_start_time:
                raise HTTPException(status_code=400, detail="End time must be after start time")

            if new_status == "scheduled":
                await _assert_no_session_overlap(
                    cursor,
                    student_id=session["student_id"],
                    tutor_id=session["tutor_id"],
                    session_date=target_session_date,
                    start_time=target_start_time,
                    end_time=target_end_time,
                    exclude_session_id=session_id,
                )

            if session.get("enrollment_id") and new_status == "scheduled":
                await cursor.execute(
                    """
                    SELECT valid_until, remaining_lessons
                    FROM lesson_enrollments
                    WHERE id = %s
                    """,
                    (session["enrollment_id"],),
                )
                enrollment_meta = await cursor.fetchone()
                if (
                    enrollment_meta
                    and enrollment_meta.get("valid_until")
                    and enrollment_meta["valid_until"]
                    < datetime.combine(target_session_date, target_start_time)
                ):
                    raise HTTPException(status_code=400, detail="Lesson balance is expired")

            if payload.session_date is not None:
                updates.append("session_date = %s")
                params.append(payload.session_date)
            if payload.start_time is not None:
                updates.append("start_time = %s")
                params.append(payload.start_time)
            if payload.end_time is not None:
                updates.append("end_time = %s")
                params.append(payload.end_time)
            if payload.online_link is not None:
                updates.append("online_link = %s")
                params.append(payload.online_link or None)
            if payload.notes is not None:
                updates.append("notes = %s")
                params.append(payload.notes)
            if payload.status is not None:
                updates.append("status = %s")
                params.append(payload.status)
                if payload.status == "cancelled":
                    updates.append("cancelled_at = NOW()")
                if payload.status == "completed":
                    updates.append("completed_at = NOW()")

            if payload.status == "cancelled" and session["status"] != "cancelled":
                await cursor.execute(
                    """
                    UPDATE lesson_enrollments
                    SET remaining_lessons = remaining_lessons + %s
                    WHERE id = %s
                    """,
                    (session["lessons_reserved"], session["enrollment_id"]),
                )
                await cursor.execute(
                    """
                    UPDATE lesson_enrollments
                    SET status = 'enrolled'
                    WHERE id = %s
                    """,
                    (session["enrollment_id"],),
                )
            elif session["status"] == "cancelled" and new_status == "scheduled":
                await cursor.execute(
                    """
                    SELECT remaining_lessons
                    FROM lesson_enrollments
                    WHERE id = %s
                    """,
                    (session["enrollment_id"],),
                )
                enrollment = await cursor.fetchone()
                remaining = int(enrollment["remaining_lessons"]) if enrollment else 0
                if remaining < session["lessons_reserved"]:
                    raise HTTPException(status_code=400, detail="Not enough lessons remaining to restore session")
                await cursor.execute(
                    """
                    UPDATE lesson_enrollments
                    SET remaining_lessons = remaining_lessons - %s
                    WHERE id = %s
                    """,
                    (session["lessons_reserved"], session["enrollment_id"]),
                )
                await cursor.execute(
                    """
                    UPDATE lesson_enrollments
                    SET status = 'enrolled'
                    WHERE id = %s
                    """,
                    (session["enrollment_id"],),
                )

            if payload.status == "completed" and session["status"] != "completed":
                await cursor.execute(
                    """
                    SELECT remaining_lessons
                    FROM lesson_enrollments
                    WHERE id = %s
                    """,
                    (session["enrollment_id"],),
                )
                enrollment_after = await cursor.fetchone()
                remaining_after = int(enrollment_after["remaining_lessons"]) if enrollment_after else 0
                await cursor.execute(
                    """
                    SELECT COUNT(*) AS scheduled_count
                    FROM lesson_student_sessions
                    WHERE enrollment_id = %s
                      AND status = 'scheduled'
                      AND id <> %s
                    """,
                    (session["enrollment_id"], session_id),
                )
                scheduled_left = await cursor.fetchone()
                if remaining_after <= 0 and int(scheduled_left["scheduled_count"] or 0) == 0:
                    await cursor.execute(
                        """
                        UPDATE lesson_enrollments
                        SET status = 'completed'
                        WHERE id = %s
                        """,
                        (session["enrollment_id"],),
                    )

            if updates:
                params.append(session_id)
                await cursor.execute(
                    f"""
                    UPDATE lesson_student_sessions
                    SET {', '.join(updates)}
                    WHERE id = %s
                    """,
                    params,
                )
                await conn.commit()

            await cursor.execute(
                """
                SELECT s.lesson_id
                FROM lesson_student_sessions s
                WHERE s.id = %s
                """,
                (session_id,),
            )
            lesson_ref = await cursor.fetchone()
            sessions = await _fetch_lesson_sessions(cursor, lesson_ref["lesson_id"])
            for item in sessions:
                if item.id == session_id:
                    return item
            raise HTTPException(status_code=500, detail="Failed to update lesson session")
    finally:
        conn.close()
