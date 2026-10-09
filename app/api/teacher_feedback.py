from datetime import datetime, timedelta
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.entitlements import require_school_feature
from app.core.security import get_current_user
from app.db.connection import get_db_connection
from app.services.notifications import notify_many
from app.services.achievement_service import evaluate_and_award_achievements

router = APIRouter()
logger = logging.getLogger(__name__)


class TeacherFeedbackCreateRequest(BaseModel):
    class_id: int
    subject_id: int
    teacher_id: int
    lesson_date: str
    lesson_number: int
    rating: int
    comment: str | None = None


@router.get("/teacher-feedback/week")
async def get_teacher_feedback_for_week(
    class_id: int = Query(...),
    week_start_date: str = Query(...),
    current_user=Depends(get_current_user),
):
    await require_school_feature(
        current_user,
        "teacher_feedback_enabled",
        detail="Отзывы учителям отключены в настройках школы",
    )
    role = getattr(current_user, "role", None) or current_user.get("role")
    if role != "student":
        raise HTTPException(status_code=403, detail="Only students can access")
    student_id = current_user.id if hasattr(current_user, "id") else current_user["id"]

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT 1 FROM class_students WHERE class_id = %s AND student_id = %s",
                (class_id, student_id),
            )
            if not await cursor.fetchone():
                raise HTTPException(status_code=403, detail="Access denied")

            week_start = datetime.strptime(week_start_date, "%Y-%m-%d").date()
            week_end = week_start + timedelta(days=6)

            await cursor.execute(
                """
                SELECT
                    class_id, subject_id, teacher_id, lesson_date, lesson_number,
                    rating, comment, created_at
                FROM teacher_lesson_feedback
                WHERE student_id = %s
                  AND class_id = %s
                  AND lesson_date BETWEEN %s AND %s
                ORDER BY lesson_date, lesson_number
                """,
                (student_id, class_id, week_start, week_end),
            )
            rows = await cursor.fetchall()

        return {
            "items": [
                {
                    "class_id": row["class_id"],
                    "subject_id": row["subject_id"],
                    "teacher_id": row["teacher_id"],
                    "lesson_date": str(row["lesson_date"]),
                    "lesson_number": row["lesson_number"],
                    "rating": row["rating"],
                    "comment": row.get("comment") or "",
                    "created_at": row["created_at"].isoformat()
                    if row.get("created_at")
                    else None,
                }
                for row in rows
            ]
        }
    finally:
        conn.close()


@router.post("/teacher-feedback")
async def submit_teacher_feedback(
    body: TeacherFeedbackCreateRequest,
    current_user=Depends(get_current_user),
):
    await require_school_feature(
        current_user,
        "teacher_feedback_enabled",
        detail="Отзывы учителям отключены в настройках школы",
    )
    role = getattr(current_user, "role", None) or current_user.get("role")
    if role != "student":
        raise HTTPException(status_code=403, detail="Only students can rate teachers")

    if body.rating < 1 or body.rating > 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")

    student_id = current_user.id if hasattr(current_user, "id") else current_user["id"]
    lesson_date = datetime.strptime(body.lesson_date, "%Y-%m-%d").date()
    today = datetime.now().date()
    if lesson_date > today:
        raise HTTPException(status_code=400, detail="You can rate only completed lessons")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT COALESCE(NULLIF(TRIM(BOTH '"' FROM p.full_name), ''), u.username) AS student_name
                FROM users u
                LEFT JOIN profiles p ON p.user_id = u.id
                WHERE u.id = %s
                """,
                (student_id,),
            )
            student_row = await cursor.fetchone()
            student_name = student_row["student_name"] if student_row else "Ученик"
            await cursor.execute(
                "SELECT 1 FROM class_students WHERE class_id = %s AND student_id = %s",
                (body.class_id, student_id),
            )
            if not await cursor.fetchone():
                raise HTTPException(status_code=403, detail="Access denied")

            weekday = lesson_date.weekday() + 1
            await cursor.execute(
                """
                SELECT 1
                FROM timetable_templates
                WHERE class_id = %s
                  AND subject_id = %s
                  AND teacher_id = %s
                  AND day_of_week = %s
                  AND lesson_number = %s
                LIMIT 1
                """,
                (
                    body.class_id,
                    body.subject_id,
                    body.teacher_id,
                    weekday,
                    body.lesson_number,
                ),
            )
            if not await cursor.fetchone():
                raise HTTPException(status_code=400, detail="Lesson not found for rating")

            await cursor.execute(
                """
                INSERT INTO teacher_lesson_feedback
                    (class_id, subject_id, teacher_id, student_id, lesson_date, lesson_number, rating, comment)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    rating = VALUES(rating),
                    comment = VALUES(comment),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    body.class_id,
                    body.subject_id,
                    body.teacher_id,
                    student_id,
                    lesson_date,
                    body.lesson_number,
                    body.rating,
                    (body.comment or "").strip(),
                ),
            )
            await conn.commit()
            await cursor.execute("SELECT name FROM classes WHERE id = %s", (body.class_id,))
            class_row = await cursor.fetchone()
            await cursor.execute("SELECT name FROM subjects WHERE id = %s", (body.subject_id,))
            subject_row = await cursor.fetchone()

        class_name = class_row["name"] if class_row else ""
        subject_name = subject_row["name"] if subject_row else ""
        await notify_many(
            [body.teacher_id],
            "Новая оценка урока",
            f"{student_name} оценил(а) урок по предмету «{subject_name}» на {body.rating}/5"
            f"{' (' + class_name + ')' if class_name else ''}.",
            notification_type="teacher_feedback",
            sender_id=student_id,
            entity_type="teacher_feedback",
            route_name="teacher_rating",
        )
        try:
            await evaluate_and_award_achievements(
                body.teacher_id,
                only_codes={"politeness"},
            )
        except Exception:
            logger.exception("Failed to evaluate teacher achievements")
        return {"status": "ok"}
    finally:
        conn.close()


@router.get("/teacher-feedback/me/summary")
async def get_my_teacher_feedback_summary(current_user=Depends(get_current_user)):
    await require_school_feature(
        current_user,
        "teacher_feedback_enabled",
        detail="Отзывы учителям отключены в настройках школы",
    )
    role = getattr(current_user, "role", None) or current_user.get("role")
    if role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can access")
    teacher_id = current_user.id if hasattr(current_user, "id") else current_user["id"]

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT
                    COUNT(*) AS reviews_count,
                    ROUND(AVG(rating), 2) AS average_rating
                FROM teacher_lesson_feedback
                WHERE teacher_id = %s
                """,
                (teacher_id,),
            )
            summary = await cursor.fetchone()

            await cursor.execute(
                """
                SELECT rating, COUNT(*) AS count
                FROM teacher_lesson_feedback
                WHERE teacher_id = %s
                GROUP BY rating
                ORDER BY rating DESC
                """,
                (teacher_id,),
            )
            distribution_rows = await cursor.fetchall()

            await cursor.execute(
                """
                SELECT
                    tlf.rating,
                    tlf.comment,
                    tlf.lesson_date,
                    c.name AS class_name,
                    s.name AS subject_name
                FROM teacher_lesson_feedback tlf
                LEFT JOIN classes c ON c.id = tlf.class_id
                LEFT JOIN subjects s ON s.id = tlf.subject_id
                WHERE tlf.teacher_id = %s
                  AND COALESCE(TRIM(tlf.comment), '') <> ''
                ORDER BY tlf.lesson_date DESC, tlf.id DESC
                LIMIT 10
                """,
                (teacher_id,),
            )
            recent = await cursor.fetchall()

        distribution = {str(i): 0 for i in range(1, 6)}
        for row in distribution_rows:
            distribution[str(row["rating"])] = row["count"]

        return {
            "average_rating": float(summary["average_rating"] or 0),
            "reviews_count": int(summary["reviews_count"] or 0),
            "distribution": distribution,
            "recent_comments": [
                {
                    "rating": row["rating"],
                    "comment": row.get("comment") or "",
                    "lesson_date": str(row["lesson_date"]),
                    "class_name": row.get("class_name") or "",
                    "subject_name": row.get("subject_name") or "",
                }
                for row in recent
            ],
        }
    finally:
        conn.close()
