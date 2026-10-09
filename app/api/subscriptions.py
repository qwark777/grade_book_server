from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, status
from typing import List, Optional
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import hmac
import json
import math
import secrets
import time
import aiomysql
from app.core.config import settings
from app.core.security import get_current_user
from app.core.subscription_limits import get_school_resource_limit
from app.db.connection import get_db_connection
from app.models.subscription import (
    SubscriptionPlan, PlanEntitlement, SchoolSubscription,
    SubscriptionWithPlan, UsageStats, EntitlementCheck,
    SubscriptionStatus, InvoiceStatus
)
from app.models.user import User
from pydantic import BaseModel

router = APIRouter()


PROVIDER_PAYMENT_METHODS = {"card_provider", "sbp_provider"}
SUPPORTED_PAYMENT_PROVIDERS = {"generic_hmac"}
APPLICATION_OWNER_ROLES = {"owner", "superadmin", "root"}
SCHOOL_SUBSCRIPTION_VIEWER_ROLES = APPLICATION_OWNER_ROLES | {"admin"}
SUPPORTED_SUBSCRIPTION_DAYS = {30, 90, 365}
INVOICE_STATUS_TRANSITIONS = {
    InvoiceStatus.PENDING.value: {
        InvoiceStatus.PAID.value,
        InvoiceStatus.FAILED.value,
    },
    InvoiceStatus.FAILED.value: {
        InvoiceStatus.PENDING.value,
        InvoiceStatus.PAID.value,
    },
    InvoiceStatus.PAID.value: {InvoiceStatus.REFUNDED.value},
    InvoiceStatus.REFUNDED.value: set(),
}
WEBHOOK_MAX_AGE_SECONDS = 300
WEBHOOK_MAX_BODY_BYTES = 256 * 1024


def _is_application_owner(user: User) -> bool:
    return user.role in APPLICATION_OWNER_ROLES


def _can_view_school_subscription(user: User) -> bool:
    return user.role in SCHOOL_SUBSCRIPTION_VIEWER_ROLES


def _payment_provider_configured() -> bool:
    return (
        settings.PAYMENT_PROVIDER_ENABLED
        and settings.PAYMENT_PROVIDER in SUPPORTED_PAYMENT_PROVIDERS
        and bool(settings.PAYMENT_WEBHOOK_SECRET)
    )


def _row_value(row, key: str, index: int, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    return row[index] if index < len(row) else default


def _as_datetime(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _invoice_amount(plan, days: int) -> Decimal:
    monthly = Decimal(str(_row_value(plan, "price_monthly", 1, 0) or 0))
    yearly = Decimal(str(_row_value(plan, "price_yearly", 2, 0) or 0))
    if days == 365:
        amount = yearly
    elif days == 90:
        amount = monthly * Decimal("3")
    elif days == 30:
        amount = monthly
    else:
        raise HTTPException(
            status_code=400,
            detail="days must be one of: 30, 90, 365",
        )
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _validate_invoice_transition(current_status: str, requested_status: str) -> None:
    if current_status == requested_status:
        return
    if requested_status not in INVOICE_STATUS_TRANSITIONS.get(current_status, set()):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Invalid invoice status transition: "
                f"{current_status} -> {requested_status}"
            ),
        )


async def _apply_paid_invoice(cursor, invoice) -> None:
    school_id = int(_row_value(invoice, "school_id", 1))
    plan_id = int(_row_value(invoice, "plan_id", 2))
    invoice_start = _as_datetime(_row_value(invoice, "period_start", 6))
    invoice_end = _as_datetime(_row_value(invoice, "period_end", 7))
    if invoice_start is None or invoice_end is None:
        raise HTTPException(status_code=409, detail="Invoice period is invalid")

    await cursor.execute(
        """
        SELECT plan_id, status, current_period_start, current_period_end, updated_at
        FROM school_subscriptions
        WHERE school_id = %s
        FOR UPDATE
        """,
        (school_id,),
    )
    subscription = await cursor.fetchone()
    if not subscription:
        await cursor.execute(
            """
            INSERT INTO school_subscriptions
            (school_id, plan_id, status, current_period_start, current_period_end)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                school_id,
                plan_id,
                SubscriptionStatus.ACTIVE.value,
                invoice_start,
                invoice_end,
            ),
        )
        return

    current_plan_id = int(_row_value(subscription, "plan_id", 0))
    current_status = str(_row_value(subscription, "status", 1))
    current_start = _as_datetime(
        _row_value(subscription, "current_period_start", 2)
    )
    current_end = _as_datetime(_row_value(subscription, "current_period_end", 3))
    subscription_updated_at = _as_datetime(_row_value(subscription, "updated_at", 4))
    invoice_created_at = _as_datetime(_row_value(invoice, "created_at", 8))

    if (
        current_status in {
            SubscriptionStatus.SUSPENDED.value,
            SubscriptionStatus.CANCELED.value,
        }
        and subscription_updated_at
        and invoice_created_at
        and invoice_created_at <= subscription_updated_at
    ):
        raise HTTPException(
            status_code=409,
            detail="Invoice predates the current subscription status",
        )

    if current_plan_id == plan_id:
        next_start = current_start or invoice_start
        next_end = max(current_end, invoice_end) if current_end else invoice_end
    else:
        if current_end and invoice_end <= current_end:
            raise HTTPException(
                status_code=409,
                detail="Invoice is stale and cannot replace the current subscription",
            )
        next_start = datetime.now()
        next_end = max(current_end, invoice_end) if current_end else invoice_end

    await cursor.execute(
        """
        UPDATE school_subscriptions
        SET status = %s,
            plan_id = %s,
            current_period_start = %s,
            current_period_end = %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE school_id = %s
        """,
        (
            SubscriptionStatus.ACTIVE.value,
            plan_id,
            next_start,
            next_end,
            school_id,
        ),
    )


async def _apply_refunded_invoice(cursor, invoice) -> None:
    school_id = int(_row_value(invoice, "school_id", 1))
    plan_id = int(_row_value(invoice, "plan_id", 2))
    invoice_end = _as_datetime(_row_value(invoice, "period_end", 7))
    if invoice_end is None:
        raise HTTPException(status_code=409, detail="Invoice period is invalid")
    await cursor.execute(
        """
        UPDATE school_subscriptions
        SET status = %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE school_id = %s
          AND plan_id = %s
          AND current_period_end <= %s
        """,
        (
            SubscriptionStatus.PAST_DUE.value,
            school_id,
            plan_id,
            invoice_end,
        ),
    )


def _subscription_expired(period_end) -> bool:
    if not period_end:
        return True
    if isinstance(period_end, str):
        try:
            period_end = datetime.fromisoformat(period_end)
        except ValueError:
            return True
    if not isinstance(period_end, datetime):
        return True
    grace_until = period_end + timedelta(days=settings.SUBSCRIPTION_GRACE_DAYS)
    now = datetime.now(period_end.tzinfo) if period_end.tzinfo else datetime.now()
    return grace_until < now


def _past_due_grace_expired(grace_started_at) -> bool:
    if not grace_started_at:
        return True
    if isinstance(grace_started_at, str):
        try:
            grace_started_at = datetime.fromisoformat(grace_started_at)
        except ValueError:
            return True
    if not isinstance(grace_started_at, datetime):
        return True
    now = (
        datetime.now(grace_started_at.tzinfo)
        if grace_started_at.tzinfo
        else datetime.now()
    )
    return (
        grace_started_at + timedelta(days=settings.SUBSCRIPTION_GRACE_DAYS)
        < now
    )


class SchoolSubscriptionCreateRequest(BaseModel):
    plan_id: int
    status: SubscriptionStatus = SubscriptionStatus.TRIAL
    days: int = 30
    create_invoice: bool = False
    invoice_status: InvoiceStatus = InvoiceStatus.PENDING
    payment_method: Optional[str] = None
    provider_payment_id: Optional[str] = None
    payment_note: Optional[str] = None


class SubscriptionPlanRequest(BaseModel):
    name: str
    description: Optional[str] = None
    price_monthly: float = 0
    price_yearly: float = 0
    currency: str = "USD"
    is_active: bool = True


class PlanEntitlementRequest(BaseModel):
    entitlement_key: str
    entitlement_value: str


class PlanLimitsRequest(BaseModel):
    students_max: int = 0
    students_unlimited: bool = False
    teachers_max: int = 0
    teachers_unlimited: bool = False
    classes_max: int = 0
    classes_unlimited: bool = False


class SubscriptionInvoiceUpdateRequest(BaseModel):
    status: InvoiceStatus
    payment_method: Optional[str] = None
    provider_payment_id: Optional[str] = None
    payment_note: Optional[str] = None


class PaymentProviderWebhookRequest(BaseModel):
    status: InvoiceStatus
    invoice_id: Optional[int] = None
    provider_payment_id: Optional[str] = None
    payment_method: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    event_id: Optional[str] = None
    raw_event: Optional[dict] = None


async def get_school_id_for_user(user: User, conn) -> Optional[int]:
    """Get school_id for the current user based on their role"""
    async with conn.cursor() as cursor:
        if _is_application_owner(user):
            # Application owners can access any school.
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
async def get_subscription_plans(
    include_inactive: bool = False,
    current_user: User = Depends(get_current_user),
):
    """Get all available subscription plans"""
    if include_inactive and not _is_application_owner(current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            if include_inactive:
                await cursor.execute("""
                    SELECT * FROM subscription_plans
                    ORDER BY is_active DESC, price_monthly ASC
                """)
            else:
                await cursor.execute("""
                    SELECT * FROM subscription_plans
                    WHERE is_active = TRUE
                    ORDER BY price_monthly ASC
                """)
            plans = await cursor.fetchall()
            return [SubscriptionPlan(**plan) for plan in plans]
    finally:
        conn.close()


def _validate_plan_request(request: SubscriptionPlanRequest):
    name = request.name.strip()
    if len(name) < 2:
        raise HTTPException(status_code=400, detail="Plan name is required")
    if (
        not math.isfinite(request.price_monthly)
        or not math.isfinite(request.price_yearly)
        or request.price_monthly < 0
        or request.price_yearly < 0
    ):
        raise HTTPException(
            status_code=400,
            detail="Plan price must be a finite non-negative number",
        )
    currency = request.currency.strip().upper()
    if len(currency) != 3:
        raise HTTPException(status_code=400, detail="Currency must be a 3-letter code")
    return name, (request.description or "").strip() or None, currency


@router.post("/plans", response_model=SubscriptionPlan)
async def create_subscription_plan(
    request: SubscriptionPlanRequest,
    current_user: User = Depends(get_current_user),
):
    """Create a school subscription plan (owner only)."""
    if not _is_application_owner(current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    name, description, currency = _validate_plan_request(request)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT id FROM subscription_plans WHERE name = %s",
                (name,),
            )
            if await cursor.fetchone():
                raise HTTPException(
                    status_code=409,
                    detail="A plan with this name already exists",
                )
            await cursor.execute(
                """
                INSERT INTO subscription_plans
                (name, description, price_monthly, price_yearly, currency, is_active)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    name,
                    description,
                    request.price_monthly,
                    request.price_yearly,
                    currency,
                    request.is_active,
                ),
            )
            plan_id = cursor.lastrowid
            await cursor.execute(
                "SELECT * FROM subscription_plans WHERE id = %s",
                (plan_id,),
            )
            plan = await cursor.fetchone()
            await conn.commit()
            return SubscriptionPlan(**plan)
    finally:
        conn.close()


@router.patch("/plans/{plan_id}", response_model=SubscriptionPlan)
async def update_subscription_plan(
    plan_id: int,
    request: SubscriptionPlanRequest,
    current_user: User = Depends(get_current_user),
):
    """Update a school subscription plan (owner only)."""
    if not _is_application_owner(current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    name, description, currency = _validate_plan_request(request)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id FROM subscription_plans
                WHERE name = %s AND id <> %s
                """,
                (name, plan_id),
            )
            if await cursor.fetchone():
                raise HTTPException(
                    status_code=409,
                    detail="A plan with this name already exists",
                )
            await cursor.execute(
                """
                UPDATE subscription_plans
                SET name = %s,
                    description = %s,
                    price_monthly = %s,
                    price_yearly = %s,
                    currency = %s,
                    is_active = %s
                WHERE id = %s
                """,
                (
                    name,
                    description,
                    request.price_monthly,
                    request.price_yearly,
                    currency,
                    request.is_active,
                    plan_id,
                ),
            )
            await cursor.execute(
                "SELECT * FROM subscription_plans WHERE id = %s",
                (plan_id,),
            )
            plan = await cursor.fetchone()
            if not plan:
                raise HTTPException(status_code=404, detail="Plan not found")
            await conn.commit()
            return SubscriptionPlan(**plan)
    finally:
        conn.close()


@router.get("/plans/{plan_id}", response_model=SubscriptionPlan)
async def get_subscription_plan(
    plan_id: int,
    current_user: User = Depends(get_current_user),
):
    """Get a specific subscription plan (owner only)."""
    if not _is_application_owner(current_user):
        raise HTTPException(status_code=403, detail="Access denied")

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


@router.get("/payment-provider/status")
async def get_payment_provider_status(
    current_user: User = Depends(get_current_user),
):
    """Return whether automatic subscription payments can be used."""
    if not _is_application_owner(current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    configured = _payment_provider_configured()
    return {
        "configured": configured,
        "provider": settings.PAYMENT_PROVIDER if configured else "none",
        "name": settings.PAYMENT_PROVIDER_PUBLIC_NAME
        or (settings.PAYMENT_PROVIDER if configured else "Не настроен"),
        "message": (
            "Платёжный провайдер настроен"
            if configured
            else "Автосписание выключено: настройте PAYMENT_PROVIDER, PAYMENT_PROVIDER_ENABLED и PAYMENT_WEBHOOK_SECRET"
        ),
    }


@router.get("/plans/{plan_id}/entitlements", response_model=List[PlanEntitlement])
async def get_plan_entitlements(
    plan_id: int,
    current_user: User = Depends(get_current_user),
):
    """Get entitlements for a specific plan (owner only)."""
    if not _is_application_owner(current_user):
        raise HTTPException(status_code=403, detail="Access denied")

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


async def _peak_plan_usage(cursor, plan_id: int, resource: str):
    if resource == "classes":
        await cursor.execute(
            """
            SELECT s.id AS school_id, s.name AS school_name, COUNT(c.id) AS current_count
            FROM school_subscriptions ss
            JOIN schools s ON s.id = ss.school_id
            LEFT JOIN classes c
              ON c.school_id = s.id AND COALESCE(c.is_archived, 0) = 0
            WHERE ss.plan_id = %s
            GROUP BY s.id, s.name
            ORDER BY current_count DESC
            LIMIT 1
            """,
            (plan_id,),
        )
    else:
        await cursor.execute(
            """
            SELECT s.id AS school_id, s.name AS school_name, COUNT(su.user_id) AS current_count
            FROM school_subscriptions ss
            JOIN schools s ON s.id = ss.school_id
            LEFT JOIN school_users su
              ON su.school_id = s.id AND su.role = %s
            WHERE ss.plan_id = %s
            GROUP BY s.id, s.name
            ORDER BY current_count DESC
            LIMIT 1
            """,
            (resource[:-1], plan_id),
        )
    return await cursor.fetchone()


@router.put("/plans/{plan_id}/limits")
async def update_plan_limits(
    plan_id: int,
    request: PlanLimitsRequest,
    current_user: User = Depends(get_current_user),
):
    """Atomically update school capacity limits for a plan (owner only)."""
    if not _is_application_owner(current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    requested = {
        "students": (request.students_max, request.students_unlimited),
        "teachers": (request.teachers_max, request.teachers_unlimited),
        "classes": (request.classes_max, request.classes_unlimited),
    }
    if any(maximum < 0 for maximum, _ in requested.values()):
        raise HTTPException(
            status_code=400,
            detail="Plan limits must be non-negative integers",
        )

    conn = await get_db_connection()
    await conn.begin()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT id, name FROM subscription_plans WHERE id = %s FOR UPDATE",
                (plan_id,),
            )
            plan = await cursor.fetchone()
            if not plan:
                raise HTTPException(status_code=404, detail="Plan not found")

            for resource, (maximum, unlimited) in requested.items():
                if unlimited:
                    continue
                peak = await _peak_plan_usage(cursor, plan_id, resource)
                current = int(
                    (peak.get("current_count") if isinstance(peak, dict) else peak[2])
                    if peak
                    else 0
                )
                if current > maximum:
                    school_name = (
                        peak.get("school_name")
                        if isinstance(peak, dict)
                        else peak[1]
                    )
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Нельзя установить лимит {maximum}: "
                            f"в школе «{school_name}» уже используется {current}"
                        ),
                    )

            for resource, (maximum, unlimited) in requested.items():
                for key, value in (
                    (f"{resource}.max", str(maximum)),
                    (f"{resource}.unlimited", str(unlimited).lower()),
                ):
                    await cursor.execute(
                        """
                        INSERT INTO plan_entitlements
                        (plan_id, entitlement_key, entitlement_value)
                        VALUES (%s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            entitlement_value = %s
                        """,
                        (plan_id, key, value, value),
                    )

            user_id = (
                current_user.get("id")
                if isinstance(current_user, dict)
                else getattr(current_user, "id", None)
            )
            await cursor.execute(
                """
                INSERT INTO audit_log
                (school_id, user_id, action, entity_type, entity_id, details)
                VALUES (NULL, %s, 'subscription_plan_limits_updated', 'subscription_plan', %s, %s)
                """,
                (user_id, plan_id, json.dumps(request.model_dump())),
            )
            await conn.commit()
            return {"plan_id": plan_id, **request.model_dump()}
    except Exception:
        await conn.rollback()
        raise
    finally:
        conn.close()


@router.post("/plans/{plan_id}/entitlements", response_model=PlanEntitlement)
async def upsert_plan_entitlement(
    plan_id: int,
    request: PlanEntitlementRequest,
    current_user: User = Depends(get_current_user),
):
    """Create or update a plan entitlement (owner only)."""
    if not _is_application_owner(current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    key = request.entitlement_key.strip()
    value = request.entitlement_value.strip()
    if len(key) < 2:
        raise HTTPException(status_code=400, detail="entitlement_key is required")
    if not value:
        raise HTTPException(status_code=400, detail="entitlement_value is required")
    if key in {"students.max", "teachers.max", "classes.max"}:
        try:
            parsed_limit = int(value)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Plan limit must be a non-negative integer",
            ) from exc
        if parsed_limit < 0:
            raise HTTPException(
                status_code=400,
                detail="Plan limit must be a non-negative integer",
            )
        value = str(parsed_limit)
    if key in {
        "students.unlimited",
        "teachers.unlimited",
        "classes.unlimited",
    }:
        normalized = value.lower()
        if normalized not in {"true", "false"}:
            raise HTTPException(
                status_code=400,
                detail="Unlimited flag must be true or false",
            )
        value = normalized

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT id FROM subscription_plans WHERE id = %s",
                (plan_id,),
            )
            if not await cursor.fetchone():
                raise HTTPException(status_code=404, detail="Plan not found")

            await cursor.execute(
                """
                INSERT INTO plan_entitlements
                (plan_id, entitlement_key, entitlement_value)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE entitlement_value = %s
                """,
                (plan_id, key, value, value),
            )
            await cursor.execute(
                """
                SELECT * FROM plan_entitlements
                WHERE plan_id = %s AND entitlement_key = %s
                """,
                (plan_id, key),
            )
            entitlement = await cursor.fetchone()
            await conn.commit()
            return PlanEntitlement(**entitlement)
    finally:
        conn.close()


@router.get("/schools/{school_id}/limits")
async def get_school_subscription_limits(
    school_id: int,
    current_user: User = Depends(get_current_user),
):
    """Return current licensed resource usage for owner or the school's admin."""
    if not _can_view_school_subscription(current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    conn = await get_db_connection()
    try:
        user_school_id = await get_school_id_for_user(current_user)
        if not _is_application_owner(current_user) and user_school_id != school_id:
            raise HTTPException(status_code=403, detail="Access denied")

        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT sp.id, sp.name, ss.current_period_start
                FROM school_subscriptions ss
                JOIN subscription_plans sp ON sp.id = ss.plan_id
                WHERE ss.school_id = %s
                """,
                (school_id,),
            )
            plan = await cursor.fetchone()
            if not plan:
                raise HTTPException(status_code=404, detail="Subscription not found")

            resources = {}
            def resource_payload(current, maximum):
                percent = (
                    None
                    if maximum is None
                    else round((current / max(1, maximum)) * 100, 1)
                )
                level = (
                    "unlimited"
                    if percent is None
                    else "blocked"
                    if percent >= 100
                    else "critical"
                    if percent >= 90
                    else "warning"
                    if percent >= 80
                    else "ok"
                )
                return {
                    "current": current,
                    "maximum": maximum,
                    "remaining": (
                        None if maximum is None else max(0, maximum - current)
                    ),
                    "percent": percent,
                    "level": level,
                }

            for resource in ("students", "teachers", "classes"):
                limit = await get_school_resource_limit(
                    cursor,
                    school_id=school_id,
                    resource=resource,
                )
                resources[resource] = resource_payload(
                    limit.current,
                    limit.maximum,
                )

            plan_id = plan.get("id") if isinstance(plan, dict) else plan[0]
            period_start = (
                plan.get("current_period_start")
                if isinstance(plan, dict)
                else plan[2]
            )
            await cursor.execute(
                """
                SELECT entitlement_key, entitlement_value
                FROM plan_entitlements
                WHERE plan_id = %s AND entitlement_key IN (
                    'storage.mb', 'storage.unlimited',
                    'ai.quota',
                    'messages.daily', 'messages.unlimited',
                    'exports.csv'
                )
                """,
                (plan_id,),
            )
            entitlement_values = {
                (
                    row.get("entitlement_key")
                    if isinstance(row, dict)
                    else row[0]
                ): (
                    row.get("entitlement_value")
                    if isinstance(row, dict)
                    else row[1]
                )
                for row in await cursor.fetchall()
            }

            await cursor.execute(
                "SELECT used_bytes FROM school_storage_usage WHERE school_id = %s",
                (school_id,),
            )
            storage_row = await cursor.fetchone()
            used_bytes = (
                storage_row.get("used_bytes")
                if isinstance(storage_row, dict)
                else storage_row[0] if storage_row else 0
            ) or 0
            storage_unlimited = str(
                entitlement_values.get("storage.unlimited", "")
            ).lower() == "true"
            storage_max = (
                None
                if storage_unlimited
                else int(float(entitlement_values.get("storage.mb", 0) or 0))
            )
            resources["storage"] = resource_payload(
                round(float(used_bytes) / 1024 / 1024, 1),
                storage_max,
            )

            quota_specs = {
                "ai": ("ai.quota", False, None),
                "messages": (
                    "messages.daily",
                    True,
                    "messages.unlimited",
                ),
                "exports": ("exports.csv", False, None),
            }
            for resource, (key, daily, unlimited_key) in quota_specs.items():
                raw_limit = entitlement_values.get(key)
                unlimited = str(raw_limit or "").lower() == "unlimited"
                if unlimited_key:
                    unlimited = unlimited or str(
                        entitlement_values.get(unlimited_key, "")
                    ).lower() == "true"
                maximum = None
                if not unlimited:
                    try:
                        maximum = float(raw_limit)
                    except (TypeError, ValueError):
                        maximum = 0
                if daily:
                    await cursor.execute(
                        """
                        SELECT COALESCE(SUM(event_value), 0) AS current_usage
                        FROM usage_events
                        WHERE school_id = %s AND event_key = %s
                          AND event_date = CURRENT_DATE
                        """,
                        (school_id, key),
                    )
                else:
                    await cursor.execute(
                        """
                        SELECT COALESCE(SUM(event_value), 0) AS current_usage
                        FROM usage_events
                        WHERE school_id = %s AND event_key = %s
                          AND event_date >= %s
                        """,
                        (school_id, key, period_start),
                    )
                usage_row = await cursor.fetchone()
                current = float(
                    (
                        usage_row.get("current_usage")
                        if isinstance(usage_row, dict)
                        else usage_row[0] if usage_row else 0
                    )
                    or 0
                )
                resources[resource] = resource_payload(current, maximum)

            return {
                "school_id": school_id,
                "plan_id": plan_id,
                "plan_name": (
                    plan.get("name") if isinstance(plan, dict) else plan[1]
                ),
                "resources": resources,
            }
    finally:
        conn.close()


@router.delete("/plans/{plan_id}/entitlements/{entitlement_key}")
async def delete_plan_entitlement(
    plan_id: int,
    entitlement_key: str,
    current_user: User = Depends(get_current_user),
):
    """Delete a plan entitlement (owner only)."""
    if not _is_application_owner(current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                DELETE FROM plan_entitlements
                WHERE plan_id = %s AND entitlement_key = %s
                """,
                (plan_id, entitlement_key),
            )
            await conn.commit()
            return {"message": "Entitlement deleted"}
    finally:
        conn.close()


@router.get("/schools/{school_id}/subscription", response_model=SubscriptionWithPlan)
async def get_school_subscription(
    school_id: int,
    current_user: User = Depends(get_current_user)
):
    """Get subscription for a specific school"""
    if not _can_view_school_subscription(current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    conn = await get_db_connection()
    try:
        # Check if user has access to this school
        user_school_id = await get_school_id_for_user(current_user, conn)
        if not _is_application_owner(current_user) and user_school_id != school_id:
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
            if (
                subscription.get("status") in (SubscriptionStatus.ACTIVE.value, SubscriptionStatus.TRIAL.value)
                and _subscription_expired(subscription.get("current_period_end"))
            ):
                subscription["status"] = SubscriptionStatus.PAST_DUE.value

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
    plan_id: Optional[int] = None,
    payload: Optional[SchoolSubscriptionCreateRequest] = Body(default=None),
    current_user: User = Depends(get_current_user)
):
    """Create or update subscription for a school."""
    if not _is_application_owner(current_user):
        raise HTTPException(
            status_code=403,
            detail="Only application owners can manage school subscriptions",
        )

    conn = await get_db_connection()
    await conn.begin()
    try:
        request = payload
        if request is None and plan_id is not None:
            request = SchoolSubscriptionCreateRequest(plan_id=plan_id)
        if request is None:
            raise HTTPException(status_code=400, detail="plan_id is required")
        if request.days not in SUPPORTED_SUBSCRIPTION_DAYS:
            raise HTTPException(
                status_code=400,
                detail="days must be one of: 30, 90, 365",
            )
        if request.invoice_status == InvoiceStatus.PAID and request.create_invoice:
            note = (request.payment_note or "").strip()
            if len(note) < 5:
                raise HTTPException(
                    status_code=400,
                    detail="payment_note is required for manually paid invoices",
                )
        if (
            request.create_invoice
            and request.payment_method in PROVIDER_PAYMENT_METHODS
            and not _payment_provider_configured()
        ):
            raise HTTPException(
                status_code=400,
                detail="Payment provider is not configured",
            )
        effective_status = (
            SubscriptionStatus.ACTIVE
            if request.create_invoice
            and request.invoice_status == InvoiceStatus.PAID
            else request.status
        )

        async with conn.cursor() as cursor:
            # Check if school exists
            await cursor.execute("SELECT id FROM schools WHERE id = %s", (school_id,))
            if not await cursor.fetchone():
                raise HTTPException(status_code=404, detail="School not found")

            # Check if plan exists
            await cursor.execute(
                """
                SELECT id, price_monthly, price_yearly, currency, is_active
                FROM subscription_plans
                WHERE id = %s
                """,
                (request.plan_id,),
            )
            plan = await cursor.fetchone()
            if not plan:
                raise HTTPException(status_code=404, detail="Plan not found")
            if not bool(_row_value(plan, "is_active", 4, False)):
                raise HTTPException(status_code=409, detail="Plan is inactive")

            await cursor.execute(
                """
                SELECT plan_id, status, current_period_start, current_period_end
                FROM school_subscriptions
                WHERE school_id = %s
                FOR UPDATE
                """,
                (school_id,),
            )
            current_subscription = await cursor.fetchone()
            now = datetime.now()
            current_end = _as_datetime(
                _row_value(current_subscription, "current_period_end", 3)
            )
            current_start = _as_datetime(
                _row_value(current_subscription, "current_period_start", 2)
            )
            period_start = (
                current_end if current_end is not None and current_end > now else now
            )
            period_end = period_start + timedelta(days=request.days)

            invoice_id = None
            if request.create_invoice:
                amount = _invoice_amount(plan, request.days)
                due_date = now + timedelta(days=settings.SUBSCRIPTION_GRACE_DAYS)
                paid_at = datetime.now() if request.invoice_status == InvoiceStatus.PAID else None
                confirmed_by_user_id = (
                    current_user.id
                    if request.invoice_status == InvoiceStatus.PAID
                    else None
                )
                await cursor.execute("""
                    INSERT INTO subscription_invoices
                    (school_id, plan_id, amount, currency, status, period_start, period_end, due_date, payment_method, provider_payment_id, payment_note, confirmed_by_user_id, paid_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    school_id,
                    request.plan_id,
                    amount,
                    _row_value(plan, "currency", 3, "USD") or "USD",
                    request.invoice_status.value,
                    period_start,
                    period_end,
                    due_date,
                    request.payment_method,
                    request.provider_payment_id,
                    (request.payment_note or "").strip()[:2000] or None,
                    confirmed_by_user_id,
                    paid_at,
                ))
                invoice_id = cursor.lastrowid
                if request.invoice_status == InvoiceStatus.PAID:
                    await _apply_paid_invoice(
                        cursor,
                        {
                            "id": invoice_id,
                            "school_id": school_id,
                            "plan_id": request.plan_id,
                            "period_start": period_start,
                            "period_end": period_end,
                        },
                    )
            else:
                subscription_start = (
                    current_start
                    if current_subscription is not None
                    and current_start is not None
                    and current_end is not None
                    and current_end > now
                    else now
                )
                await cursor.execute(
                    """
                    INSERT INTO school_subscriptions
                    (school_id, plan_id, status, current_period_start, current_period_end)
                    VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                    plan_id = %s,
                    status = %s,
                    current_period_start = %s,
                    current_period_end = %s,
                    updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        school_id,
                        request.plan_id,
                        effective_status.value,
                        subscription_start,
                        period_end,
                        request.plan_id,
                        effective_status.value,
                        subscription_start,
                        period_end,
                    ),
                )

            await cursor.execute(
                """
                INSERT INTO audit_log (school_id, user_id, action, entity_type, entity_id, details)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    school_id,
                    current_user.id,
                    "school_subscription_saved",
                    "school_subscription",
                    school_id,
                    json.dumps(
                        {
                            "plan_id": request.plan_id,
                            "requested_status": request.status.value,
                            "status": effective_status.value,
                            "days": request.days,
                            "create_invoice": request.create_invoice,
                            "invoice_status": request.invoice_status.value,
                            "invoice_id": invoice_id,
                            "payment_method": request.payment_method,
                            "provider_payment_id": request.provider_payment_id,
                            "payment_note": (request.payment_note or "").strip()
                            or None,
                            "confirmed_by_user_id": (
                                current_user.id
                                if request.invoice_status == InvoiceStatus.PAID
                                and request.create_invoice
                                else None
                            ),
                        },
                        ensure_ascii=False,
                    ),
                ),
            )

            await conn.commit()
            return {
                "message": "Subscription saved successfully",
                "invoice_id": invoice_id,
                "period_start": period_start,
                "period_end": period_end,
            }
    except Exception:
        await conn.rollback()
        raise
    finally:
        conn.close()


@router.get("/schools/{school_id}/usage", response_model=List[UsageStats])
async def get_school_usage(
    school_id: int,
    current_user: User = Depends(get_current_user)
):
    """Get usage statistics for a school"""
    if not _can_view_school_subscription(current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    conn = await get_db_connection()
    try:
        # Check if user has access to this school
        user_school_id = await get_school_id_for_user(current_user, conn)
        if not _is_application_owner(current_user) and user_school_id != school_id:
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
    if not _can_view_school_subscription(current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    conn = await get_db_connection()
    try:
        # Check if user has access to this school
        user_school_id = await get_school_id_for_user(current_user, conn)
        if not _is_application_owner(current_user) and user_school_id != school_id:
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
    if not _can_view_school_subscription(current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    conn = await get_db_connection()
    try:
        # Check if user has access to this school
        user_school_id = await get_school_id_for_user(current_user, conn)
        if not _is_application_owner(current_user) and user_school_id != school_id:
            raise HTTPException(status_code=403, detail="Access denied")

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
            subscription_status = result["status"]
            if subscription_status in ["suspended", "canceled"]:
                return EntitlementCheck(
                    school_id=school_id,
                    entitlement_key=entitlement_key,
                    has_access=False,
                    reason=f"Subscription is {subscription_status}"
                )
            if subscription_status == "past_due" and _past_due_grace_expired(
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
                    reason="Subscription period has ended"
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
    """Get invoices for a school. Billing history is owner-only."""
    if not _is_application_owner(current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT si.*, sp.name as plan_name,
                       cu.username as confirmed_by_username,
                       TRIM(BOTH '"' FROM cp.full_name) as confirmed_by_full_name
                FROM subscription_invoices si
                JOIN subscription_plans sp ON si.plan_id = sp.id
                LEFT JOIN users cu ON cu.id = si.confirmed_by_user_id
                LEFT JOIN profiles cp ON cp.user_id = cu.id
                WHERE si.school_id = %s
                ORDER BY si.created_at DESC
            """, (school_id,))
            
            invoices = await cursor.fetchall()
            return invoices
    finally:
        conn.close()


@router.get("/invoices/pending")
async def get_pending_school_invoices(
    current_user: User = Depends(get_current_user),
):
    """Get pending school subscription invoices for owner confirmation."""
    if not _is_application_owner(current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT si.*, sp.name as plan_name, s.name as school_name,
                       cu.username as confirmed_by_username,
                       TRIM(BOTH '"' FROM cp.full_name) as confirmed_by_full_name
                FROM subscription_invoices si
                JOIN subscription_plans sp ON si.plan_id = sp.id
                JOIN schools s ON s.id = si.school_id
                LEFT JOIN users cu ON cu.id = si.confirmed_by_user_id
                LEFT JOIN profiles cp ON cp.user_id = cu.id
                WHERE si.status = %s
                ORDER BY si.due_date ASC, si.created_at DESC
                LIMIT 50
            """, (InvoiceStatus.PENDING.value,))
            return await cursor.fetchall()
    finally:
        conn.close()


@router.get("/schools/subscription-issues")
async def get_school_subscription_issues(
    current_user: User = Depends(get_current_user),
):
    """Get schools with subscription or billing issues for owner review."""
    if not _is_application_owner(current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT s.id as school_id,
                       s.name as school_name,
                       ss.status as subscription_status,
                       ss.current_period_end,
                       sp.name as plan_name,
                       COALESCE(SUM(CASE WHEN si.status = 'pending' THEN si.amount ELSE 0 END), 0) as pending_amount,
                       MIN(CASE WHEN si.status = 'pending' THEN si.due_date ELSE NULL END) as nearest_due_date,
                       SUM(CASE WHEN si.status = 'pending' THEN 1 ELSE 0 END) as pending_invoices
                FROM schools s
                LEFT JOIN school_subscriptions ss ON ss.school_id = s.id
                LEFT JOIN subscription_plans sp ON sp.id = ss.plan_id
                LEFT JOIN subscription_invoices si ON si.school_id = s.id
                WHERE ss.id IS NULL
                   OR ss.status IN ('past_due', 'suspended', 'canceled')
                   OR ss.current_period_end < DATE_SUB(NOW(), INTERVAL %s DAY)
                   OR EXISTS (
                       SELECT 1 FROM subscription_invoices pi
                       WHERE pi.school_id = s.id AND pi.status = 'pending'
                   )
                GROUP BY s.id, s.name, ss.status, ss.current_period_end, sp.name
                ORDER BY
                    CASE
                        WHEN ss.id IS NULL THEN 0
                        WHEN ss.status IN ('past_due', 'suspended', 'canceled') THEN 1
                        WHEN ss.current_period_end < DATE_SUB(NOW(), INTERVAL %s DAY) THEN 2
                        ELSE 3
                    END,
                    nearest_due_date ASC,
                    s.name ASC
                LIMIT 100
            """, (settings.SUBSCRIPTION_GRACE_DAYS, settings.SUBSCRIPTION_GRACE_DAYS))
            rows = await cursor.fetchall()

        issues = []
        for row in rows:
            get = row.get if isinstance(row, dict) else None
            school_id = get("school_id") if get else row[0]
            school_name = get("school_name") if get else row[1]
            status = get("subscription_status") if get else row[2]
            period_end = get("current_period_end") if get else row[3]
            plan_name = get("plan_name") if get else row[4]
            pending_amount = float((get("pending_amount") if get else row[5]) or 0)
            nearest_due_date = get("nearest_due_date") if get else row[6]
            pending_invoices = int((get("pending_invoices") if get else row[7]) or 0)

            issue_type = "pending_invoice"
            issue_description = "Есть счёт, ожидающий подтверждения"
            if status is None:
                issue_type = "no_subscription"
                issue_description = "Нет подписки школы"
            elif status in ("past_due", "suspended", "canceled"):
                issue_type = status
                issue_description = {
                    "past_due": "Подписка в долге",
                    "suspended": "Подписка на паузе",
                    "canceled": "Подписка отключена",
                }.get(status, f"Подписка: {status}")
            elif _subscription_expired(period_end):
                issue_type = "expired"
                issue_description = "Период подписки истёк"

            issues.append({
                "school_id": school_id,
                "school_name": school_name,
                "issue_type": issue_type,
                "issue_description": issue_description,
                "subscription_status": status,
                "plan_name": plan_name,
                "current_period_end": period_end,
                "pending_amount": pending_amount,
                "pending_invoices": pending_invoices,
                "nearest_due_date": nearest_due_date,
            })

        return issues
    finally:
        conn.close()


@router.patch("/schools/{school_id}/invoices/{invoice_id}")
async def update_school_invoice(
    school_id: int,
    invoice_id: int,
    request: SubscriptionInvoiceUpdateRequest,
    current_user: User = Depends(get_current_user),
):
    """Update a school subscription invoice. Payment confirmation is owner-only."""
    if not _is_application_owner(current_user):
        raise HTTPException(
            status_code=403,
            detail="Only application owners can confirm subscription invoices",
        )

    note = (request.payment_note or "").strip()
    if request.status in {
        InvoiceStatus.PAID,
        InvoiceStatus.REFUNDED,
    } and len(note) < 5:
        raise HTTPException(
            status_code=400,
            detail="payment_note is required for paid or refunded invoices",
        )
    if (
        request.payment_method in PROVIDER_PAYMENT_METHODS
        and not _payment_provider_configured()
    ):
        raise HTTPException(
            status_code=400,
            detail="Payment provider is not configured",
        )

    conn = await get_db_connection()
    await conn.begin()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id, school_id, plan_id, status, amount, currency,
                       period_start, period_end, created_at
                FROM subscription_invoices
                WHERE id = %s AND school_id = %s
                FOR UPDATE
                """,
                (invoice_id, school_id),
            )
            invoice = await cursor.fetchone()
            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")
            current_status = str(_row_value(invoice, "status", 3))
            requested_status = request.status.value
            _validate_invoice_transition(current_status, requested_status)
            if current_status == requested_status:
                await conn.commit()
                return {
                    "message": "Invoice already has requested status",
                    "idempotent": True,
                }

            paid_at = datetime.now() if request.status == InvoiceStatus.PAID else None
            confirmed_by_user_id = (
                current_user.id if request.status == InvoiceStatus.PAID else None
            )

            await cursor.execute(
                """
                UPDATE subscription_invoices
                SET status = %s,
                    payment_method = COALESCE(%s, payment_method),
                    provider_payment_id = COALESCE(%s, provider_payment_id),
                    payment_note = COALESCE(%s, payment_note),
                    confirmed_by_user_id = %s,
                    paid_at = %s
                WHERE id = %s AND school_id = %s
                """,
                (
                    request.status.value,
                    request.payment_method,
                    request.provider_payment_id,
                    note[:2000] or None,
                    confirmed_by_user_id,
                    paid_at,
                    invoice_id,
                    school_id,
                ),
            )

            if request.status == InvoiceStatus.PAID:
                await _apply_paid_invoice(cursor, invoice)
            elif request.status == InvoiceStatus.REFUNDED:
                await _apply_refunded_invoice(cursor, invoice)

            await cursor.execute(
                """
                INSERT INTO audit_log (school_id, user_id, action, entity_type, entity_id, details)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    school_id,
                    current_user.id,
                    "school_subscription_invoice_updated",
                    "subscription_invoice",
                    invoice_id,
                    json.dumps(
                        {
                            "status": request.status.value,
                            "payment_method": request.payment_method,
                            "provider_payment_id": request.provider_payment_id,
                            "payment_note": note or None,
                            "confirmed_by_user_id": confirmed_by_user_id,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )

            await conn.commit()
            return {"message": "Invoice updated successfully"}
    except Exception:
        await conn.rollback()
        raise
    finally:
        conn.close()


@router.post("/payment-provider/webhook")
async def payment_provider_webhook(
    http_request: Request,
    x_payment_timestamp: Optional[str] = Header(default=None),
    x_payment_signature: Optional[str] = Header(default=None),
    x_payment_event_id: Optional[str] = Header(default=None),
):
    """Receive subscription invoice status updates from a payment provider."""
    if not _payment_provider_configured():
        raise HTTPException(status_code=503, detail="Payment provider is not configured")
    if not x_payment_timestamp or not x_payment_signature or not x_payment_event_id:
        raise HTTPException(status_code=401, detail="Missing webhook signature headers")
    if len(x_payment_event_id) > 255:
        raise HTTPException(status_code=400, detail="Webhook event id is too long")
    try:
        timestamp = int(x_payment_timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid webhook timestamp") from exc
    if abs(int(time.time()) - timestamp) > WEBHOOK_MAX_AGE_SECONDS:
        raise HTTPException(status_code=401, detail="Webhook timestamp is outside allowed window")

    raw_body = await http_request.body()
    if len(raw_body) > WEBHOOK_MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Webhook payload is too large")
    expected_signature = hmac.new(
        settings.PAYMENT_WEBHOOK_SECRET.encode("utf-8"),
        f"{timestamp}.".encode("utf-8") + raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not secrets.compare_digest(x_payment_signature, expected_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        request = PaymentProviderWebhookRequest.model_validate_json(raw_body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook payload") from exc
    if request.event_id and request.event_id != x_payment_event_id:
        raise HTTPException(status_code=400, detail="Webhook event id mismatch")
    if request.invoice_id is None and not request.provider_payment_id:
        raise HTTPException(
            status_code=400,
            detail="invoice_id or provider_payment_id is required",
        )

    conn = await get_db_connection()
    await conn.begin()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT IGNORE INTO subscription_webhook_events
                (provider, event_id, payload_hash)
                VALUES (%s, %s, %s)
                """,
                (
                    settings.PAYMENT_PROVIDER,
                    x_payment_event_id,
                    hashlib.sha256(raw_body).hexdigest(),
                ),
            )
            if cursor.rowcount == 0:
                await conn.commit()
                return {
                    "message": "Webhook already processed",
                    "event_id": x_payment_event_id,
                    "idempotent": True,
                }

            if request.invoice_id is not None:
                await cursor.execute(
                    """
                    SELECT id, school_id, plan_id, status, amount, currency,
                           period_start, period_end, created_at
                    FROM subscription_invoices
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (request.invoice_id,),
                )
            else:
                await cursor.execute(
                    """
                    SELECT id, school_id, plan_id, status, amount, currency,
                           period_start, period_end, created_at
                    FROM subscription_invoices
                    WHERE provider_payment_id = %s
                    FOR UPDATE
                    """,
                    (request.provider_payment_id,),
                )
            invoice = await cursor.fetchone()
            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            if request.amount is not None and Decimal(
                str(_row_value(invoice, "amount", 4))
            ) != Decimal(str(request.amount)):
                raise HTTPException(status_code=400, detail="Webhook amount mismatch")
            if request.currency and _row_value(
                invoice, "currency", 5
            ) != request.currency.upper():
                raise HTTPException(status_code=400, detail="Webhook currency mismatch")

            current_status = str(_row_value(invoice, "status", 3))
            requested_status = request.status.value
            _validate_invoice_transition(current_status, requested_status)
            if current_status == requested_status:
                await conn.commit()
                return {
                    "message": "Invoice already has requested status",
                    "invoice_id": _row_value(invoice, "id", 0),
                    "status": requested_status,
                    "idempotent": True,
                }

            paid_at = datetime.now() if request.status == InvoiceStatus.PAID else None
            note = f"Webhook {settings.PAYMENT_PROVIDER}"
            note = f"{note}: {x_payment_event_id}"

            await cursor.execute(
                """
                UPDATE subscription_invoices
                SET status = %s,
                    payment_method = COALESCE(%s, payment_method),
                    provider_payment_id = COALESCE(%s, provider_payment_id),
                    payment_note = COALESCE(%s, payment_note),
                    confirmed_by_user_id = NULL,
                    paid_at = %s
                WHERE id = %s
                """,
                (
                    request.status.value,
                    request.payment_method,
                    request.provider_payment_id,
                    note,
                    paid_at,
                    _row_value(invoice, "id", 0),
                ),
            )

            if request.status == InvoiceStatus.PAID:
                await _apply_paid_invoice(cursor, invoice)
            elif request.status == InvoiceStatus.REFUNDED:
                await _apply_refunded_invoice(cursor, invoice)

            await cursor.execute(
                """
                INSERT INTO audit_log (school_id, user_id, action, entity_type, entity_id, details)
                VALUES (%s, NULL, %s, %s, %s, %s)
                """,
                (
                    _row_value(invoice, "school_id", 1),
                    "school_subscription_invoice_webhook",
                    "subscription_invoice",
                    _row_value(invoice, "id", 0),
                    json.dumps(
                        {
                            "status": request.status.value,
                            "provider": settings.PAYMENT_PROVIDER,
                            "provider_payment_id": request.provider_payment_id,
                            "payment_method": request.payment_method,
                            "event_id": x_payment_event_id,
                            "raw_event": request.raw_event,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )

            await conn.commit()
            return {
                "message": "Webhook processed",
                "invoice_id": _row_value(invoice, "id", 0),
                "status": request.status.value,
            }
    except Exception:
        await conn.rollback()
        raise
    finally:
        conn.close()
