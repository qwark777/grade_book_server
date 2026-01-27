from fastapi import HTTPException, Depends, Request
from typing import Optional, Callable, Any
from functools import wraps
import aiomysql
from app.core.security import get_current_user
from app.db.connection import get_db_connection
from app.models.user import User
from app.models.subscription import EntitlementCheck


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
                SELECT ss.status, pe.entitlement_value
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
            if result["status"] in ["suspended", "canceled"]:
                return EntitlementCheck(
                    school_id=school_id,
                    entitlement_key=entitlement_key,
                    has_access=False,
                    reason=f"Subscription is {result['status']}"
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
            try:
                limit = float(result["entitlement_value"])
            except ValueError:
                # Boolean entitlement (e.g., "true", "false")
                has_access = result["entitlement_value"].lower() == "true"
                return EntitlementCheck(
                    school_id=school_id,
                    entitlement_key=entitlement_key,
                    has_access=has_access,
                    reason="Boolean entitlement"
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


async def get_school_id_for_user(user: User) -> Optional[int]:
    """Get school_id for the current user based on their role"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            if user.role == "owner":
                # Owner can access any school
                return None
            elif user.role == "admin":
                # Admin is linked to a specific school
                await cursor.execute(
                    "SELECT school_id FROM school_admins WHERE admin_user_id = %s",
                    (user.id,)
                )
                result = await cursor.fetchone()
                return result["school_id"] if result else None
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
