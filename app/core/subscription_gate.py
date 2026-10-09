from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Request

from app.core.config import settings
from app.core.entitlements import get_school_id_for_user
from app.core.security import verify_token
from app.db.connection import get_db_connection
from app.db.user_operations import get_user
from app.models.user import UserInDB


APPLICATION_OWNER_ROLES = {"owner", "superadmin", "root"}
SCHOOL_SUBSCRIPTION_GATE_ROLES = {"admin"}
BLOCKED_ADMIN_MESSAGE = "Оплатите подписку. Приложение недоступно."
WARNING_ADMIN_MESSAGE = (
    "Подписка школы не оплачена. Оплатите подписку, чтобы доступ не был заблокирован."
)

EXEMPT_PATH_PREFIXES = (
    "/api/v1/token",
    "/api/v1/register",
    "/api/v1/verify-token",
    "/api/v1/change-password",
    "/api/v1/health",
    "/api/v1/subscriptions",
    "/api/v1/owner",
    "/api/v1/owners",
    "/static/",
    "/docs",
    "/redoc",
    "/openapi.json",
)


@dataclass(frozen=True)
class SubscriptionGateResult:
    blocked: bool = False
    warning: bool = False
    message: str = ""
    school_id: Optional[int] = None
    days_left: Optional[int] = None
    reason: str = ""


def is_subscription_gate_exempt(path: str) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in EXEMPT_PATH_PREFIXES)


def _get_role(user: UserInDB) -> str:
    return user.role if hasattr(user, "role") else ""


def _parse_datetime(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _deadline_from(*values) -> Optional[datetime]:
    for value in values:
        parsed = _parse_datetime(value)
        if parsed is not None:
            return parsed + timedelta(days=settings.SUBSCRIPTION_GRACE_DAYS)
    return None


async def _user_from_request(request: Request) -> Optional[UserInDB]:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    try:
        payload = verify_token(token)
    except Exception:
        return None
    username = payload.get("sub")
    if not username:
        return None
    return await get_user(username)


async def check_school_subscription_gate(request: Request) -> SubscriptionGateResult:
    if is_subscription_gate_exempt(request.url.path):
        return SubscriptionGateResult()

    user = await _user_from_request(request)
    if user is None:
        return SubscriptionGateResult()

    role = _get_role(user)
    if role in APPLICATION_OWNER_ROLES:
        return SubscriptionGateResult()
    if role not in SCHOOL_SUBSCRIPTION_GATE_ROLES:
        return SubscriptionGateResult()

    school_id = await get_school_id_for_user(user)
    if school_id is None:
        return SubscriptionGateResult()

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT status, current_period_start, current_period_end, updated_at
                FROM school_subscriptions
                WHERE school_id = %s
                """,
                (school_id,),
            )
            subscription = await cursor.fetchone()
    finally:
        conn.close()

    if not subscription:
        return SubscriptionGateResult(
            blocked=True,
            message=BLOCKED_ADMIN_MESSAGE,
            school_id=school_id,
            reason="no_subscription",
        )

    status = subscription.get("status") if isinstance(subscription, dict) else subscription[0]
    period_start = subscription.get("current_period_start") if isinstance(subscription, dict) else subscription[1]
    period_end = subscription.get("current_period_end") if isinstance(subscription, dict) else subscription[2]
    updated_at = subscription.get("updated_at") if isinstance(subscription, dict) else subscription[3]
    now = datetime.now()

    if status in {"active", "trial"}:
        parsed_end = _parse_datetime(period_end)
        if parsed_end is None:
            return SubscriptionGateResult(
                blocked=True,
                message=BLOCKED_ADMIN_MESSAGE,
                school_id=school_id,
                reason="missing_period_end",
            )
        if parsed_end >= now:
            return SubscriptionGateResult()
        deadline = parsed_end + timedelta(days=settings.SUBSCRIPTION_GRACE_DAYS)
        if deadline >= now:
            return SubscriptionGateResult(
                warning=True,
                message=WARNING_ADMIN_MESSAGE,
                school_id=school_id,
                days_left=max(0, (deadline.date() - now.date()).days),
                reason="expired_in_grace",
            )
        return SubscriptionGateResult(
            blocked=True,
            message=BLOCKED_ADMIN_MESSAGE,
            school_id=school_id,
            reason="expired",
        )

    if status == "past_due":
        deadline = _deadline_from(period_end)
        if deadline is not None and deadline >= now:
            return SubscriptionGateResult(
                warning=True,
                message=WARNING_ADMIN_MESSAGE,
                school_id=school_id,
                days_left=max(0, (deadline.date() - now.date()).days),
                reason="past_due_in_grace",
            )
        return SubscriptionGateResult(
            blocked=True,
            message=BLOCKED_ADMIN_MESSAGE,
            school_id=school_id,
            reason="past_due",
        )

    if status in {"suspended", "canceled"}:
        return SubscriptionGateResult(
            blocked=True,
            message=BLOCKED_ADMIN_MESSAGE,
            school_id=school_id,
            reason=str(status),
        )

    return SubscriptionGateResult()
