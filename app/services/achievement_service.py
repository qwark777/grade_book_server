from __future__ import annotations

from typing import Iterable, Optional

import aiomysql

from app.db.connection import get_db_connection
from app.services.notifications import notify_many


ACHIEVEMENT_CATALOG = (
    {
        "code": "class_president",
        "title": "Староста",
        "description": "Назначается администрацией школы.",
        "image_url": "/static/achievements/class_president.png",
        "rarity": "rare",
        "roles": {"student"},
    },
    {
        "code": "first_place",
        "title": "Первое место",
        "description": "Первое место в рейтинге своего класса.",
        "image_url": "/static/achievements/first_place.png",
        "rarity": "legendary",
        "roles": {"student"},
    },
    {
        "code": "heart_of_class",
        "title": "Сердце класса",
        "description": "Отправлено 100 сообщений в школьных чатах.",
        "image_url": "/static/achievements/heart_of_class.png",
        "rarity": "heart",
        "roles": {"student"},
    },
    {
        "code": "olimpiad_coach",
        "title": "Олимпиадный наставник",
        "description": "Выдаётся школой за подтверждённые олимпиадные достижения.",
        "image_url": "/static/achievements/olimpiad_coach.png",
        "rarity": "rare",
        "roles": {"teacher"},
    },
    {
        "code": "politeness",
        "title": "Вежливость",
        "description": "Получено 20 положительных отзывов об уроках.",
        "image_url": "/static/achievements/politeness.png",
        "rarity": "common",
        "roles": {"teacher"},
    },
    {
        "code": "second_place",
        "title": "Второе место",
        "description": "Второе место в рейтинге своего класса.",
        "image_url": "/static/achievements/second_place.png",
        "rarity": "common",
        "roles": {"student"},
    },
    {
        "code": "third_place",
        "title": "Третье место",
        "description": "Третье место в рейтинге своего класса.",
        "image_url": "/static/achievements/third_place.png",
        "rarity": "common",
        "roles": {"student"},
    },
    {
        "code": "first_grade",
        "title": "Первая оценка",
        "description": "Получена первая оценка в электронном журнале.",
        "image_url": "/static/achievements/first_grade.png",
        "rarity": "common",
        "roles": {"student"},
    },
    {
        "code": "homework_master",
        "title": "Мастер ДЗ",
        "description": "Выполнено 50 домашних заданий.",
        "image_url": "/static/achievements/homework_master.png",
        "rarity": "rare",
        "roles": {"student"},
    },
    {
        "code": "social_butterfly",
        "title": "Душа общения",
        "description": "Отправлено 100 сообщений в школьных чатах.",
        "image_url": "/static/achievements/social_butterfly.png",
        "rarity": "heart",
        "roles": {"student"},
    },
    {
        "code": "week_warrior",
        "title": "Неделя в деле",
        "description": "Активность каждый день учебной недели.",
        "image_url": "/static/achievements/week_warrior.png",
        "rarity": "common",
        "roles": {"student"},
    },
    {
        "code": "perfect_attendance",
        "title": "Без пропусков",
        "description": "100% посещаемости за месяц.",
        "image_url": "/static/achievements/perfect_attendance.png",
        "rarity": "rare",
        "roles": {"student"},
    },
    {
        "code": "excellent_student",
        "title": "Отличник",
        "description": "Все оценки за период — пятёрки.",
        "image_url": "/static/achievements/excellent_student.png",
        "rarity": "legendary",
        "roles": {"student"},
    },
)

_CATALOG_BY_CODE = {item["code"]: item for item in ACHIEVEMENT_CATALOG}
_ROLE_CODES = {
    "student": {
        "class_president",
        "first_place",
        "second_place",
        "third_place",
        "heart_of_class",
        "first_grade",
        "homework_master",
        "social_butterfly",
        "week_warrior",
        "perfect_attendance",
        "excellent_student",
    },
    "teacher": {"politeness", "olimpiad_coach"},
}
_MANUAL_CODES = {"class_president", "olimpiad_coach"}
_PODIUM_CODE_BY_RANK = {1: "first_place", 2: "second_place", 3: "third_place"}


async def notify_achievement_awarded(
    user_id: int,
    achievement_id: int,
    title: str,
) -> None:
    await notify_many(
        [user_id],
        "Новое достижение",
        f"Вы получили достижение «{title}».",
        notification_type="achievement",
        entity_type="achievement",
        entity_id=achievement_id,
        route_name="achievements",
        route_params={"achievement_id": achievement_id},
    )


def catalog_codes_for_role(role: str) -> set[str]:
    return set(_ROLE_CODES.get((role or "").lower(), set()))


def achievement_catalog_metadata(code: str) -> dict:
    item = _CATALOG_BY_CODE.get(code)
    if item is None:
        return {
            "is_manual": True,
            "roles": ["student", "teacher"],
        }
    return {
        "is_manual": code in _MANUAL_CODES,
        "roles": sorted(item["roles"]),
    }


def can_manually_award_achievement(code: str, role: str) -> bool:
    item = _CATALOG_BY_CODE.get(code)
    if item is None:
        return True
    return code in _MANUAL_CODES and (role or "").lower() in item["roles"]


def _progress_item(
    achievement_id: int,
    code: str,
    current: int,
    target: int,
    label: str,
    unit: Optional[str] = None,
    is_completed: bool = False,
) -> dict:
    safe_target = max(1, int(target))
    safe_current = max(0, min(int(current), safe_target))
    return {
        "achievement_id": int(achievement_id),
        "code": code,
        "current": safe_current,
        "target": safe_target,
        "label": label,
        "unit": unit,
        "is_completed": bool(is_completed or safe_current >= safe_target),
    }


def competition_rank(rows: list[dict], user_id: int) -> Optional[int]:
    """Return 1, 1, 3 style rank for an already score-sorted leaderboard."""
    previous_score = None
    current_rank = 0
    for index, row in enumerate(rows):
        score = float(row.get("total_points") or 0)
        if previous_score is None or score < previous_score:
            current_rank = index + 1
            previous_score = score
        if int(row.get("student_id") or 0) == int(user_id):
            return current_rank
    return None


async def ensure_achievement_catalog() -> None:
    """Upsert the canonical catalog without touching custom achievements."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.executemany(
                """
                INSERT INTO achievements
                    (code, title, description, image_url, rarity)
                VALUES (%s, %s, %s, %s, %s)
                AS incoming
                ON DUPLICATE KEY UPDATE
                    title = incoming.title,
                    description = incoming.description,
                    image_url = incoming.image_url,
                    rarity = incoming.rarity
                """,
                [
                    (
                        item["code"],
                        item["title"],
                        item["description"],
                        item["image_url"],
                        item["rarity"],
                    )
                    for item in ACHIEVEMENT_CATALOG
                ],
            )
            await conn.commit()
    finally:
        conn.close()


async def _class_leaderboard(cursor, class_id: int) -> list[dict]:
    await cursor.execute(
        """
        SELECT start_date, end_date
        FROM academic_periods
        WHERE school_id = (SELECT school_id FROM classes WHERE id = %s)
          AND is_active = TRUE
          AND start_date <= CURDATE()
          AND end_date >= CURDATE()
        ORDER BY end_date DESC
        LIMIT 1
        """,
        (class_id,),
    )
    period = await cursor.fetchone()
    if not period:
        await cursor.execute(
            """
            SELECT start_date, end_date
            FROM academic_periods
            WHERE school_id = (SELECT school_id FROM classes WHERE id = %s)
            ORDER BY end_date DESC
            LIMIT 1
            """,
            (class_id,),
        )
        period = await cursor.fetchone()
    if not period:
        return []
    params = [period["start_date"], period["end_date"], class_id]

    await cursor.execute(
        f"""
        SELECT
            cs.student_id,
            COUNT(g.id) AS grade_count,
            COALESCE(SUM(
                CASE g.value
                    WHEN 5 THEN 10
                    WHEN 4 THEN 5
                    WHEN 3 THEN -3
                    WHEN 2 THEN -6
                    ELSE 0
                END * COALESCE(ss.coefficient, s.coefficient, 1)
            ), 0) AS total_points
        FROM class_students cs
        LEFT JOIN grades g
          ON g.student_id = cs.student_id
          AND g.date BETWEEN %s AND %s
        LEFT JOIN subjects s ON s.id = g.subject_id
        LEFT JOIN classes class_scope ON class_scope.id = cs.class_id
        LEFT JOIN school_subjects ss
          ON ss.school_id = class_scope.school_id
         AND ss.subject_id = g.subject_id
        WHERE cs.class_id = %s
        GROUP BY cs.student_id
        HAVING COUNT(g.id) > 0
        ORDER BY total_points DESC, cs.student_id ASC
        """,
        tuple(params),
    )
    return list(await cursor.fetchall())


async def _student_rank(cursor, user_id: int) -> Optional[int]:
    await cursor.execute(
        "SELECT class_id FROM class_students WHERE student_id = %s LIMIT 1",
        (user_id,),
    )
    class_row = await cursor.fetchone()
    if not class_row:
        return None
    rows = await _class_leaderboard(cursor, int(class_row["class_id"]))
    return competition_rank(rows, user_id)


async def evaluate_and_award_achievements(
    user_id: int,
    only_codes: Optional[Iterable[str]] = None,
) -> list[dict]:
    """Calculate role-appropriate progress and persist newly earned awards."""
    await ensure_achievement_catalog()
    requested_codes = set(only_codes or ())
    conn = await get_db_connection()
    try:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("SELECT role FROM users WHERE id = %s", (user_id,))
            user_row = await cursor.fetchone()
            if not user_row:
                return []
            role = (user_row.get("role") or "").lower()
            role_codes = catalog_codes_for_role(role)
            if requested_codes:
                role_codes &= requested_codes
            if not role_codes:
                return []

            await cursor.execute(
                "SELECT id, code FROM achievements WHERE code IN ({})".format(
                    ",".join(["%s"] * len(role_codes))
                ),
                tuple(sorted(role_codes)),
            )
            achievement_ids = {
                row["code"]: int(row["id"]) for row in await cursor.fetchall()
            }
            if not achievement_ids:
                return []

            await cursor.execute(
                """
                SELECT a.code
                FROM user_achievements ua
                JOIN achievements a ON a.id = ua.achievement_id
                WHERE ua.user_id = %s
                """,
                (user_id,),
            )
            completed_codes = {
                row["code"] for row in await cursor.fetchall() if row.get("code")
            }

            progress: list[dict] = []
            if role == "student":
                rank = (
                    await _student_rank(cursor, user_id)
                    if role_codes & set(_PODIUM_CODE_BY_RANK.values())
                    else None
                )
                for place, code in _PODIUM_CODE_BY_RANK.items():
                    if code not in achievement_ids:
                        continue
                    is_exact_place = rank == place
                    progress.append(
                        _progress_item(
                            achievement_ids[code],
                            code,
                            1 if is_exact_place else 0,
                            1,
                            (
                                f"Текущее место в классе: {rank}"
                                if rank is not None
                                else "Нет оценок за учебный период"
                            ),
                            is_completed=code in completed_codes or is_exact_place,
                        )
                    )

                if "heart_of_class" in achievement_ids:
                    await cursor.execute(
                        "SELECT COUNT(*) AS value FROM messages WHERE sender_id = %s",
                        (user_id,),
                    )
                    row = await cursor.fetchone()
                    sent_messages = int((row or {}).get("value") or 0)
                    progress.append(
                        _progress_item(
                            achievement_ids["heart_of_class"],
                            "heart_of_class",
                            sent_messages,
                            100,
                            "Сообщений в школьных чатах",
                            "сообщений",
                            "heart_of_class" in completed_codes,
                        )
                    )

                if "social_butterfly" in achievement_ids:
                    await cursor.execute(
                        "SELECT COUNT(*) AS value FROM messages WHERE sender_id = %s",
                        (user_id,),
                    )
                    row = await cursor.fetchone()
                    sent_messages = int((row or {}).get("value") or 0)
                    progress.append(
                        _progress_item(
                            achievement_ids["social_butterfly"],
                            "social_butterfly",
                            sent_messages,
                            100,
                            "Отправлено сообщений",
                            "сообщений",
                            "social_butterfly" in completed_codes,
                        )
                    )

                if "first_grade" in achievement_ids or "excellent_student" in achievement_ids:
                    await cursor.execute(
                        """
                        SELECT
                            COUNT(*) AS grade_count,
                            COALESCE(SUM(CASE WHEN value = 5 THEN 1 ELSE 0 END), 0)
                                AS five_count
                        FROM grades
                        WHERE student_id = %s
                        """,
                        (user_id,),
                    )
                    row = await cursor.fetchone() or {}
                    grade_count = int(row.get("grade_count") or 0)
                    five_count = int(row.get("five_count") or 0)
                    excellent_percent = (
                        round((five_count / grade_count) * 100)
                        if grade_count
                        else 0
                    )

                    if "first_grade" in achievement_ids:
                        progress.append(
                            _progress_item(
                                achievement_ids["first_grade"],
                                "first_grade",
                                grade_count,
                                1,
                                "Первая оценка",
                                "оценка",
                                "first_grade" in completed_codes,
                            )
                        )
                    if "excellent_student" in achievement_ids:
                        progress.append(
                            _progress_item(
                                achievement_ids["excellent_student"],
                                "excellent_student",
                                excellent_percent,
                                100,
                                "Оценок «5»",
                                "%",
                                "excellent_student" in completed_codes,
                            )
                        )

                if "homework_master" in achievement_ids:
                    await cursor.execute(
                        """
                        SELECT COUNT(DISTINCT homework_id) AS value
                        FROM (
                            SELECT homework_id
                            FROM homework_submissions
                            WHERE student_id = %s
                            UNION
                            SELECT homework_id
                            FROM homework_done_marks
                            WHERE student_id = %s
                        ) done_homeworks
                        """,
                        (user_id, user_id),
                    )
                    row = await cursor.fetchone()
                    homework_completed = int((row or {}).get("value") or 0)
                    progress.append(
                        _progress_item(
                            achievement_ids["homework_master"],
                            "homework_master",
                            homework_completed,
                            50,
                            "Выполнено ДЗ",
                            "ДЗ",
                            "homework_master" in completed_codes,
                        )
                    )

                if "week_warrior" in achievement_ids:
                    await cursor.execute(
                        """
                        SELECT COUNT(DISTINCT day) AS value
                        FROM (
                            SELECT date AS day
                            FROM grades
                            WHERE student_id = %s
                              AND date >= DATE_SUB(CURDATE(), INTERVAL 6 DAY)
                            UNION
                            SELECT DATE(created_at) AS day
                            FROM homework_submissions
                            WHERE student_id = %s
                              AND created_at >= DATE_SUB(NOW(), INTERVAL 6 DAY)
                            UNION
                            SELECT DATE(marked_at) AS day
                            FROM homework_done_marks
                            WHERE student_id = %s
                              AND marked_at >= DATE_SUB(NOW(), INTERVAL 6 DAY)
                            UNION
                            SELECT DATE(created_at) AS day
                            FROM messages
                            WHERE sender_id = %s
                              AND created_at >= DATE_SUB(NOW(), INTERVAL 6 DAY)
                            UNION
                            SELECT date AS day
                            FROM attendance
                            WHERE student_id = %s
                              AND date >= DATE_SUB(CURDATE(), INTERVAL 6 DAY)
                        ) activity
                        """,
                        (user_id, user_id, user_id, user_id, user_id),
                    )
                    row = await cursor.fetchone()
                    activity_days = int((row or {}).get("value") or 0)
                    progress.append(
                        _progress_item(
                            achievement_ids["week_warrior"],
                            "week_warrior",
                            activity_days,
                            7,
                            "Активных дней",
                            "дней",
                            "week_warrior" in completed_codes,
                        )
                    )

                if "perfect_attendance" in achievement_ids:
                    await cursor.execute(
                        """
                        SELECT
                            COUNT(*) AS total,
                            COALESCE(SUM(CASE WHEN status IN ('present', 'late')
                                THEN 1 ELSE 0 END), 0) AS present_count
                        FROM attendance
                        WHERE student_id = %s
                          AND date >= DATE_FORMAT(CURDATE(), '%%Y-%%m-01')
                        """,
                        (user_id,),
                    )
                    row = await cursor.fetchone() or {}
                    attendance_total = int(row.get("total") or 0)
                    attendance_present = int(row.get("present_count") or 0)
                    attendance_percent = (
                        round((attendance_present / attendance_total) * 100)
                        if attendance_total
                        else 0
                    )
                    progress.append(
                        _progress_item(
                            achievement_ids["perfect_attendance"],
                            "perfect_attendance",
                            attendance_percent,
                            100,
                            "Посещаемость за месяц",
                            "%",
                            "perfect_attendance" in completed_codes,
                        )
                    )

            if role == "teacher" and "politeness" in achievement_ids:
                await cursor.execute(
                    """
                    SELECT COUNT(*) AS value
                    FROM teacher_lesson_feedback
                    WHERE teacher_id = %s AND rating >= 4
                    """,
                    (user_id,),
                )
                row = await cursor.fetchone()
                positive_feedback = int((row or {}).get("value") or 0)
                progress.append(
                    _progress_item(
                        achievement_ids["politeness"],
                        "politeness",
                        positive_feedback,
                        20,
                        "Положительных отзывов",
                        "отзывов",
                        "politeness" in completed_codes,
                    )
                )

            for code in sorted(role_codes & _MANUAL_CODES):
                if code not in achievement_ids:
                    continue
                is_completed = code in completed_codes
                progress.append(
                    _progress_item(
                        achievement_ids[code],
                        code,
                        1 if is_completed else 0,
                        1,
                        (
                            "Назначается администрацией школы"
                            if code == "class_president"
                            else "Выдаётся школой за олимпиадные достижения"
                        ),
                        is_completed=is_completed,
                    )
                )

            newly_completed = [
                item
                for item in progress
                if item["is_completed"] and item["code"] not in completed_codes
            ]
            if newly_completed:
                await cursor.executemany(
                    """
                    INSERT IGNORE INTO user_achievements
                        (user_id, achievement_id, earned_at)
                    VALUES (%s, %s, NOW())
                    """,
                    [
                        (user_id, item["achievement_id"])
                        for item in newly_completed
                    ],
                )
                await conn.commit()
                for item in newly_completed:
                    title = _CATALOG_BY_CODE.get(item["code"], {}).get(
                        "title",
                        item["code"],
                    )
                    await notify_achievement_awarded(
                        user_id,
                        item["achievement_id"],
                        title,
                    )
            return progress
    finally:
        conn.close()


async def evaluate_class_podium(class_id: int) -> None:
    conn = await get_db_connection()
    try:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            rows = await _class_leaderboard(cursor, class_id)
            if not rows:
                return

            await cursor.execute(
                """
                SELECT id, code
                FROM achievements
                WHERE code IN ('first_place', 'second_place', 'third_place')
                """
            )
            achievement_rows = await cursor.fetchall()
            achievement_ids = {row["code"]: int(row["id"]) for row in achievement_rows}
            achievement_titles = {
                int(row["id"]): _CATALOG_BY_CODE.get(row["code"], {}).get(
                    "title",
                    row["code"],
                )
                for row in achievement_rows
            }

            awards: list[tuple[int, int]] = []
            candidate_awards: list[tuple[int, int]] = []
            previous_score = None
            current_rank = 0
            for index, row in enumerate(rows):
                score = float(row.get("total_points") or 0)
                if previous_score is None or score < previous_score:
                    current_rank = index + 1
                    previous_score = score
                code = _PODIUM_CODE_BY_RANK.get(current_rank)
                achievement_id = achievement_ids.get(code or "")
                if achievement_id is not None:
                    candidate_awards.append((int(row["student_id"]), achievement_id))

            if candidate_awards:
                for student_id, achievement_id in candidate_awards:
                    await cursor.execute(
                        """
                        SELECT 1
                        FROM user_achievements
                        WHERE user_id = %s AND achievement_id = %s
                        LIMIT 1
                        """,
                        (student_id, achievement_id),
                    )
                    if not await cursor.fetchone():
                        awards.append((student_id, achievement_id))

            if awards:
                await cursor.executemany(
                    """
                    INSERT IGNORE INTO user_achievements
                        (user_id, achievement_id, earned_at)
                    VALUES (%s, %s, NOW())
                    """,
                    awards,
                )
                await conn.commit()
                for student_id, achievement_id in awards:
                    await notify_achievement_awarded(
                        student_id,
                        achievement_id,
                        achievement_titles.get(achievement_id, "Достижение"),
                    )
    finally:
        conn.close()
