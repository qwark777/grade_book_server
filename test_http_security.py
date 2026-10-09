import os
import unittest
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test_secret_key_123456789012345678901234567890")
os.environ.setdefault("ENCRYPTION_KEY", "ICkoftk-wbOx89vzo2nuGkPatHZCQ1IKBVpFdRJ1F4k=")

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import auth
from app.api import academic_periods
from app.api import admin_extras
from app.api import achievements
from app.api import crm_integrations
from app.api import group_chats
from app.api import main as api_main
from app.api import messages
from app.api import profile
from app.api import subscriptions
from app.api import users
from app.core.config import settings
from app.core import security
from app.core.file_uploads import safe_storage_path
from app.core.subscription_gate import SubscriptionGateResult
from app import main as server_main
from app.main import app
from app.services import startup


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []
        self.lastrowid = 123
        self.rowcount = 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query, params=None):
        self.calls.append((query, params))

    async def fetchone(self):
        if not self.rows:
            return None
        return self.rows.pop(0)

    async def fetchall(self):
        if not self.rows:
            return []
        row = self.rows.pop(0)
        return row if isinstance(row, list) else [row]

    async def close(self):
        pass


class FakeConnection:
    def __init__(self, rows):
        self._cursor = FakeCursor(rows)
        self.began = False

    def cursor(self, *args, **kwargs):
        return self._cursor

    def close(self):
        pass

    async def commit(self):
        pass

    async def begin(self):
        self.began = True

    async def rollback(self):
        pass


class HttpSecurityTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        self._admin_get_school_id = admin_extras._get_school_id
        self._admin_get_db_connection = admin_extras.get_db_connection
        self._achievements_save_upload_limited = achievements.save_upload_limited
        self._auth_get_user = auth.get_user
        self._group_chats_get_db_connection = group_chats.get_db_connection
        self._group_chats_require_school_feature = group_chats.require_school_feature
        self._group_chats_save_upload_limited = group_chats.save_upload_limited
        self._profile_get_db_connection = profile.get_db_connection
        self._profile_save_upload_limited = profile.save_upload_limited
        self._profile_get_school_id_for_user = profile.get_school_id_for_user
        self._api_main_get_school_id_for_user = api_main.get_school_id_for_user
        self._users_get_db_connection = users.get_db_connection
        self._users_get_school_id_for_user = users.get_school_id_for_user
        self._subscriptions_get_db_connection = subscriptions.get_db_connection
        self._server_subscription_gate = server_main.check_school_subscription_gate
        app.dependency_overrides.clear()

    def tearDown(self):
        admin_extras._get_school_id = self._admin_get_school_id
        admin_extras.get_db_connection = self._admin_get_db_connection
        achievements.save_upload_limited = self._achievements_save_upload_limited
        auth.get_user = self._auth_get_user
        group_chats.get_db_connection = self._group_chats_get_db_connection
        group_chats.require_school_feature = self._group_chats_require_school_feature
        group_chats.save_upload_limited = self._group_chats_save_upload_limited
        profile.get_db_connection = self._profile_get_db_connection
        profile.save_upload_limited = self._profile_save_upload_limited
        profile.get_school_id_for_user = self._profile_get_school_id_for_user
        api_main.get_school_id_for_user = self._api_main_get_school_id_for_user
        users.get_db_connection = self._users_get_db_connection
        users.get_school_id_for_user = self._users_get_school_id_for_user
        subscriptions.get_db_connection = self._subscriptions_get_db_connection
        server_main.check_school_subscription_gate = self._server_subscription_gate
        app.dependency_overrides.clear()

    def _as_school_admin(self):
        async def current_user():
            return SimpleNamespace(id=10, username="admin1", role="admin")

        async def school_one(_user):
            return 1

        app.dependency_overrides[security.get_current_user] = current_user
        admin_extras._get_school_id = school_one

    def _as_owner(self):
        async def current_user():
            return SimpleNamespace(id=1, username="owner", role="owner")

        app.dependency_overrides[security.get_current_user] = current_user

    def _as_superadmin(self):
        async def current_user():
            return SimpleNamespace(id=2, username="superadmin", role="superadmin")

        app.dependency_overrides[security.get_current_user] = current_user

    def _as_student(self):
        async def current_user():
            return SimpleNamespace(id=50, username="student", role="student")

        app.dependency_overrides[security.get_current_user] = current_user

    def test_rewards_award_requires_authentication(self):
        response = self.client.post(
            "/api/v1/rewards/award",
            json={"user_id": 1, "points": 10},
        )

        self.assertEqual(response.status_code, 401)

    def test_request_context_headers_are_returned(self):
        response = self.client.get(
            "/api/v1/users/points/leaderboard",
            headers={"X-Request-ID": "test-request-1"},
        )

        self.assertEqual(response.headers["X-Request-ID"], "test-request-1")
        self.assertRegex(response.headers["X-Process-Time"], r"^\d+\.\d{4}$")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Referrer-Policy"], "same-origin")

    def test_static_does_not_expose_source_files(self):
        response = self.client.get("/static/app/core/config.py")

        self.assertEqual(response.status_code, 404)

    def test_static_serves_achievement_images_only(self):
        response = self.client.get("/static/achievements/first_place.png")

        self.assertEqual(response.status_code, 200)

    def test_blocked_school_subscription_returns_payment_required(self):
        async def blocked_gate(_request):
            return SubscriptionGateResult(
                blocked=True,
                message="Сервис недоступен",
                school_id=10,
                reason="past_due",
            )

        server_main.check_school_subscription_gate = blocked_gate

        response = self.client.get("/api/v1/users/all")

        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.json()["detail"], "Сервис недоступен")
        self.assertEqual(response.json()["error"], "subscription_required")
        self.assertEqual(response.headers["X-Subscription-Status"], "blocked")
        self.assertEqual(response.headers["X-Subscription-Reason"], "past_due")

    def test_subscription_grace_adds_warning_headers(self):
        async def warning_gate(_request):
            return SubscriptionGateResult(
                warning=True,
                message="Подписка школы не оплачена",
                school_id=10,
                days_left=4,
                reason="past_due_in_grace",
            )

        server_main.check_school_subscription_gate = warning_gate

        response = self.client.get("/api/v1/users/all")

        self.assertNotEqual(response.status_code, 402)
        self.assertEqual(response.headers["X-Subscription-Status"], "warning")
        self.assertEqual(
            response.headers["X-Subscription-Message"],
            "%D0%9F%D0%BE%D0%B4%D0%BF%D0%B8%D1%81%D0%BA%D0%B0%20%D1%88%D0%BA%D0%BE%D0%BB%D1%8B%20%D0%BD%D0%B5%20%D0%BE%D0%BF%D0%BB%D0%B0%D1%87%D0%B5%D0%BD%D0%B0",
        )
        self.assertEqual(response.headers["X-Subscription-Days-Left"], "4")

    def test_flutter_critical_routes_are_registered(self):
        registered = {
            (method, getattr(route, "path", ""))
            for route in app.routes
            for method in getattr(route, "methods", set())
        }

        expected = {
            ("GET", "/api/v1/lessons/export/1c/xml"),
            ("GET", "/api/v1/lessons/export/1c/json"),
            ("GET", "/api/v1/lessons/enrollments/export/1c/json"),
            ("GET", "/api/v1/lessons/financial/export/1c/json"),
            ("GET", "/api/v1/owner/analytics/financial-stats"),
        }

        self.assertTrue(expected.issubset(registered))

    def test_profile_photos_are_not_rate_limit_exempt(self):
        request = SimpleNamespace(
            method="GET",
            url=SimpleNamespace(path="/api/v1/profile_photo/50"),
        )

        self.assertFalse(server_main._should_skip_rate_limit(request))

    def test_profile_photo_upload_returns_api_v1_url(self):
        class FakeUpload:
            filename = "avatar.png"
            content_type = "image/png"

            async def read(self, _size):
                return b""

            async def close(self):
                pass

        async def fake_save(*_args, **_kwargs):
            return 0

        async def fake_db():
            return FakeConnection([])

        async def no_school(_user):
            return None

        profile.get_db_connection = fake_db
        profile.save_upload_limited = fake_save
        profile.get_school_id_for_user = no_school

        with patch.object(profile, "validate_avatar_dimensions"):
            result = __import__("asyncio").run(
                profile.upload_photo(
                    file=FakeUpload(),
                    current_user=SimpleNamespace(
                        id=12,
                        username="ivan",
                        role="student",
                    ),
                )
            )

        self.assertEqual(result["photo_url"], "/api/v1/profile_photo/12")

    def test_group_photo_upload_returns_existing_api_url(self):
        class FakeUpload:
            filename = "group.png"
            content_type = "image/png"

            async def read(self, _size):
                return b""

            async def close(self):
                pass

        async def no_feature_check(*_args, **_kwargs):
            return None

        async def fake_save(*_args, **_kwargs):
            return 0

        async def fake_db():
            return FakeConnection([{"role": "admin"}])

        group_chats.require_school_feature = no_feature_check
        group_chats.save_upload_limited = fake_save
        group_chats.get_db_connection = fake_db

        result = __import__("asyncio").run(
            group_chats.upload_group_photo(
                7,
                file=FakeUpload(),
                current_user=SimpleNamespace(id=10, username="admin", role="admin"),
            )
        )

        self.assertEqual(result["photo_url"], "/api/v1/group-chats/7/photo")

    def test_root_can_upload_achievement_photo_with_unique_fallback_name(self):
        class FakeUpload:
            filename = "badge.png"
            content_type = "image/png"

            async def read(self, _size):
                return b""

            async def close(self):
                pass

        async def fake_save(*_args, **_kwargs):
            return 0

        achievements.save_upload_limited = fake_save

        result = __import__("asyncio").run(
            achievements.upload_achievement_photo(
                file=FakeUpload(),
                achievement_code=None,
                current_user=SimpleNamespace(id=1, username="root", role="root"),
            )
        )

        self.assertTrue(result["filename"].startswith("badge-"))
        self.assertTrue(result["filename"].endswith(".png"))
        self.assertEqual(result["image_url"], f"/static/achievements/{result['filename']}")

    def test_root_can_manage_academic_periods(self):
        academic_periods._check_admin(
            SimpleNamespace(id=1, username="root", role="root")
        )

    def test_crm_api_url_rejects_loopback_hosts(self):
        with self.assertRaises(HTTPException) as raised:
            crm_integrations._validate_api_url("http://127.0.0.1:8001/internal")

        self.assertEqual(raised.exception.status_code, 400)

    def test_crm_webhook_url_rejects_localhost(self):
        with self.assertRaises(HTTPException) as raised:
            crm_integrations._validate_webhook_url("http://localhost/webhook")

        self.assertEqual(raised.exception.status_code, 400)

    def test_safe_storage_path_rejects_parent_directory_escape(self):
        with self.assertRaises(HTTPException):
            safe_storage_path(settings.HOMEWORK_ATTACHMENTS_DIR, "../app/core/config.py")

    def test_safe_storage_path_rejects_absolute_path(self):
        with self.assertRaises(HTTPException):
            safe_storage_path(settings.HOMEWORK_ATTACHMENTS_DIR, "/etc/passwd")

    def test_safe_storage_path_allows_relative_child_path(self):
        result = safe_storage_path(settings.HOMEWORK_ATTACHMENTS_DIR, "1/file.pdf")

        self.assertTrue(result.startswith(settings.HOMEWORK_ATTACHMENTS_DIR))
        self.assertTrue(result.endswith("1/file.pdf"))

    def test_message_attachment_rejects_query_token_without_auth_header(self):
        response = self.client.get("/api/v1/messages/1/attachment/file?token=leaky")

        self.assertEqual(response.status_code, 401)

    def test_placeholder_owner_password_is_unsafe(self):
        self.assertTrue(
            startup._is_unsafe_initial_owner_password(
                "change_me_to_a_long_random_value"
            )
        )
        self.assertTrue(startup._is_unsafe_initial_owner_password("short"))
        self.assertFalse(
            startup._is_unsafe_initial_owner_password(
                "K2k8-vhCx7pn-KM8q-Yd8q-4pM8b"
            )
        )

    def test_public_registration_is_disabled_by_default(self):
        old_value = settings.ALLOW_PUBLIC_REGISTRATION
        settings.ALLOW_PUBLIC_REGISTRATION = False
        try:
            response = self.client.post(
                "/api/v1/register",
                json={"username": "outside-user", "password": "StrongPass123!"},
            )
        finally:
            settings.ALLOW_PUBLIC_REGISTRATION = old_value

        self.assertEqual(response.status_code, 403)

    def test_public_registration_rejects_weak_password_when_enabled(self):
        old_value = settings.ALLOW_PUBLIC_REGISTRATION
        settings.ALLOW_PUBLIC_REGISTRATION = True
        try:
            response = self.client.post(
                "/api/v1/register",
                json={"username": "outside-user", "password": "12345678"},
            )
        finally:
            settings.ALLOW_PUBLIC_REGISTRATION = old_value

        self.assertEqual(response.status_code, 400)

    def test_change_password_rejects_common_password(self):
        async def current_user():
            return SimpleNamespace(id=50, username="student123", role="student")

        async def fake_get_user(_username):
            return SimpleNamespace(
                id=50,
                username="student123",
                role="student",
                hashed_password=security.get_password_hash("OldStrong123!"),
            )

        app.dependency_overrides[security.get_current_user] = current_user
        auth.get_user = fake_get_user

        response = self.client.post(
            "/api/v1/change-password",
            data={"old_password": "OldStrong123!", "new_password": "12345678"},
        )

        self.assertEqual(response.status_code, 400)

    def test_change_password_rejects_same_password(self):
        async def current_user():
            return SimpleNamespace(id=50, username="student123", role="student")

        async def fake_get_user(_username):
            return SimpleNamespace(
                id=50,
                username="student123",
                role="student",
                hashed_password=security.get_password_hash("OldStrong123!"),
            )

        app.dependency_overrides[security.get_current_user] = current_user
        auth.get_user = fake_get_user

        response = self.client.post(
            "/api/v1/change-password",
            data={"old_password": "OldStrong123!", "new_password": "OldStrong123!"},
        )

        self.assertEqual(response.status_code, 400)

    def test_owner_create_admin_rejects_weak_password(self):
        self._as_owner()

        response = self.client.post(
            "/api/v1/owners/create-admin",
            data={
                "username": "school-admin",
                "password": "12345678",
                "school_id": "1",
            },
        )

        self.assertEqual(response.status_code, 400)

    def test_parent_create_rejects_weak_password(self):
        self._as_school_admin()

        response = self.client.post(
            "/api/v1/parents/create",
            json={
                "username": "parent1",
                "password": "password",
                "full_name": "Родитель",
                "student_ids": [],
            },
        )

        self.assertEqual(response.status_code, 400)

    def test_admin_password_validator_rejects_username_password(self):
        with self.assertRaises(HTTPException):
            users._validate_admin_password("school-admin", "school-admin")

    def test_student_cannot_read_user_from_other_school(self):
        self._as_student()

        async def school_one(_user):
            return 1

        async def fake_db():
            return FakeConnection(
                [
                    {"role": "student"},
                    [{"school_id": 2}],
                ]
            )

        users.get_school_id_for_user = school_one
        users.get_db_connection = fake_db

        response = self.client.get("/api/v1/users/77")

        self.assertEqual(response.status_code, 403)

    def test_student_can_read_application_owner_public_profile(self):
        self._as_student()

        async def fake_db():
            return FakeConnection(
                [
                    {"role": "owner"},
                    {
                        "id": 1,
                        "full_name": "Владелец",
                        "work_place": None,
                        "school_name": None,
                        "location": None,
                        "class_name": None,
                        "subject": None,
                        "classes": None,
                        "username": "owner",
                        "password": None,
                        "photo_url": "/api/v1/profile_photo/1",
                    },
                ]
            )

        users.get_db_connection = fake_db

        response = self.client.get("/api/v1/users/1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["role"], "owner")
        self.assertEqual(response.json()["full_name"], "Владелец")

    def test_admin_create_student_persists_selected_class(self):
        connection = FakeConnection(
            [
                {"exists": 1},
                None,
                {"id": 77},
            ]
        )

        async def fake_db():
            return connection

        async def school_one(_user):
            return 1

        async def no_capacity_check(*_args, **_kwargs):
            return None

        async def no_membership_write(*_args, **_kwargs):
            return None

        original_capacity = users.ensure_school_capacity
        original_membership = users.register_school_user
        users.get_db_connection = fake_db
        users.get_school_id_for_user = school_one
        users.ensure_school_capacity = no_capacity_check
        users.register_school_user = no_membership_write
        try:
            result = __import__("asyncio").run(
                users.admin_create_student(
                    full_name="Тест Ученик",
                    username="student77",
                    password="StrongPassword77!",
                    email="student77@example.com",
                    phone=None,
                    class_id=42,
                    note=None,
                    school_id=None,
                    current_user=SimpleNamespace(id=10, role="admin"),
                )
            )
        finally:
            users.ensure_school_capacity = original_capacity
            users.register_school_user = original_membership

        self.assertEqual(result["student_id"], 77)
        self.assertTrue(
            any(
                "INSERT IGNORE INTO class_students" in query
                and params == (42, 77)
                for query, params in connection._cursor.calls
            )
        )

    def test_mark_message_read_broadcasts_receipt_to_sender(self):
        connection = FakeConnection(
            [
                {
                    "id": 55,
                    "sender_id": 10,
                    "conversation_id": 7,
                    "user1_id": 10,
                    "user2_id": 20,
                }
            ]
        )
        events = []

        async def fake_db():
            return connection

        async def allow_feature(*_args, **_kwargs):
            return None

        async def capture_event(user_id, payload):
            events.append((user_id, payload))

        original_db = messages.get_db_connection
        original_feature = messages.require_school_feature
        original_send = messages.ws_manager.send_to_user
        messages.get_db_connection = fake_db
        messages.require_school_feature = allow_feature
        messages.ws_manager.send_to_user = capture_event
        try:
            result = __import__("asyncio").run(
                messages.mark_message_as_read(
                    55,
                    current_user=SimpleNamespace(id=20, role="student"),
                )
            )
        finally:
            messages.get_db_connection = original_db
            messages.require_school_feature = original_feature
            messages.ws_manager.send_to_user = original_send

        self.assertEqual(result["status"], "ok")
        self.assertEqual(events[0][0], 10)
        self.assertEqual(events[0][1]["type"], "messages_read")
        self.assertEqual(
            events[0][1]["data"]["last_read_message_id"],
            55,
        )

    def test_user_without_school_cannot_list_global_users(self):
        self._as_student()

        async def no_school(_user):
            return None

        users.get_school_id_for_user = no_school

        response = self.client.get(
            "/api/v1/users/all",
            params={"include": "", "role": "student"},
        )

        self.assertEqual(response.status_code, 403)

    def test_non_admin_cannot_award_points_over_http(self):
        async def student_user():
            return SimpleNamespace(id=50, username="student", role="student")

        app.dependency_overrides[security.get_current_user] = student_user

        response = self.client.post(
            "/api/v1/rewards/award",
            json={"user_id": 50, "points": 10},
        )

        self.assertEqual(response.status_code, 403)

    def test_points_leaderboard_requires_authentication(self):
        response = self.client.get("/api/v1/users/points/leaderboard")

        self.assertEqual(response.status_code, 401)

    def test_student_cannot_read_points_leaderboard(self):
        async def student_user():
            return SimpleNamespace(id=50, username="student", role="student")

        app.dependency_overrides[security.get_current_user] = student_user

        response = self.client.get("/api/v1/users/points/leaderboard")

        self.assertEqual(response.status_code, 403)

    def test_school_admin_without_school_cannot_read_global_points_leaderboard(self):
        self._as_school_admin()

        async def no_school(_user):
            return None

        api_main.get_school_id_for_user = no_school

        response = self.client.get("/api/v1/users/points/leaderboard")

        self.assertEqual(response.status_code, 403)

    def test_payment_webhook_requires_configured_provider(self):
        old_enabled = settings.PAYMENT_PROVIDER_ENABLED
        old_provider = settings.PAYMENT_PROVIDER
        old_secret = settings.PAYMENT_WEBHOOK_SECRET
        settings.PAYMENT_PROVIDER_ENABLED = False
        settings.PAYMENT_PROVIDER = "none"
        settings.PAYMENT_WEBHOOK_SECRET = ""
        try:
            response = self.client.post(
                "/api/v1/subscriptions/payment-provider/webhook",
                json={"status": "paid", "invoice_id": 1},
            )
        finally:
            settings.PAYMENT_PROVIDER_ENABLED = old_enabled
            settings.PAYMENT_PROVIDER = old_provider
            settings.PAYMENT_WEBHOOK_SECRET = old_secret

        self.assertEqual(response.status_code, 503)

    def test_payment_webhook_rejects_invalid_secret(self):
        old_enabled = settings.PAYMENT_PROVIDER_ENABLED
        old_provider = settings.PAYMENT_PROVIDER
        old_secret = settings.PAYMENT_WEBHOOK_SECRET
        settings.PAYMENT_PROVIDER_ENABLED = True
        settings.PAYMENT_PROVIDER = "generic_hmac"
        settings.PAYMENT_WEBHOOK_SECRET = "correct-secret"
        try:
            response = self.client.post(
                "/api/v1/subscriptions/payment-provider/webhook",
                headers={"X-Payment-Webhook-Secret": "wrong-secret"},
                json={"status": "paid", "invoice_id": 1},
            )
        finally:
            settings.PAYMENT_PROVIDER_ENABLED = old_enabled
            settings.PAYMENT_PROVIDER = old_provider
            settings.PAYMENT_WEBHOOK_SECRET = old_secret

        self.assertEqual(response.status_code, 401)

    def test_signed_payment_webhook_activates_invoice(self):
        self._as_owner()
        old_enabled = settings.PAYMENT_PROVIDER_ENABLED
        old_provider = settings.PAYMENT_PROVIDER
        old_secret = settings.PAYMENT_WEBHOOK_SECRET
        settings.PAYMENT_PROVIDER_ENABLED = True
        settings.PAYMENT_PROVIDER = "generic_hmac"
        settings.PAYMENT_WEBHOOK_SECRET = "correct-secret"
        period_start = datetime.now()
        period_end = period_start + timedelta(days=30)
        conn = FakeConnection([
            {
                "id": 5,
                "school_id": 1,
                "plan_id": 2,
                "status": "pending",
                "amount": 1000,
                "currency": "RUB",
                "period_start": period_start,
                "period_end": period_end,
                "created_at": datetime.now(),
            },
            None,
        ])

        async def fake_db():
            return conn

        subscriptions.get_db_connection = fake_db
        payload = {
            "status": "paid",
            "invoice_id": 5,
            "amount": 1000,
            "currency": "RUB",
        }
        raw_body = json.dumps(payload, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        signature = hmac.new(
            settings.PAYMENT_WEBHOOK_SECRET.encode(),
            f"{timestamp}.".encode() + raw_body,
            hashlib.sha256,
        ).hexdigest()
        try:
            response = self.client.post(
                "/api/v1/subscriptions/payment-provider/webhook",
                content=raw_body,
                headers={
                    "Content-Type": "application/json",
                    "X-Payment-Timestamp": timestamp,
                    "X-Payment-Signature": signature,
                    "X-Payment-Event-ID": "evt-1",
                },
            )
        finally:
            settings.PAYMENT_PROVIDER_ENABLED = old_enabled
            settings.PAYMENT_PROVIDER = old_provider
            settings.PAYMENT_WEBHOOK_SECRET = old_secret

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            any(
                "INSERT INTO school_subscriptions" in query
                for query, _ in conn._cursor.calls
            )
        )

    def test_school_admin_cannot_list_pending_subscription_invoices(self):
        self._as_school_admin()

        response = self.client.get("/api/v1/subscriptions/invoices/pending")

        self.assertEqual(response.status_code, 403)

    def test_school_admin_cannot_list_school_subscription_invoices(self):
        self._as_school_admin()

        response = self.client.get("/api/v1/subscriptions/schools/1/invoices")

        self.assertEqual(response.status_code, 403)

    def test_school_admin_cannot_confirm_subscription_invoice(self):
        self._as_school_admin()

        response = self.client.patch(
            "/api/v1/subscriptions/schools/1/invoices/5",
            json={
                "status": "paid",
                "payment_method": "bank_transfer",
                "payment_note": "Платежное поручение 123",
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_school_admin_cannot_create_school_subscription(self):
        self._as_school_admin()

        response = self.client.post(
            "/api/v1/subscriptions/schools/1/subscription",
            json={"plan_id": 1, "status": "active", "days": 30},
        )

        self.assertEqual(response.status_code, 403)

    def test_student_cannot_read_school_subscription(self):
        self._as_student()

        response = self.client.get("/api/v1/subscriptions/schools/1/subscription")

        self.assertEqual(response.status_code, 403)

    def test_student_cannot_read_subscription_usage(self):
        self._as_student()

        response = self.client.get("/api/v1/subscriptions/schools/1/usage")

        self.assertEqual(response.status_code, 403)

    def test_student_cannot_record_subscription_usage(self):
        self._as_student()

        response = self.client.post(
            "/api/v1/subscriptions/schools/1/usage",
            params={"event_key": "students", "event_value": 999},
        )

        self.assertEqual(response.status_code, 403)

    def test_student_cannot_check_school_entitlement(self):
        self._as_student()

        response = self.client.get(
            "/api/v1/subscriptions/schools/1/entitlements/check",
            params={"entitlement_key": "analytics.full"},
        )

        self.assertEqual(response.status_code, 403)

    def test_pending_subscription_invoice_does_not_activate_access(self):
        self._as_owner()
        conn = FakeConnection([
            {"id": 1},
            {
                "id": 2,
                "price_monthly": 1000,
                "price_yearly": 10000,
                "currency": "RUB",
                "is_active": True,
            },
            None,
        ])

        async def fake_db():
            return conn

        subscriptions.get_db_connection = fake_db

        response = self.client.post(
            "/api/v1/subscriptions/schools/1/subscription",
            json={
                "plan_id": 2,
                "status": "active",
                "days": 30,
                "create_invoice": True,
                "invoice_status": "pending",
                "payment_method": "bank_transfer",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(conn.began)
        self.assertFalse(
            any(
                "INSERT INTO school_subscriptions" in query
                for query, _ in conn._cursor.calls
            )
        )
        invoice_insert = next(
            call
            for call in conn._cursor.calls
            if "INSERT INTO subscription_invoices" in call[0]
        )
        self.assertEqual(invoice_insert[1][2], 1000)

    def test_paid_manual_subscription_invoice_activates_access(self):
        self._as_owner()
        conn = FakeConnection([
            {"id": 1},
            {
                "id": 2,
                "price_monthly": 1000,
                "price_yearly": 10000,
                "currency": "RUB",
                "is_active": True,
            },
            None,
            None,
        ])

        async def fake_db():
            return conn

        subscriptions.get_db_connection = fake_db

        response = self.client.post(
            "/api/v1/subscriptions/schools/1/subscription",
            json={
                "plan_id": 2,
                "status": "past_due",
                "days": 30,
                "create_invoice": True,
                "invoice_status": "paid",
                "payment_method": "bank_transfer",
                "payment_note": "Платежное поручение 123",
            },
        )

        self.assertEqual(response.status_code, 200)
        subscription_insert = next(
            call for call in conn._cursor.calls
            if "INSERT INTO school_subscriptions" in call[0]
        )
        self.assertEqual(subscription_insert[1][2], "active")

    def test_annual_subscription_invoice_uses_yearly_price(self):
        self._as_owner()
        conn = FakeConnection([
            {"id": 1},
            {
                "id": 2,
                "price_monthly": 1000,
                "price_yearly": 10000,
                "currency": "RUB",
                "is_active": True,
            },
            None,
        ])

        async def fake_db():
            return conn

        subscriptions.get_db_connection = fake_db
        response = self.client.post(
            "/api/v1/subscriptions/schools/1/subscription",
            json={
                "plan_id": 2,
                "status": "active",
                "days": 365,
                "create_invoice": True,
                "invoice_status": "pending",
                "payment_method": "bank_transfer",
            },
        )

        self.assertEqual(response.status_code, 200)
        invoice_insert = next(
            call
            for call in conn._cursor.calls
            if "INSERT INTO subscription_invoices" in call[0]
        )
        self.assertEqual(invoice_insert[1][2], 10000)

    def test_pending_renewal_starts_after_current_period(self):
        self._as_owner()
        current_end = datetime.now() + timedelta(days=14)
        conn = FakeConnection([
            {"id": 1},
            {
                "id": 2,
                "price_monthly": 1000,
                "price_yearly": 10000,
                "currency": "RUB",
                "is_active": True,
            },
            {
                "plan_id": 2,
                "status": "active",
                "current_period_start": datetime.now() - timedelta(days=16),
                "current_period_end": current_end,
            },
        ])

        async def fake_db():
            return conn

        subscriptions.get_db_connection = fake_db
        response = self.client.post(
            "/api/v1/subscriptions/schools/1/subscription",
            json={
                "plan_id": 2,
                "status": "active",
                "days": 30,
                "create_invoice": True,
                "invoice_status": "pending",
                "payment_method": "bank_transfer",
            },
        )

        self.assertEqual(response.status_code, 200)
        invoice_insert = next(
            call
            for call in conn._cursor.calls
            if "INSERT INTO subscription_invoices" in call[0]
        )
        self.assertEqual(invoice_insert[1][5], current_end)
        self.assertEqual(invoice_insert[1][6], current_end + timedelta(days=30))
        self.assertFalse(
            any(
                "INSERT INTO school_subscriptions" in query
                for query, _ in conn._cursor.calls
            )
        )

    def test_inactive_plan_cannot_be_assigned(self):
        self._as_owner()
        conn = FakeConnection([
            {"id": 1},
            {
                "id": 2,
                "price_monthly": 1000,
                "price_yearly": 10000,
                "currency": "RUB",
                "is_active": False,
            },
        ])

        async def fake_db():
            return conn

        subscriptions.get_db_connection = fake_db
        response = self.client.post(
            "/api/v1/subscriptions/schools/1/subscription",
            json={
                "plan_id": 2,
                "status": "active",
                "days": 30,
            },
        )

        self.assertEqual(response.status_code, 409)

    def test_paid_invoice_cannot_return_to_pending(self):
        with self.assertRaises(HTTPException) as raised:
            subscriptions._validate_invoice_transition("paid", "pending")

        self.assertEqual(raised.exception.status_code, 409)

    def test_repeated_invoice_status_is_idempotent(self):
        subscriptions._validate_invoice_transition("paid", "paid")

    def test_stale_invoice_cannot_replace_newer_plan(self):
        self._as_owner()
        now = datetime.now()
        conn = FakeConnection([
            {
                "id": 5,
                "school_id": 1,
                "plan_id": 2,
                "status": "pending",
                "amount": 1000,
                "currency": "RUB",
                "period_start": now - timedelta(days=60),
                "period_end": now - timedelta(days=30),
                "created_at": now - timedelta(days=60),
            },
            {
                "plan_id": 3,
                "status": "active",
                "current_period_start": now - timedelta(days=10),
                "current_period_end": now + timedelta(days=20),
                "updated_at": now - timedelta(days=10),
            },
        ])

        async def fake_db():
            return conn

        subscriptions.get_db_connection = fake_db
        response = self.client.patch(
            "/api/v1/subscriptions/schools/1/invoices/5",
            json={
                "status": "paid",
                "payment_method": "bank_transfer",
                "payment_note": "Платёж найден позднее",
            },
        )

        self.assertEqual(response.status_code, 409)

    def test_refund_marks_matching_subscription_past_due(self):
        self._as_owner()
        now = datetime.now()
        conn = FakeConnection([
            {
                "id": 5,
                "school_id": 1,
                "plan_id": 2,
                "status": "paid",
                "amount": 1000,
                "currency": "RUB",
                "period_start": now - timedelta(days=10),
                "period_end": now + timedelta(days=20),
                "created_at": now - timedelta(days=10),
            },
        ])

        async def fake_db():
            return conn

        subscriptions.get_db_connection = fake_db
        response = self.client.patch(
            "/api/v1/subscriptions/schools/1/invoices/5",
            json={
                "status": "refunded",
                "payment_method": "bank_transfer",
                "payment_note": "Возврат по заявлению школы",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            any(
                "UPDATE school_subscriptions" in query
                and "current_period_end <= %s" in query
                for query, _ in conn._cursor.calls
            )
        )

    def test_superadmin_can_read_any_school_subscription(self):
        self._as_superadmin()

        async def fake_db():
            return FakeConnection([
                {
                    "id": 9,
                    "school_id": 77,
                    "plan_id": 2,
                    "status": "active",
                    "current_period_start": "2026-06-01T00:00:00",
                    "current_period_end": "2026-07-01T00:00:00",
                    "seats_students": 0,
                    "seats_teachers": 0,
                    "created_at": "2026-06-01T00:00:00",
                    "updated_at": "2026-06-01T00:00:00",
                },
                {
                    "id": 2,
                    "name": "Base",
                    "description": "Basic plan",
                    "price_monthly": 1000,
                    "price_yearly": 10000,
                    "currency": "RUB",
                    "is_active": True,
                    "created_at": "2026-06-01T00:00:00",
                },
                [],
            ])

        subscriptions.get_db_connection = fake_db

        response = self.client.get("/api/v1/subscriptions/schools/77/subscription")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["subscription"]["school_id"], 77)

    def test_owner_can_list_pending_subscription_invoices(self):
        self._as_owner()

        async def fake_db():
            return FakeConnection([
                [
                    {
                        "id": 5,
                        "school_id": 1,
                        "school_name": "School One",
                        "plan_id": 2,
                        "plan_name": "Base",
                        "amount": 1000,
                        "currency": "RUB",
                        "status": "pending",
                        "period_start": "2026-06-01T00:00:00",
                        "period_end": "2026-07-01T00:00:00",
                        "due_date": "2026-06-08T00:00:00",
                        "payment_method": "bank_transfer",
                        "provider_payment_id": None,
                        "payment_note": None,
                        "confirmed_by_user_id": None,
                        "confirmed_by_username": None,
                        "confirmed_by_full_name": None,
                        "paid_at": None,
                        "created_at": "2026-06-01T00:00:00",
                    }
                ]
            ])

        subscriptions.get_db_connection = fake_db

        response = self.client.get("/api/v1/subscriptions/invoices/pending")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["school_name"], "School One")

    def test_school_admin_cannot_list_subscription_issues(self):
        self._as_school_admin()

        response = self.client.get(
            "/api/v1/subscriptions/schools/subscription-issues"
        )

        self.assertEqual(response.status_code, 403)

    def test_owner_can_list_subscription_issues(self):
        self._as_owner()

        async def fake_db():
            return FakeConnection([
                [
                    {
                        "school_id": 1,
                        "school_name": "School One",
                        "subscription_status": None,
                        "current_period_end": None,
                        "plan_name": None,
                        "pending_amount": 0,
                        "nearest_due_date": None,
                        "pending_invoices": 0,
                    },
                    {
                        "school_id": 2,
                        "school_name": "School Two",
                        "subscription_status": "active",
                        "current_period_end": (
                            datetime.now(timezone.utc) + timedelta(days=30)
                        ).isoformat(),
                        "plan_name": "Base",
                        "pending_amount": 1000,
                        "nearest_due_date": "2026-06-25T00:00:00",
                        "pending_invoices": 1,
                    },
                ]
            ])

        subscriptions.get_db_connection = fake_db

        response = self.client.get(
            "/api/v1/subscriptions/schools/subscription-issues"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["issue_type"], "no_subscription")
        self.assertEqual(response.json()[1]["issue_type"], "pending_invoice")

    def test_school_admin_cannot_create_subscription_plan(self):
        self._as_school_admin()

        response = self.client.post(
            "/api/v1/subscriptions/plans",
            json={
                "name": "Base",
                "price_monthly": 1000,
                "price_yearly": 10000,
                "currency": "RUB",
                "is_active": True,
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_school_admin_cannot_update_subscription_plan(self):
        self._as_school_admin()

        response = self.client.patch(
            "/api/v1/subscriptions/plans/1",
            json={
                "name": "Base",
                "price_monthly": 1000,
                "price_yearly": 10000,
                "currency": "RUB",
                "is_active": True,
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_subscription_plan_detail_requires_authentication(self):
        response = self.client.get("/api/v1/subscriptions/plans/1")

        self.assertEqual(response.status_code, 401)

    def test_school_admin_cannot_read_plan_entitlements(self):
        self._as_school_admin()

        response = self.client.get("/api/v1/subscriptions/plans/1/entitlements")

        self.assertEqual(response.status_code, 403)

    def test_owner_can_create_subscription_plan(self):
        self._as_owner()

        async def fake_db():
            return FakeConnection([
                None,
                {
                    "id": 7,
                    "name": "Base",
                    "description": "Basic plan",
                    "price_monthly": 1000,
                    "price_yearly": 10000,
                    "currency": "RUB",
                    "is_active": True,
                    "created_at": "2026-06-01T00:00:00",
                }
            ])

        subscriptions.get_db_connection = fake_db

        response = self.client.post(
            "/api/v1/subscriptions/plans",
            json={
                "name": "Base",
                "description": "Basic plan",
                "price_monthly": 1000,
                "price_yearly": 10000,
                "currency": "RUB",
                "is_active": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], 7)

    def test_school_admin_cannot_manage_plan_entitlements(self):
        self._as_school_admin()

        response = self.client.post(
            "/api/v1/subscriptions/plans/1/entitlements",
            json={"entitlement_key": "analytics.full", "entitlement_value": "true"},
        )

        self.assertEqual(response.status_code, 403)

    def test_owner_can_upsert_plan_entitlement(self):
        self._as_owner()

        async def fake_db():
            return FakeConnection([
                {"id": 1},
                {
                    "id": 9,
                    "plan_id": 1,
                    "entitlement_key": "analytics.full",
                    "entitlement_value": "true",
                    "created_at": "2026-06-01T00:00:00",
                },
            ])

        subscriptions.get_db_connection = fake_db

        response = self.client.post(
            "/api/v1/subscriptions/plans/1/entitlements",
            json={"entitlement_key": "analytics.full", "entitlement_value": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["entitlement_key"], "analytics.full")

    def test_owner_cannot_set_negative_plan_limit(self):
        self._as_owner()

        response = self.client.post(
            "/api/v1/subscriptions/plans/1/entitlements",
            json={"entitlement_key": "students.max", "entitlement_value": "-1"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("non-negative integer", response.json()["detail"])

    def test_school_admin_cannot_update_plan_limits(self):
        self._as_school_admin()

        response = self.client.put(
            "/api/v1/subscriptions/plans/1/limits",
            json={
                "students_max": 100,
                "students_unlimited": False,
                "teachers_max": 5,
                "teachers_unlimited": False,
                "classes_max": 10,
                "classes_unlimited": False,
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_owner_cannot_set_negative_atomic_plan_limit(self):
        self._as_owner()

        response = self.client.put(
            "/api/v1/subscriptions/plans/1/limits",
            json={
                "students_max": -1,
                "students_unlimited": False,
                "teachers_max": 5,
                "teachers_unlimited": False,
                "classes_max": 10,
                "classes_unlimited": False,
            },
        )

        self.assertEqual(response.status_code, 400)

    def test_owner_can_delete_plan_entitlement(self):
        self._as_owner()

        async def fake_db():
            return FakeConnection([])

        subscriptions.get_db_connection = fake_db

        response = self.client.delete(
            "/api/v1/subscriptions/plans/1/entitlements/analytics.full"
        )

        self.assertEqual(response.status_code, 200)

    def test_school_admin_cannot_fetch_foreign_class_print_list(self):
        self._as_school_admin()

        async def fake_db():
            return FakeConnection([None])

        admin_extras.get_db_connection = fake_db

        response = self.client.get("/api/v1/admin/classes/200/print-list")

        self.assertEqual(response.status_code, 404)

    def test_school_admin_cannot_create_announcement_for_foreign_class(self):
        self._as_school_admin()

        async def fake_db():
            return FakeConnection([None])

        admin_extras.get_db_connection = fake_db

        response = self.client.post(
            "/api/v1/admin/announcements",
            json={
                "title": "Foreign class",
                "content": "Nope",
                "target_type": "class",
                "target_class_id": 200,
            },
        )

        self.assertEqual(response.status_code, 404)

    def test_school_admin_can_fetch_own_class_print_list(self):
        self._as_school_admin()

        async def fake_db():
            return FakeConnection([
                {"school_id": 1},
                {"class_name": "1A"},
                [],
            ])

        admin_extras.get_db_connection = fake_db

        response = self.client.get("/api/v1/admin/classes/100/print-list")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"class_name": "1A", "students": []})

    def test_grade_report_accepts_tuple_rows(self):
        self._as_school_admin()

        async def fake_db():
            return FakeConnection([
                (1,),
                ("1A",),
                [(5, "Math")],
                [(20, "Alice", 5, "Math", 5, "2026-06-01")],
            ])

        admin_extras.get_db_connection = fake_db

        response = self.client.get("/api/v1/admin/classes/100/grade-report")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["subjects"], [{"id": 5, "name": "Math"}])
        self.assertEqual(response.json()["students"][0]["grades"][0]["value"], 5)

    def test_attendance_summary_accepts_tuple_rows(self):
        self._as_school_admin()

        async def fake_db():
            return FakeConnection([
                (1,),
                [(20, 100, "1A", "Alice", 2, 1, 1)],
            ])

        admin_extras.get_db_connection = fake_db

        response = self.client.get("/api/v1/admin/attendance-summary?class_id=100")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["rate"], 50.0)

    def test_announcements_accept_tuple_rows(self):
        self._as_school_admin()
        created_at = datetime(2026, 6, 1, 12, 0, 0)

        async def fake_db():
            return FakeConnection([
                [(7, "Title", "Body", "all", None, created_at, None)],
            ])

        admin_extras.get_db_connection = fake_db

        response = self.client.get("/api/v1/admin/announcements")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["created_at"], "2026-06-01T12:00:00")

    def test_login_history_accepts_tuple_rows(self):
        self._as_school_admin()
        created_at = datetime(2026, 6, 1, 12, 0, 0)

        async def fake_db():
            return FakeConnection([
                [(9, 20, created_at, "127.0.0.1", "Alice", "alice")],
            ])

        admin_extras.get_db_connection = fake_db

        response = self.client.get("/api/v1/admin/login-history")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["username"], "alice")


if __name__ == "__main__":
    unittest.main()
