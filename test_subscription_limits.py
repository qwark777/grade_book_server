import unittest

from fastapi import HTTPException

from app.core.subscription_limits import (
    change_school_storage,
    consume_school_quota,
    ensure_school_capacity,
    ensure_school_user_membership,
    get_school_resource_limit,
)


class LimitCursor:
    def __init__(
        self,
        *,
        entitlements=None,
        current=0,
        membership_exists=False,
        quota_usage=0,
        storage_bytes=0,
    ):
        self.entitlements = entitlements or {}
        self.current = current
        self.membership_exists = membership_exists
        self.quota_usage = quota_usage
        self.storage_bytes = storage_bytes
        self.fetchone_value = None
        self.fetchall_value = []
        self.calls = []

    async def execute(self, query, params=None):
        normalized = " ".join(query.split())
        self.calls.append((normalized, params))
        self.fetchone_value = None
        self.fetchall_value = []
        if "FROM school_subscriptions" in normalized:
            self.fetchone_value = {"plan_id": 1}
        elif "FROM plan_entitlements" in normalized:
            self.fetchall_value = [
                {
                    "entitlement_key": key,
                    "entitlement_value": value,
                }
                for key, value in self.entitlements.items()
            ]
        elif "COUNT(*) AS current_count" in normalized:
            self.fetchone_value = {"current_count": self.current}
        elif "SUM(event_value)" in normalized:
            self.fetchone_value = {"current_usage": self.quota_usage}
        elif "SELECT used_bytes FROM school_storage_usage" in normalized:
            self.fetchone_value = {"used_bytes": self.storage_bytes}
        elif "SELECT 1 FROM school_users" in normalized:
            self.fetchone_value = {"exists": 1} if self.membership_exists else None

    async def fetchone(self):
        return self.fetchone_value

    async def fetchall(self):
        return self.fetchall_value


class SubscriptionLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_maximum_blocks_new_student(self):
        cursor = LimitCursor(
            entitlements={"students.max": "100"},
            current=100,
        )

        with self.assertRaises(HTTPException) as raised:
            await ensure_school_capacity(
                cursor,
                school_id=2,
                resource="students",
            )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("100 из 100", raised.exception.detail)

    async def test_unlimited_resource_ignores_numeric_limit(self):
        cursor = LimitCursor(
            entitlements={
                "teachers.max": "5",
                "teachers.unlimited": "true",
            },
            current=500,
        )

        result = await get_school_resource_limit(
            cursor,
            school_id=2,
            resource="teachers",
        )

        self.assertIsNone(result.maximum)
        self.assertEqual(result.current, 500)

    async def test_existing_membership_does_not_consume_another_seat(self):
        cursor = LimitCursor(membership_exists=True)

        created = await ensure_school_user_membership(
            cursor,
            school_id=2,
            user_id=25,
            role="teacher",
        )

        self.assertFalse(created)
        self.assertFalse(
            any("INSERT INTO school_users" in query for query, _ in cursor.calls)
        )

    async def test_missing_limit_is_rejected(self):
        cursor = LimitCursor(current=0)

        with self.assertRaises(HTTPException) as raised:
            await ensure_school_capacity(
                cursor,
                school_id=2,
                resource="classes",
            )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("не настроен лимит", raised.exception.detail)

    async def test_daily_quota_blocks_after_limit(self):
        cursor = LimitCursor(
            entitlements={"messages.daily": "10"},
            quota_usage=10,
        )

        with self.assertRaises(HTTPException) as raised:
            await consume_school_quota(
                cursor,
                school_id=2,
                entitlement_key="messages.daily",
                user_id=1,
                daily=True,
            )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("10 из 10", raised.exception.detail)

    async def test_storage_quota_blocks_oversized_upload(self):
        cursor = LimitCursor(
            entitlements={"storage.mb": "1"},
            storage_bytes=1024 * 1024,
        )

        with self.assertRaises(HTTPException) as raised:
            await change_school_storage(
                cursor,
                school_id=2,
                delta_bytes=1,
            )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("Лимит хранилища", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
