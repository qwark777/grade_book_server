from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException


RESOURCE_LABELS = {
    "students": "учеников",
    "teachers": "учителей",
    "classes": "классов",
}


@dataclass(frozen=True)
class SchoolResourceLimit:
    resource: str
    current: int
    maximum: int | None

    @property
    def remaining(self) -> int | None:
        if self.maximum is None:
            return None
        return max(0, self.maximum - self.current)


def _row_value(row, key: str, index: int, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    return row[index] if index < len(row) else default


def _is_true(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "unlimited"}


async def register_school_user(
    cursor,
    *,
    school_id: int,
    user_id: int,
    role: str,
) -> None:
    await cursor.execute(
        """
        INSERT INTO school_users (school_id, user_id, role)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE role = %s
        """,
        (school_id, user_id, role, role),
    )


async def get_school_resource_limit(
    cursor,
    *,
    school_id: int,
    resource: str,
    lock: bool = False,
) -> SchoolResourceLimit:
    if resource not in RESOURCE_LABELS:
        raise ValueError(f"Unsupported school resource: {resource}")

    lock_clause = " FOR UPDATE" if lock else ""
    await cursor.execute(
        f"""
        SELECT ss.plan_id
        FROM school_subscriptions ss
        WHERE ss.school_id = %s
        {lock_clause}
        """,
        (school_id,),
    )
    subscription = await cursor.fetchone()
    if not subscription:
        raise HTTPException(
            status_code=402,
            detail="У школы нет активной подписки",
        )
    plan_id = int(_row_value(subscription, "plan_id", 0))

    await cursor.execute(
        """
        SELECT entitlement_key, entitlement_value
        FROM plan_entitlements
        WHERE plan_id = %s
          AND entitlement_key IN (%s, %s)
        """,
        (plan_id, f"{resource}.max", f"{resource}.unlimited"),
    )
    entitlements = {}
    for row in await cursor.fetchall():
        key = _row_value(row, "entitlement_key", 0)
        entitlements[key] = _row_value(row, "entitlement_value", 1)

    if _is_true(entitlements.get(f"{resource}.unlimited")):
        maximum = None
    else:
        raw_limit = entitlements.get(f"{resource}.max")
        try:
            maximum = int(raw_limit)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"В тарифе не настроен лимит {RESOURCE_LABELS[resource]}. "
                    "Владелец приложения должен настроить тариф."
                ),
            )
        if maximum < 0:
            raise HTTPException(
                status_code=409,
                detail=f"Некорректный лимит {RESOURCE_LABELS[resource]} в тарифе",
            )

    if resource == "classes":
        await cursor.execute(
            """
            SELECT COUNT(*) AS current_count
            FROM classes
            WHERE school_id = %s AND COALESCE(is_archived, 0) = 0
            """,
            (school_id,),
        )
    else:
        await cursor.execute(
            """
            SELECT COUNT(*) AS current_count
            FROM school_users
            WHERE school_id = %s AND role = %s
            """,
            (school_id, resource[:-1]),
        )
    count_row = await cursor.fetchone()
    current = int(_row_value(count_row, "current_count", 0, 0) or 0)
    return SchoolResourceLimit(
        resource=resource,
        current=current,
        maximum=maximum,
    )


async def ensure_school_capacity(
    cursor,
    *,
    school_id: int,
    resource: str,
    requested: int = 1,
) -> SchoolResourceLimit:
    if requested < 1:
        raise ValueError("requested must be positive")

    limit = await get_school_resource_limit(
        cursor,
        school_id=school_id,
        resource=resource,
        lock=True,
    )
    if limit.maximum is not None and limit.current + requested > limit.maximum:
        label = RESOURCE_LABELS[resource]
        raise HTTPException(
            status_code=409,
            detail=(
                f"Лимит {label} по тарифу исчерпан: "
                f"{limit.current} из {limit.maximum}. "
                "Измените тариф или увеличьте его лимит."
            ),
        )
    return limit


async def ensure_school_user_membership(
    cursor,
    *,
    school_id: int,
    user_id: int,
    role: str,
) -> bool:
    await cursor.execute(
        """
        SELECT 1
        FROM school_users
        WHERE school_id = %s AND user_id = %s
        """,
        (school_id, user_id),
    )
    if await cursor.fetchone():
        return False

    resource = f"{role}s"
    await ensure_school_capacity(
        cursor,
        school_id=school_id,
        resource=resource,
    )
    await register_school_user(
        cursor,
        school_id=school_id,
        user_id=user_id,
        role=role,
    )
    return True


async def consume_school_quota(
    cursor,
    *,
    school_id: int,
    entitlement_key: str,
    user_id: int | None,
    amount: float = 1.0,
    daily: bool = False,
    unlimited_key: str | None = None,
) -> tuple[float, float | None]:
    await cursor.execute(
        """
        SELECT plan_id, current_period_start
        FROM school_subscriptions
        WHERE school_id = %s
        FOR UPDATE
        """,
        (school_id,),
    )
    subscription = await cursor.fetchone()
    if not subscription:
        raise HTTPException(status_code=402, detail="У школы нет подписки")
    plan_id = int(_row_value(subscription, "plan_id", 0))
    period_start = _row_value(subscription, "current_period_start", 1)

    keys = [entitlement_key]
    if unlimited_key:
        keys.append(unlimited_key)
    placeholders = ", ".join(["%s"] * len(keys))
    await cursor.execute(
        f"""
        SELECT entitlement_key, entitlement_value
        FROM plan_entitlements
        WHERE plan_id = %s AND entitlement_key IN ({placeholders})
        """,
        (plan_id, *keys),
    )
    values = {
        _row_value(row, "entitlement_key", 0): _row_value(
            row, "entitlement_value", 1
        )
        for row in await cursor.fetchall()
    }
    if unlimited_key and _is_true(values.get(unlimited_key)):
        return 0, None
    raw_limit = values.get(entitlement_key)
    if str(raw_limit or "").strip().lower() == "unlimited":
        return 0, None
    try:
        maximum = float(raw_limit)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=409,
            detail=f"В тарифе не настроена квота {entitlement_key}",
        )

    if daily:
        await cursor.execute(
            """
            SELECT COALESCE(SUM(event_value), 0) AS current_usage
            FROM usage_events
            WHERE school_id = %s AND event_key = %s AND event_date = CURRENT_DATE
            """,
            (school_id, entitlement_key),
        )
    else:
        await cursor.execute(
            """
            SELECT COALESCE(SUM(event_value), 0) AS current_usage
            FROM usage_events
            WHERE school_id = %s AND event_key = %s AND event_date >= %s
            """,
            (school_id, entitlement_key, period_start),
        )
    usage_row = await cursor.fetchone()
    current = float(_row_value(usage_row, "current_usage", 0, 0) or 0)
    if current + amount > maximum:
        raise HTTPException(
            status_code=409,
            detail=f"Квота {entitlement_key} исчерпана: {current:g} из {maximum:g}",
        )
    await cursor.execute(
        """
        INSERT INTO usage_events
        (school_id, user_id, event_key, event_value, event_date)
        VALUES (%s, %s, %s, %s, CURRENT_DATE)
        """,
        (school_id, user_id, entitlement_key, amount),
    )
    return current + amount, maximum


async def change_school_storage(
    cursor,
    *,
    school_id: int,
    delta_bytes: int,
) -> tuple[int, int | None]:
    await cursor.execute(
        """
        SELECT plan_id
        FROM school_subscriptions
        WHERE school_id = %s
        FOR UPDATE
        """,
        (school_id,),
    )
    subscription = await cursor.fetchone()
    if not subscription:
        raise HTTPException(status_code=402, detail="У школы нет подписки")
    plan_id = int(_row_value(subscription, "plan_id", 0))

    await cursor.execute(
        """
        SELECT entitlement_key, entitlement_value
        FROM plan_entitlements
        WHERE plan_id = %s
          AND entitlement_key IN ('storage.mb', 'storage.unlimited')
        """,
        (plan_id,),
    )
    values = {
        _row_value(row, "entitlement_key", 0): _row_value(
            row, "entitlement_value", 1
        )
        for row in await cursor.fetchall()
    }
    maximum_bytes = None
    if not _is_true(values.get("storage.unlimited")):
        try:
            maximum_bytes = int(float(values.get("storage.mb"))) * 1024 * 1024
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=409,
                detail="В тарифе не настроен лимит хранилища",
            )

    await cursor.execute(
        """
        INSERT IGNORE INTO school_storage_usage (school_id, used_bytes)
        VALUES (%s, 0)
        """,
        (school_id,),
    )
    await cursor.execute(
        """
        SELECT used_bytes
        FROM school_storage_usage
        WHERE school_id = %s
        FOR UPDATE
        """,
        (school_id,),
    )
    usage = await cursor.fetchone()
    current = int(_row_value(usage, "used_bytes", 0, 0) or 0)
    updated = max(0, current + delta_bytes)
    if maximum_bytes is not None and updated > maximum_bytes:
        raise HTTPException(
            status_code=409,
            detail=(
                "Лимит хранилища исчерпан: "
                f"{current / 1024 / 1024:.1f} из "
                f"{maximum_bytes / 1024 / 1024:.0f} МБ"
            ),
        )
    await cursor.execute(
        """
        UPDATE school_storage_usage
        SET used_bytes = %s
        WHERE school_id = %s
        """,
        (updated, school_id),
    )
    return updated, maximum_bytes
