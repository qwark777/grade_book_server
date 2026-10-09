from fastapi import APIRouter

from app.api import auth, profile, grades, users, messages, timetable, schools, subscriptions, entitlements, lessons, ai_advice, risks, lessons_1c_integration, crm_integrations, group_chats, admin_analytics, admin_features, admin_extras, owner_analytics, achievements, academic_periods, parents, teacher_feedback, notifications, health, announcements, invites, landing
from typing import List
from fastapi import Depends, HTTPException
from pydantic import BaseModel
import os, datetime
from app.core.config import settings
from app.core.entitlements import get_school_id_for_user
from app.core.security import get_current_user
from app.db.connection import get_db_connection
from app.services.achievement_service import (
    achievement_catalog_metadata,
    can_manually_award_achievement,
    notify_achievement_awarded,
)
from app.services.notifications import notify_many

# Create main API router
api_router = APIRouter()

# Include all route modules
api_router.include_router(auth.router, tags=["authentication"])
api_router.include_router(profile.router, tags=["profile"])
api_router.include_router(grades.router, tags=["grades"])
api_router.include_router(users.router, tags=["users"])
api_router.include_router(messages.router, tags=["messages"])
api_router.include_router(timetable.router, tags=["timetable"])
api_router.include_router(schools.router, tags=["schools"])
api_router.include_router(subscriptions.router, prefix="/subscriptions", tags=["subscriptions"])
api_router.include_router(entitlements.router, prefix="/entitlements", tags=["entitlements"])
api_router.include_router(lessons.router, tags=["lessons"])
api_router.include_router(lessons_1c_integration.router, prefix="/lessons", tags=["lessons-1c"])
api_router.include_router(crm_integrations.router, tags=["crm-integrations"])
api_router.include_router(group_chats.router, tags=["group-chats"])
api_router.include_router(admin_analytics.router, tags=["admin-analytics"])
api_router.include_router(admin_features.router, tags=["admin-features"])
api_router.include_router(admin_extras.router, tags=["admin-extras"])
api_router.include_router(owner_analytics.router, tags=["owner-analytics"])
api_router.include_router(ai_advice.router, tags=["ai-advice"])
api_router.include_router(risks.router, tags=["risks"])
api_router.include_router(achievements.router, tags=["achievements"])
api_router.include_router(academic_periods.router, tags=["academic-periods"])
api_router.include_router(parents.router, prefix="/parents", tags=["parents"])
api_router.include_router(invites.router, prefix="/invites", tags=["invites"])
api_router.include_router(teacher_feedback.router, tags=["teacher-feedback"])
api_router.include_router(notifications.router, tags=["notifications"])
api_router.include_router(health.router, tags=["health"])
api_router.include_router(announcements.router, tags=["announcements"])
api_router.include_router(landing.router, tags=["landing"])

# Simple achievements listing (static files served from /static)
@api_router.get("/achievements", tags=["achievements"])
async def list_achievements() -> List[dict]:
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'achievements'
                  AND COLUMN_NAME = 'description'
            """)
            has_description = await cur.fetchone() is not None
            description_select = "description" if has_description else "NULL AS description"
            await cur.execute(f"SELECT id, code, title, {description_select}, image_url, rarity FROM achievements ORDER BY id")
            rows = await cur.fetchall()
            return [
                {
                    "id": str(r["id"]),
                    "code": r["code"],
                    "title": r["title"],
                    "description": r.get("description"),
                    "image_url": r["image_url"],
                    "rarity": r.get("rarity"),
                    **achievement_catalog_metadata(r["code"]),
                }
                for r in rows
            ]
    finally:
        conn.close()


@api_router.post("/achievements/sync", tags=["achievements"])
async def sync_achievements_catalog(current_user=Depends(get_current_user)) -> dict:
    """Scan /achievements directory and upsert files into DB catalog."""
    _require_owner(current_user)
    ach_dir = settings.ACHIEVEMENTS_DIR
    if not os.path.isdir(ach_dir):
        return {"inserted": 0}

    files = [f for f in os.listdir(ach_dir) if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))]
    if not files:
        return {"inserted": 0}

    conn = await get_db_connection()
    inserted = 0
    try:
        async with conn.cursor() as cur:
            for fname in files:
                code = os.path.splitext(fname)[0]
                title = code.replace("_", " ").title()
                image_url = f"/static/achievements/{fname}"
                await cur.execute(
                    """
                    INSERT INTO achievements (code, title, image_url)
                    VALUES (%s, %s, %s)
                    AS incoming
                    ON DUPLICATE KEY UPDATE
                        title = incoming.title,
                        image_url = incoming.image_url
                    """,
                    (code, title, image_url)
                )
                inserted += cur.rowcount
            await conn.commit()
        return {"inserted": max(0, inserted)}
    finally:
        conn.close()


# ==== User Achievements persistence (simple JSON store) ====
class AwardRequest(BaseModel):
    user_id: int
    achievement_id: str

class PointsAwardRequest(BaseModel):
    user_id: int
    points: int
    achievement_id: str = None


def _role(current_user) -> str:
    return current_user.get("role", "") if isinstance(current_user, dict) else getattr(current_user, "role", "")


def _user_id(current_user) -> int:
    return current_user.get("id") if isinstance(current_user, dict) else getattr(current_user, "id", None)


def _require_admin(current_user) -> None:
    if _role(current_user) not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")


def _require_owner(current_user) -> None:
    if _role(current_user) not in ("owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")


async def _ensure_user_in_admin_scope(cur, current_user, target_user_id: int) -> None:
    role = _role(current_user)
    if role in ("owner", "superadmin", "root"):
        return

    school_id = await get_school_id_for_user(current_user)
    if school_id is None:
        raise HTTPException(status_code=403, detail="No school access")

    await cur.execute(
        """
        SELECT 1
        FROM users u
        LEFT JOIN school_admins sa ON sa.admin_user_id = u.id
        LEFT JOIN class_students cs ON cs.student_id = u.id
        LEFT JOIN classes c_student ON c_student.id = cs.class_id
        LEFT JOIN class_teachers ct ON ct.teacher_id = u.id
        LEFT JOIN classes c_teacher ON c_teacher.id = ct.class_id
        LEFT JOIN class_subject_teachers cst ON cst.teacher_id = u.id
        LEFT JOIN classes c_subject_teacher ON c_subject_teacher.id = cst.class_id
        WHERE u.id = %s
          AND (
              sa.school_id = %s
              OR c_student.school_id = %s
              OR c_teacher.school_id = %s
              OR c_subject_teacher.school_id = %s
          )
        LIMIT 1
        """,
        (target_user_id, school_id, school_id, school_id, school_id),
    )
    if not await cur.fetchone():
        raise HTTPException(status_code=403, detail="Access denied")


async def _resolve_manual_achievement(
    cur,
    achievement_reference: str,
    target_user_id: int,
) -> int:
    if achievement_reference.isdigit():
        await cur.execute(
            "SELECT id, code FROM achievements WHERE id = %s",
            (int(achievement_reference),),
        )
    else:
        await cur.execute(
            "SELECT id, code FROM achievements WHERE code = %s",
            (achievement_reference,),
        )
    achievement = await cur.fetchone()
    if not achievement:
        raise HTTPException(status_code=404, detail="Achievement not found")

    await cur.execute("SELECT role FROM users WHERE id = %s", (target_user_id,))
    target_user = await cur.fetchone()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    if not can_manually_award_achievement(
        achievement["code"],
        target_user["role"],
    ):
        raise HTTPException(
            status_code=400,
            detail="This achievement is awarded automatically or is not available for this role",
        )
    return int(achievement["id"])


async def _achievement_title(cur, achievement_id: int) -> str:
    await cur.execute("SELECT title, code FROM achievements WHERE id = %s", (achievement_id,))
    row = await cur.fetchone()
    if not row:
        return "Достижение"
    return row.get("title") or row.get("code") or "Достижение"


@api_router.post("/achievements/award", tags=["achievements"])
async def award_achievement(
    payload: AwardRequest,
    current_user=Depends(get_current_user),
) -> dict:
    _require_admin(current_user)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await _ensure_user_in_admin_scope(cur, current_user, payload.user_id)
            ach_id = await _resolve_manual_achievement(
                cur,
                payload.achievement_id,
                payload.user_id,
            )
            # insert ignore
            await cur.execute(
                """
                INSERT IGNORE INTO user_achievements (user_id, achievement_id) VALUES (%s, %s)
                """,
                (payload.user_id, ach_id)
            )
            inserted = cur.rowcount > 0
            achievement_title = await _achievement_title(cur, ach_id) if inserted else None
            await conn.commit()
            if inserted:
                await notify_achievement_awarded(
                    payload.user_id,
                    ach_id,
                    achievement_title or "Достижение",
                )
            return {"status": "ok"}
    finally:
        conn.close()


@api_router.get("/users/{user_id}/achievements", tags=["achievements"])
async def get_user_achievements(
    user_id: int,
    current_user=Depends(get_current_user),
) -> List[dict]:
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            if _user_id(current_user) != user_id:
                _require_admin(current_user)
                await _ensure_user_in_admin_scope(cur, current_user, user_id)
            await cur.execute(
                """
                SELECT ua.user_id, ua.earned_at, a.id, a.code, a.title,
                       a.description, a.image_url, a.rarity
                FROM user_achievements ua
                JOIN achievements a ON a.id = ua.achievement_id
                WHERE ua.user_id = %s
                ORDER BY ua.earned_at DESC
                """,
                (user_id,)
            )
            rows = await cur.fetchall()
            return [{
                "id": str(r["id"]),
                "code": r["code"],
                "title": r["title"],
                "description": r.get("description") or "",
                "image_url": r["image_url"],
                "earned_at": r["earned_at"].isoformat() if r.get("earned_at") else None,
                "rarity": r.get("rarity")
            } for r in rows]
    finally:
        conn.close()


@api_router.post("/rewards/award", tags=["rewards"])
async def award_points_and_achievement(
    payload: PointsAwardRequest,
    current_user=Depends(get_current_user),
) -> dict:
    """Award points and optionally an achievement to a user"""
    _require_admin(current_user)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await _ensure_user_in_admin_scope(cur, current_user, payload.user_id)
            achievement_inserted = False
            achievement_title = None
            ach_id = None
            # Award points only if points > 0
            if payload.points > 0:
                await cur.execute(
                    """
                    INSERT INTO user_points (user_id, points, reason, created_at)
                    VALUES (%s, %s, %s, NOW())
                    AS incoming
                    ON DUPLICATE KEY UPDATE
                        points = user_points.points + incoming.points
                    """,
                    (payload.user_id, payload.points, "Admin award")
                )
            
            # Award achievement if provided
            if payload.achievement_id:
                ach_id = await _resolve_manual_achievement(
                    cur,
                    payload.achievement_id,
                    payload.user_id,
                )
                await cur.execute(
                    """
                    INSERT IGNORE INTO user_achievements (user_id, achievement_id, earned_at)
                    VALUES (%s, %s, NOW())
                    """,
                    (payload.user_id, ach_id)
                )
                achievement_inserted = cur.rowcount > 0
                achievement_title = (
                    await _achievement_title(cur, ach_id)
                    if achievement_inserted
                    else None
                )
            
            await conn.commit()
            if payload.points > 0:
                await notify_many(
                    [payload.user_id],
                    "Награда начислена",
                    f"Вам начислено {payload.points} баллов.",
                    notification_type="reward",
                    entity_type="reward",
                    route_name="achievements",
                )
            if payload.achievement_id and achievement_inserted:
                await notify_achievement_awarded(
                    payload.user_id,
                    ach_id or 0,
                    achievement_title or "Достижение",
                )
            return {"status": "ok", "points_awarded": payload.points}
    finally:
        conn.close()


@api_router.get("/users/{user_id}/points", tags=["rewards"])
async def get_user_points(
    user_id: int,
    current_user=Depends(get_current_user),
) -> dict:
    """Get total points for a user"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            if _user_id(current_user) != user_id:
                _require_admin(current_user)
                await _ensure_user_in_admin_scope(cur, current_user, user_id)
            await cur.execute(
                "SELECT COALESCE(SUM(points), 0) as total_points FROM user_points WHERE user_id = %s",
                (user_id,)
            )
            result = await cur.fetchone()
            return {"user_id": user_id, "total_points": result["total_points"]}
    finally:
        conn.close()


@api_router.get("/users/points/leaderboard", tags=["rewards"])
async def get_points_leaderboard(
    limit: int = 10,
    current_user=Depends(get_current_user),
) -> List[dict]:
    """Get points leaderboard"""
    _require_admin(current_user)
    role = _role(current_user)
    school_id = await get_school_id_for_user(current_user)
    if role not in ("owner", "superadmin", "root") and school_id is None:
        raise HTTPException(status_code=403, detail="No school access")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            if role in ("owner", "superadmin", "root"):
                await cur.execute(
                    """
                    SELECT
                        u.id,
                        p.full_name,
                        u.role,
                        COALESCE(SUM(up.points), 0) as total_points
                    FROM users u
                    LEFT JOIN profiles p ON u.id = p.user_id
                    LEFT JOIN user_points up ON u.id = up.user_id
                    GROUP BY u.id, p.full_name, u.role
                    ORDER BY total_points DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            else:
                await cur.execute(
                    """
                    SELECT
                        u.id,
                        p.full_name,
                        u.role,
                        COALESCE(SUM(up.points), 0) as total_points
                    FROM users u
                    LEFT JOIN profiles p ON u.id = p.user_id
                    LEFT JOIN user_points up ON u.id = up.user_id
                    WHERE EXISTS (
                        SELECT 1
                        FROM school_admins sa
                        WHERE sa.admin_user_id = u.id AND sa.school_id = %s
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM class_students cs
                        JOIN classes c ON c.id = cs.class_id
                        WHERE cs.student_id = u.id AND c.school_id = %s
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM class_teachers ct
                        JOIN classes c ON c.id = ct.class_id
                        WHERE ct.teacher_id = u.id AND c.school_id = %s
                    )
                    GROUP BY u.id, p.full_name, u.role
                    ORDER BY total_points DESC
                    LIMIT %s
                    """,
                    (school_id, school_id, school_id, limit),
                )
            rows = await cur.fetchall()
            return [{
                "user_id": r["id"],
                "full_name": r["full_name"],
                "role": r["role"],
                "total_points": r["total_points"]
            } for r in rows]
    finally:
        conn.close()
