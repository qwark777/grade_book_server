from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from datetime import datetime, timedelta
import aiomysql
from app.core.security import get_current_user
from app.db.connection import get_db_connection
from app.models.subscription import (
    SubscriptionPlan, PlanEntitlement, SchoolSubscription, UserSubscription,
    SubscriptionWithPlan, UsageStats, EntitlementCheck,
    SubscriptionStatus, InvoiceStatus
)
from app.models.user import User
from pydantic import BaseModel

router = APIRouter()


async def get_school_id_for_user(user: User, conn) -> Optional[int]:
    """Get school_id for the current user based on their role"""
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


@router.get("/plans", response_model=List[SubscriptionPlan])
async def get_subscription_plans():
    """Get all available subscription plans"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT * FROM subscription_plans 
                WHERE is_active = TRUE 
                ORDER BY price_monthly ASC
            """)
            plans = await cursor.fetchall()
            return [SubscriptionPlan(**plan) for plan in plans]
    finally:
        conn.close()


@router.get("/plans/{plan_id}", response_model=SubscriptionPlan)
async def get_subscription_plan(plan_id: int):
    """Get a specific subscription plan"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT * FROM subscription_plans WHERE id = %s",
                (plan_id,)
            )
            plan = await cursor.fetchone()
            if not plan:
                raise HTTPException(status_code=404, detail="Plan not found")
            return SubscriptionPlan(**plan)
    finally:
        conn.close()


@router.get("/plans/{plan_id}/entitlements", response_model=List[PlanEntitlement])
async def get_plan_entitlements(plan_id: int):
    """Get entitlements for a specific plan"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT * FROM plan_entitlements WHERE plan_id = %s",
                (plan_id,)
            )
            entitlements = await cursor.fetchall()
            return [PlanEntitlement(**ent) for ent in entitlements]
    finally:
        conn.close()


@router.get("/schools/{school_id}/subscription", response_model=SubscriptionWithPlan)
async def get_school_subscription(
    school_id: int,
    current_user: User = Depends(get_current_user)
):
    """Get subscription for a specific school"""
    conn = await get_db_connection()
    try:
        # Check if user has access to this school
        user_school_id = await get_school_id_for_user(current_user, conn)
        if current_user.role != "owner" and user_school_id != school_id:
            raise HTTPException(status_code=403, detail="Access denied")

        async with conn.cursor() as cursor:
            # Get subscription
            await cursor.execute("""
                SELECT ss.* FROM school_subscriptions ss
                WHERE ss.school_id = %s
            """, (school_id,))
            subscription = await cursor.fetchone()
            
            if not subscription:
                raise HTTPException(status_code=404, detail="No subscription found")

            # Get plan
            await cursor.execute(
                "SELECT * FROM subscription_plans WHERE id = %s",
                (subscription["plan_id"],)
            )
            plan = await cursor.fetchone()

            # Get entitlements
            await cursor.execute(
                "SELECT * FROM plan_entitlements WHERE plan_id = %s",
                (subscription["plan_id"],)
            )
            entitlements = await cursor.fetchall()

            return SubscriptionWithPlan(
                subscription=SchoolSubscription(**subscription),
                plan=SubscriptionPlan(**plan),
                entitlements=[PlanEntitlement(**ent) for ent in entitlements]
            )
    finally:
        conn.close()


@router.post("/schools/{school_id}/subscription")
async def create_school_subscription(
    school_id: int,
    plan_id: int,
    current_user: User = Depends(get_current_user)
):
    """Create or update subscription for a school (owner only)"""
    if current_user.role != "owner":
        raise HTTPException(status_code=403, detail="Only owners can create subscriptions")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Check if school exists
            await cursor.execute("SELECT id FROM schools WHERE id = %s", (school_id,))
            if not await cursor.fetchone():
                raise HTTPException(status_code=404, detail="School not found")

            # Check if plan exists
            await cursor.execute("SELECT id FROM subscription_plans WHERE id = %s", (plan_id,))
            if not await cursor.fetchone():
                raise HTTPException(status_code=404, detail="Plan not found")

            # Calculate period end (30 days from now)
            period_start = datetime.now()
            period_end = period_start + timedelta(days=30)

            # Insert or update subscription
            await cursor.execute("""
                INSERT INTO school_subscriptions 
                (school_id, plan_id, status, current_period_start, current_period_end)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                plan_id = VALUES(plan_id),
                status = VALUES(status),
                current_period_start = VALUES(current_period_start),
                current_period_end = VALUES(current_period_end),
                updated_at = CURRENT_TIMESTAMP
            """, (school_id, plan_id, SubscriptionStatus.TRIAL, period_start, period_end))

            await conn.commit()
            return {"message": "Subscription created successfully"}
    finally:
        conn.close()


@router.get("/schools/{school_id}/usage", response_model=List[UsageStats])
async def get_school_usage(
    school_id: int,
    current_user: User = Depends(get_current_user)
):
    """Get usage statistics for a school"""
    conn = await get_db_connection()
    try:
        # Check if user has access to this school
        user_school_id = await get_school_id_for_user(current_user, conn)
        if current_user.role != "owner" and user_school_id != school_id:
            raise HTTPException(status_code=403, detail="Access denied")

        async with conn.cursor() as cursor:
            # Get current period
            await cursor.execute("""
                SELECT current_period_start, current_period_end 
                FROM school_subscriptions 
                WHERE school_id = %s
            """, (school_id,))
            subscription = await cursor.fetchone()
            
            if not subscription:
                raise HTTPException(status_code=404, detail="No subscription found")

            period_start = subscription["current_period_start"]
            period_end = subscription["current_period_end"]

            # Get usage stats
            await cursor.execute("""
                SELECT 
                    event_key,
                    SUM(event_value) as current_usage
                FROM usage_events 
                WHERE school_id = %s 
                AND event_date >= %s 
                AND event_date <= %s
                GROUP BY event_key
            """, (school_id, period_start, period_end))
            
            usage_data = await cursor.fetchall()
            
            # Get limits from entitlements
            await cursor.execute("""
                SELECT pe.entitlement_key, pe.entitlement_value
                FROM plan_entitlements pe
                JOIN school_subscriptions ss ON pe.plan_id = ss.plan_id
                WHERE ss.school_id = %s
            """, (school_id,))
            entitlements = await cursor.fetchall()
            
            # Create usage stats
            stats = []
            for usage in usage_data:
                limit = None
                for ent in entitlements:
                    if ent["entitlement_key"] == usage["event_key"]:
                        try:
                            limit = float(ent["entitlement_value"])
                        except ValueError:
                            pass
                        break
                
                stats.append(UsageStats(
                    school_id=school_id,
                    event_key=usage["event_key"],
                    current_usage=usage["current_usage"],
                    limit=limit,
                    period_start=period_start,
                    period_end=period_end
                ))
            
            return stats
    finally:
        conn.close()


@router.post("/schools/{school_id}/usage")
async def record_usage_event(
    school_id: int,
    event_key: str,
    event_value: float = 1.0,
    user_id: Optional[int] = None,
    current_user: User = Depends(get_current_user)
):
    """Record a usage event for a school"""
    conn = await get_db_connection()
    try:
        # Check if user has access to this school
        user_school_id = await get_school_id_for_user(current_user, conn)
        if current_user.role != "owner" and user_school_id != school_id:
            raise HTTPException(status_code=403, detail="Access denied")

        async with conn.cursor() as cursor:
            await cursor.execute("""
                INSERT INTO usage_events 
                (school_id, user_id, event_key, event_value, event_date)
                VALUES (%s, %s, %s, %s, %s)
            """, (school_id, user_id, event_key, event_value, datetime.now().date()))
            
            await conn.commit()
            return {"message": "Usage event recorded"}
    finally:
        conn.close()


@router.get("/schools/{school_id}/entitlements/check", response_model=EntitlementCheck)
async def check_entitlement(
    school_id: int,
    entitlement_key: str,
    current_user: User = Depends(get_current_user)
):
    """Check if a school has access to a specific entitlement"""
    conn = await get_db_connection()
    try:
        # Check if user has access to this school
        user_school_id = await get_school_id_for_user(current_user, conn)
        if current_user.role != "owner" and user_school_id != school_id:
            raise HTTPException(status_code=403, detail="Access denied")

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


@router.get("/schools/{school_id}/invoices")
async def get_school_invoices(
    school_id: int,
    current_user: User = Depends(get_current_user)
):
    """Get invoices for a school"""
    conn = await get_db_connection()
    try:
        # Check if user has access to this school
        user_school_id = await get_school_id_for_user(current_user, conn)
        if current_user.role != "owner" and user_school_id != school_id:
            raise HTTPException(status_code=403, detail="Access denied")

        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT si.*, sp.name as plan_name
                FROM subscription_invoices si
                JOIN subscription_plans sp ON si.plan_id = sp.id
                WHERE si.school_id = %s
                ORDER BY si.created_at DESC
            """, (school_id,))
            
            invoices = await cursor.fetchall()
            return invoices
    finally:
        conn.close()


class UserSubscriptionWithPlan(BaseModel):
    """Подписка пользователя с информацией о плане"""
    subscription: UserSubscription
    plan: SubscriptionPlan


@router.get("/users/me/subscription", response_model=UserSubscriptionWithPlan)
async def get_my_subscription(current_user: User = Depends(get_current_user)):
    """Получить подписку текущего пользователя"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Получаем подписку пользователя
            await cursor.execute("""
                SELECT * FROM user_subscriptions 
                WHERE user_id = %s
            """, (current_user.id,))
            subscription_row = await cursor.fetchone()
            
            if not subscription_row:
                raise HTTPException(status_code=404, detail="Subscription not found")
            
            # Получаем план подписки
            await cursor.execute("""
                SELECT * FROM subscription_plans 
                WHERE id = %s
            """, (subscription_row["plan_id"],))
            plan_row = await cursor.fetchone()
            
            if not plan_row:
                raise HTTPException(status_code=404, detail="Plan not found")
            
            return UserSubscriptionWithPlan(
                subscription=UserSubscription(**subscription_row),
                plan=SubscriptionPlan(**plan_row)
            )
    finally:
        conn.close()
