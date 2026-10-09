from fastapi import HTTPException, Depends, Request
from typing import Optional, Callable, Any
from functools import wraps
from datetime import datetime, timedelta
import aiomysql
import json
from app.core.config import settings
from app.core.security import get_current_user
from app.db.connection import get_db_connection
from app.models.user import User
from app.models.subscription import EntitlementCheck

DEFAULT_SCHOOL_FEATURE_FLAGS = {
    "messages_enabled": True,
    "homeworks_enabled": True,
    "ratings_enabled": True,
    "teacher_feedback_enabled": True,
    "parent_access_enabled": True,
    "attendance_enabled": True,
    "schedule_enabled": True,
    "analytics_enabled": True,
    "notifications_enabled": True,
}


def _subscription_expired(period_end: Any) -> bool:
    if not period_end:
        return True
    if isinstance(period_end, str):
        try:
            period_end = datetime.fromisoformat(period_end)
        except ValueError:
            return True
    grace_until = period_end + timedelta(days=settings.SUBSCRIPTION_GRACE_DAYS)
    return grace_until < datetime.now()


def _past_due_grace_expired(grace_started_at: Any) -> bool:
    if not grace_started_at:
        return True
    if isinstance(grace_started_at, str):
        try:
            grace_started_at = datetime.fromisoformat(grace_started_at)
        except ValueError:
            return True
    return grace_started_at + timedelta(days=settings.SUBSCRIPTION_GRACE_DAYS) < datetime.now()


async def check_entitlement(
    school_id: int, 
    entitlement_key: str, 
    user: User
) -> EntitlementCheck:
    """Check if a school has access to a specific entitlement"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Get subscription and entitlements
            await cursor.execute("""
                SELECT
                    ss.status,
                    ss.current_period_start,
                    ss.current_period_end,
                    ss.updated_at,
                    pe.entitlement_value
                FROM school_subscriptions ss
                LEFT JOIN plan_entitlements pe ON ss.plan_id = pe.plan_id 
                    AND pe.entitlement_key = %s
                WHERE ss.school_id = %s
            """, (entitlement_key, school_id))
            
            result = await cursor.fetchone()
            
            if not result:
                return EntitlementCheck(
                    school_id=school_id,
                    entitlement_key=entitlement_key,
                    has_access=False,
                    reason="No subscription found"
                )
            
            # Check subscription status
            status = result["status"]
            if status in ["suspended", "canceled"]:
                return EntitlementCheck(
                    school_id=school_id,
                    entitlement_key=entitlement_key,
                    has_access=False,
                    reason=f"Subscription is {status}"
                )
            if status == "past_due" and _past_due_grace_expired(
                result.get("current_period_end")
            ):
                return EntitlementCheck(
                    school_id=school_id,
                    entitlement_key=entitlement_key,
                    has_access=False,
                    reason="Subscription grace period has ended"
                )
            if _subscription_expired(result.get("current_period_end")):
                return EntitlementCheck(
                    school_id=school_id,
                    entitlement_key=entitlement_key,
                    has_access=False,
                    reason="Subscription period has ended",
                )
            
            # Check if entitlement exists
            if not result["entitlement_value"]:
                return EntitlementCheck(
                    school_id=school_id,
                    entitlement_key=entitlement_key,
                    has_access=False,
                    reason="Entitlement not included in plan"
                )
            
            # Parse entitlement value
            normalized_value = str(result["entitlement_value"]).strip().lower()
            if normalized_value in {"true", "unlimited"}:
                return EntitlementCheck(
                    school_id=school_id,
                    entitlement_key=entitlement_key,
                    has_access=True,
                    reason="Unlimited entitlement",
                )
            if normalized_value == "false":
                return EntitlementCheck(
                    school_id=school_id,
                    entitlement_key=entitlement_key,
                    has_access=False,
                    reason="Entitlement disabled",
                )
            try:
                limit = float(result["entitlement_value"])
            except ValueError:
                return EntitlementCheck(
                    school_id=school_id,
                    entitlement_key=entitlement_key,
                    has_access=False,
                    reason="Invalid entitlement value",
                )
            
            # Numeric entitlement - check usage
            await cursor.execute("""
                SELECT SUM(event_value) as current_usage
                FROM usage_events 
                WHERE school_id = %s 
                AND event_key = %s
                AND event_date >= (
                    SELECT current_period_start 
                    FROM school_subscriptions 
                    WHERE school_id = %s
                )
            """, (school_id, entitlement_key, school_id))
            
            usage_result = await cursor.fetchone()
            current_usage = usage_result["current_usage"] or 0
            
            has_access = current_usage < limit
            
            return EntitlementCheck(
                school_id=school_id,
                entitlement_key=entitlement_key,
                has_access=has_access,
                current_usage=current_usage,
                limit=limit,
                reason="Usage limit check" if not has_access else "Within limit"
            )
    finally:
        conn.close()


# Admin permission keys (what regular admins can do)
ADMIN_PERMISSIONS = [
    "edit_school", "edit_classes", "edit_teachers", "edit_students",
    "edit_subjects", "edit_schedule", "edit_finances", "edit_academic_periods",
    "edit_holidays", "manage_admins",
]


async def get_admin_info(user: User) -> tuple[Optional[int], bool, list[str]]:
    """
    Returns (school_id, is_main_admin, permissions).
    Owner: (None, True, all). Main admin: (school_id, True, all). Regular: (school_id, False, [...])
    """
    if user.role == "owner" or user.role in ("superadmin", "root"):
        return (None, True, ADMIN_PERMISSIONS.copy())

    if user.role != "admin":
        return (None, False, [])

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT school_id, is_main_admin FROM school_admins WHERE admin_user_id = %s",
                (user.id,)
            )
            row = await cursor.fetchone()
            if not row:
                return (None, False, [])

            school_id = row.get("school_id") if isinstance(row, dict) else row[0]
            is_main = bool(row.get("is_main_admin") if isinstance(row, dict) else row[1])

            if is_main:
                return (school_id, True, ADMIN_PERMISSIONS.copy())

            await cursor.execute(
                "SELECT permission_key FROM admin_permissions WHERE school_id = %s AND admin_user_id = %s",
                (school_id, user.id)
            )
            perms = [r.get("permission_key") if isinstance(r, dict) else r[0] for r in await cursor.fetchall()]
            return (school_id, False, perms)
    finally:
        conn.close()


async def has_admin_permission(user: User, school_id: int, permission_key: str) -> bool:
    """Check if user (admin) has permission for the given school."""
    user_school_id, is_main, perms = await get_admin_info(user)
    if user_school_id is not None and user_school_id != school_id:
        return False  # Admin is for a different school
    if is_main:
        return True
    return permission_key in perms


async def get_school_id_for_user(user: User) -> Optional[int]:
    """Get school_id for the current user based on their role"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            if user.role in ("owner", "superadmin", "root"):
                # Owner can access any school
                return None
            await cursor.execute(
                "SELECT school_id FROM school_users WHERE user_id = %s LIMIT 1",
                (user.id,),
            )
            direct_membership = await cursor.fetchone()
            if direct_membership:
                return (
                    direct_membership.get("school_id")
                    if isinstance(direct_membership, dict)
                    else direct_membership[0]
                )
            if user.role == "admin":
                # Admin is linked to a specific school
                await cursor.execute(
                    "SELECT school_id FROM school_admins WHERE admin_user_id = %s",
                    (user.id,)
                )
                result = await cursor.fetchone()
                return result["school_id"] if result else None
            elif user.role == "parent":
                await cursor.execute("""
                    SELECT DISTINCT c.school_id
                    FROM parent_students ps
                    JOIN class_students cs ON cs.student_id = ps.student_id
                    JOIN classes c ON c.id = cs.class_id
                    WHERE ps.parent_id = %s AND c.school_id IS NOT NULL
                    LIMIT 1
                """, (user.id,))
                result = await cursor.fetchone()
                return result["school_id"] if result and result.get("school_id") else None
            else:
                # Students/teachers get school from their classes
                # Check if school_id column exists in classes table
                await cursor.execute("""
                    SELECT COUNT(*) as col_count
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                    AND TABLE_NAME = 'classes'
                    AND COLUMN_NAME = 'school_id'
                """)
                col_check = await cursor.fetchone()
                
                if col_check and col_check.get("col_count", 0) > 0:
                    # Column exists, use it
                    await cursor.execute("""
                        SELECT DISTINCT c.school_id 
                        FROM classes c
                        JOIN class_students cs ON c.id = cs.class_id
                            WHERE cs.student_id = %s AND c.school_id IS NOT NULL
                        UNION
                        SELECT DISTINCT c.school_id 
                        FROM classes c
                        JOIN class_teachers ct ON c.id = ct.class_id
                            WHERE ct.teacher_id = %s AND c.school_id IS NOT NULL
                            LIMIT 1
                    """, (user.id, user.id))
                    result = await cursor.fetchone()
                    return result["school_id"] if result and result.get("school_id") else None
                else:
                    # Column doesn't exist, return None
                    return None
    finally:
        conn.close()


async def resolve_school_id_for_user(
    user: User,
    requested_school_id: Optional[int] = None,
) -> int:
    """Resolve one school and prevent regular users selecting another school."""
    role = (
        user.get("role", "") if isinstance(user, dict)
        else getattr(user, "role", "")
    )
    if role in ("owner", "superadmin", "root"):
        conn = await get_db_connection()
        try:
            async with conn.cursor() as cursor:
                if requested_school_id is not None:
                    await cursor.execute(
                        "SELECT id FROM schools WHERE id = %s",
                        (requested_school_id,),
                    )
                    if not await cursor.fetchone():
                        raise HTTPException(404, "Школа не найдена")
                    return int(requested_school_id)
                await cursor.execute("SELECT id FROM schools ORDER BY id LIMIT 2")
                rows = await cursor.fetchall()
        finally:
            conn.close()
        if len(rows) == 1:
            row = rows[0]
            return int(row["id"] if isinstance(row, dict) else row[0])
        raise HTTPException(400, "Укажите school_id")

    school_id = await get_school_id_for_user(user)
    if school_id is None:
        raise HTTPException(403, "Пользователь не привязан к школе")
    if (
        requested_school_id is not None
        and int(requested_school_id) != int(school_id)
    ):
        raise HTTPException(403, "Нет доступа к этой школе")
    return int(school_id)


async def get_school_feature_flags(
    user: User,
    school_id: Optional[int] = None,
) -> dict[str, bool]:
    resolved_school_id = school_id
    if resolved_school_id is None:
        resolved_school_id = await get_school_id_for_user(user)
    if resolved_school_id is None:
        return dict(DEFAULT_SCHOOL_FEATURE_FLAGS)

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT feature_flags FROM school_settings WHERE school_id = %s",
                (resolved_school_id,),
            )
            row = await cursor.fetchone()
            raw = row.get("feature_flags") if row and isinstance(row, dict) else None
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except Exception:
                    raw = {}
            if not isinstance(raw, dict):
                raw = {}
            merged = dict(DEFAULT_SCHOOL_FEATURE_FLAGS)
            for key in merged.keys():
                if key in raw:
                    merged[key] = bool(raw[key])
            return merged
    finally:
        conn.close()


async def require_school_feature(
    user: User,
    feature_key: str,
    school_id: Optional[int] = None,
    detail: Optional[str] = None,
) -> None:
    flags = await get_school_feature_flags(user, school_id=school_id)
    if not flags.get(feature_key, True):
        raise HTTPException(
            status_code=403,
            detail=detail or "Модуль отключен в настройках школы",
        )


def require_entitlement(entitlement_key: str, school_id_param: str = "school_id"):
    """Decorator to require a specific entitlement for an endpoint"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Get current user
            user = kwargs.get('current_user')
            if not user:
                raise HTTPException(status_code=401, detail="Authentication required")
            
            # Get school_id from kwargs or request
            school_id = kwargs.get(school_id_param)
            if not school_id:
                # Try to get from request if available
                for arg in args:
                    if hasattr(arg, 'path_params') and school_id_param in arg.path_params:
                        school_id = int(arg.path_params[school_id_param])
                        break
            
            if not school_id:
                raise HTTPException(status_code=400, detail=f"Missing {school_id_param}")
            
            # Check entitlement
            entitlement_check = await check_entitlement(school_id, entitlement_key, user)
            
            if not entitlement_check.has_access:
                raise HTTPException(
                    status_code=402,  # Payment Required
                    detail={
                        "error": "Entitlement required",
                        "entitlement": entitlement_key,
                        "reason": entitlement_check.reason,
                        "current_usage": entitlement_check.current_usage,
                        "limit": entitlement_check.limit
                    }
                )
            
            # Record usage if it's a usage-based entitlement
            if entitlement_check.limit is not None:
                await record_usage_event(school_id, entitlement_key, 1.0, user.id)
            
            return await func(*args, **kwargs)
        return wrapper
    return decorator


async def record_usage_event(
    school_id: int, 
    event_key: str, 
    event_value: float = 1.0, 
    user_id: Optional[int] = None
):
    """Record a usage event for a school"""
    from datetime import datetime
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                INSERT INTO usage_events 
                (school_id, user_id, event_key, event_value, event_date)
                VALUES (%s, %s, %s, %s, %s)
            """, (school_id, user_id, event_key, event_value, datetime.now().date()))
            
            await conn.commit()
    finally:
        conn.close()


class EntitlementGuard:
    """Class-based entitlement guard for dependency injection"""
    
    def __init__(self, entitlement_key: str, school_id_param: str = "school_id"):
        self.entitlement_key = entitlement_key
        self.school_id_param = school_id_param
    
    async def __call__(
        self, 
        request: Request,
        current_user: User = Depends(get_current_user)
    ) -> EntitlementCheck:
        # Get school_id from path parameters
        school_id = request.path_params.get(self.school_id_param)
        if not school_id:
            raise HTTPException(status_code=400, detail=f"Missing {self.school_id_param}")
        
        school_id = int(school_id)
        
        # Check entitlement
        entitlement_check = await check_entitlement(school_id, self.entitlement_key, current_user)
        
        if not entitlement_check.has_access:
            raise HTTPException(
                status_code=402,  # Payment Required
                detail={
                    "error": "Entitlement required",
                    "entitlement": self.entitlement_key,
                    "reason": entitlement_check.reason,
                    "current_usage": entitlement_check.current_usage,
                    "limit": entitlement_check.limit
                }
            )
        
        # Record usage if it's a usage-based entitlement
        if entitlement_check.limit is not None:
            await record_usage_event(school_id, self.entitlement_key, 1.0, current_user.id)
        
        return entitlement_check


# Common entitlement guards
require_analytics = EntitlementGuard("analytics.full")
require_ai_features = EntitlementGuard("ai.quota")
require_csv_export = EntitlementGuard("exports.csv")
require_unlimited_classes = EntitlementGuard("classes.unlimited")
require_advanced_roles = EntitlementGuard("roles.advanced")
