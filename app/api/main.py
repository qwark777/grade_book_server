from fastapi import APIRouter

from app.api import auth, profile, grades, users, messages, timetable, schools, subscriptions, entitlements, lessons, ai_advice, risks, lessons_1c_integration, crm_integrations, user_balance, group_chats, admin_analytics, admin_features, admin_extras, owner_analytics, achievements, academic_periods, parents
from typing import List
from fastapi import HTTPException
from pydantic import BaseModel
import os, datetime
from app.db.connection import get_db_connection

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
api_router.include_router(user_balance.router, tags=["balance"])
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

# Simple achievements listing (static files served from /static)
@api_router.get("/achievements", tags=["achievements"])
async def list_achievements() -> List[dict]:
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id, code, title, image_url, rarity FROM achievements ORDER BY id")
            rows = await cur.fetchall()
            return [{
                "id": str(r["id"]),
                "code": r["code"],
                "title": r["title"],
                "image_url": r["image_url"],
                "rarity": r.get("rarity")
            } for r in rows]
    finally:
        conn.close()


@api_router.post("/achievements/sync", tags=["achievements"])
async def sync_achievements_catalog() -> dict:
    """Scan /achievements directory and upsert files into DB catalog."""
    ach_dir = os.path.join(os.getcwd(), "achievements")
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
                    ON DUPLICATE KEY UPDATE title=VALUES(title), image_url=VALUES(image_url)
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


@api_router.post("/achievements/award", tags=["achievements"])
async def award_achievement(payload: AwardRequest) -> dict:
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            # accept either numeric id or code
            ach_id = None
            if payload.achievement_id.isdigit():
                ach_id = int(payload.achievement_id)
            else:
                await cur.execute("SELECT id FROM achievements WHERE code=%s", (payload.achievement_id,))
                row = await cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="Achievement not found")
                ach_id = row["id"]
            # insert ignore
            await cur.execute(
                """
                INSERT IGNORE INTO user_achievements (user_id, achievement_id) VALUES (%s, %s)
                """,
                (payload.user_id, ach_id)
            )
            await conn.commit()
            return {"status": "ok"}
    finally:
        conn.close()


@api_router.get("/users/{user_id}/achievements", tags=["achievements"])
async def get_user_achievements(user_id: int) -> List[dict]:
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT ua.user_id, ua.earned_at, a.id, a.code, a.title, a.image_url, a.rarity
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
                "image_url": r["image_url"],
                "earned_at": r["earned_at"].isoformat() if r.get("earned_at") else None,
                "rarity": r.get("rarity")
            } for r in rows]
    finally:
        conn.close()


@api_router.post("/rewards/award", tags=["rewards"])
async def award_points_and_achievement(payload: PointsAwardRequest) -> dict:
    """Award points and optionally an achievement to a user"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            # Award points only if points > 0
            if payload.points > 0:
                await cur.execute(
                    """
                    INSERT INTO user_points (user_id, points, reason, created_at)
                    VALUES (%s, %s, %s, NOW())
                    ON DUPLICATE KEY UPDATE points = points + VALUES(points)
                    """,
                    (payload.user_id, payload.points, "Admin award")
                )
            
            # Award achievement if provided
            if payload.achievement_id:
                ach_id = None
                if payload.achievement_id.isdigit():
                    ach_id = int(payload.achievement_id)
                else:
                    await cur.execute("SELECT id FROM achievements WHERE code=%s", (payload.achievement_id,))
                    row = await cur.fetchone()
                    if row:
                        ach_id = row["id"]
                
                if ach_id:
                    await cur.execute(
                        """
                        INSERT IGNORE INTO user_achievements (user_id, achievement_id, earned_at)
                        VALUES (%s, %s, NOW())
                        """,
                        (payload.user_id, ach_id)
                    )
            
            await conn.commit()
            return {"status": "ok", "points_awarded": payload.points}
    finally:
        conn.close()


@api_router.get("/users/{user_id}/points", tags=["rewards"])
async def get_user_points(user_id: int) -> dict:
    """Get total points for a user"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COALESCE(SUM(points), 0) as total_points FROM user_points WHERE user_id = %s",
                (user_id,)
            )
            result = await cur.fetchone()
            return {"user_id": user_id, "total_points": result["total_points"]}
    finally:
        conn.close()


@api_router.get("/users/points/leaderboard", tags=["rewards"])
async def get_points_leaderboard(limit: int = 10) -> List[dict]:
    """Get points leaderboard"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
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
                (limit,)
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

