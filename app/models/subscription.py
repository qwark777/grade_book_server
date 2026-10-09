from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    PAST_DUE = "past_due"
    SUSPENDED = "suspended"
    CANCELED = "canceled"
    TRIAL = "trial"


class InvoiceStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"


class SubscriptionPlan(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    price_monthly: float
    price_yearly: float
    currency: str = "USD"
    is_active: bool = True
    created_at: datetime

    class Config:
        from_attributes = True


class PlanEntitlement(BaseModel):
    id: int
    plan_id: int
    entitlement_key: str
    entitlement_value: str
    created_at: datetime

    class Config:
        from_attributes = True


class SchoolSubscription(BaseModel):
    id: int
    school_id: int
    plan_id: int
    status: SubscriptionStatus
    current_period_start: datetime
    current_period_end: datetime
    seats_students: int = 0
    seats_teachers: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UsageEvent(BaseModel):
    id: int
    school_id: int
    user_id: Optional[int] = None
    event_key: str
    event_value: float = 1.0
    event_date: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class SubscriptionInvoice(BaseModel):
    id: int
    school_id: int
    plan_id: int
    amount: float
    currency: str = "USD"
    status: InvoiceStatus
    period_start: datetime
    period_end: datetime
    due_date: datetime
    payment_method: Optional[str] = None
    provider_payment_id: Optional[str] = None
    payment_note: Optional[str] = None
    confirmed_by_user_id: Optional[int] = None
    confirmed_by_username: Optional[str] = None
    confirmed_by_full_name: Optional[str] = None
    paid_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class SubscriptionWithPlan(BaseModel):
    subscription: SchoolSubscription
    plan: SubscriptionPlan
    entitlements: List[PlanEntitlement]


class UsageStats(BaseModel):
    school_id: int
    event_key: str
    current_usage: float
    limit: Optional[float] = None
    period_start: datetime
    period_end: datetime


class EntitlementCheck(BaseModel):
    school_id: int
    entitlement_key: str
    has_access: bool
    current_usage: Optional[float] = None
    limit: Optional[float] = None
    reason: Optional[str] = None
