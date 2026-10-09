import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from fastapi import HTTPException

os.environ.setdefault("SECRET_KEY", "test_secret_key_123456789012345678901234567890")
os.environ.setdefault("ENCRYPTION_KEY", "ICkoftk-wbOx89vzo2nuGkPatHZCQ1IKBVpFdRJ1F4k=")

from app.api import admin_extras
from app.api import admin_analytics
from app.api import crm_integrations
from app.api import grades
from app.api import lessons
from app.api import lessons_1c_integration
from app.api import main as api_main
from app.api import student_access
from app.api import timetable
from app.core import entitlements
from app.services import subscription_sync


class FakeCursor:
    def __init__(self, rows, rowcount=0):
        self.rows = list(rows)
        self.calls = []
        self.rowcount = rowcount

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
        rows = self.rows
        self.rows = []
        return rows


class FakeConnection:
    def __init__(self, rows, rowcount=0):
        self.cursor_obj = FakeCursor(rows, rowcount=rowcount)

    def cursor(self):
        return self.cursor_obj

    async def commit(self):
        pass

    def close(self):
        pass


class SecurityAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._admin_get_school_id = admin_extras._get_school_id
        self._crm_get_school_id_for_user = crm_integrations.get_school_id_for_user
        self._main_get_school_id_for_user = api_main.get_school_id_for_user
        self._student_access_get_db_connection = student_access.get_db_connection
        self._student_access_get_school_id_for_user = student_access.get_school_id_for_user
        self._student_access_get_parent_children = student_access._get_parent_children
        self._lessons_1c_get_school_id_for_user = lessons_1c_integration.get_school_id_for_user
        self._timetable_get_school_id_for_user = timetable.get_school_id_for_user
        self._timetable_get_db_connection = timetable.get_db_connection
        self._grades_get_school_id_for_user = grades.get_school_id_for_user
        self._lessons_get_school_id_for_user = lessons.get_school_id_for_user
        self._entitlements_get_db_connection = entitlements.get_db_connection
        self._subscription_sync_get_db_connection = subscription_sync.get_db_connection

    async def asyncTearDown(self):
        admin_extras._get_school_id = self._admin_get_school_id
        crm_integrations.get_school_id_for_user = self._crm_get_school_id_for_user
        api_main.get_school_id_for_user = self._main_get_school_id_for_user
        student_access.get_db_connection = self._student_access_get_db_connection
        student_access.get_school_id_for_user = self._student_access_get_school_id_for_user
        student_access._get_parent_children = self._student_access_get_parent_children
        lessons_1c_integration.get_school_id_for_user = self._lessons_1c_get_school_id_for_user
        timetable.get_school_id_for_user = self._timetable_get_school_id_for_user
        timetable.get_db_connection = self._timetable_get_db_connection
        grades.get_school_id_for_user = self._grades_get_school_id_for_user
        lessons.get_school_id_for_user = self._lessons_get_school_id_for_user
        entitlements.get_db_connection = self._entitlements_get_db_connection
        subscription_sync.get_db_connection = self._subscription_sync_get_db_connection

    async def test_school_admin_cannot_access_foreign_class(self):
        async def school_one(_current_user):
            return 1

        admin_extras._get_school_id = school_one
        cursor = FakeCursor([None])
        current_user = SimpleNamespace(id=10, role="admin")

        with self.assertRaises(HTTPException) as raised:
            await admin_extras._require_class_access(cursor, current_user, 200)

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(cursor.calls[0][1], (200, 1))

    async def test_school_admin_cannot_modify_foreign_grades_class(self):
        async def school_one(_current_user):
            return 1

        grades.get_school_id_for_user = school_one
        cursor = FakeCursor([None])

        with self.assertRaises(HTTPException) as raised:
            await grades._get_class_for_admin(
                cursor,
                200,
                SimpleNamespace(id=10, role="admin"),
            )

        self.assertEqual(raised.exception.status_code, 404)

    async def test_school_admin_cannot_read_foreign_grades_class(self):
        async def school_one(_current_user):
            return 1

        grades.get_school_id_for_user = school_one
        cursor = FakeCursor([None])

        with self.assertRaises(HTTPException) as raised:
            await grades._ensure_can_read_class(
                cursor,
                SimpleNamespace(id=10, role="admin"),
                200,
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_student_can_read_own_grades_class(self):
        cursor = FakeCursor([{"allowed": 1}])

        await grades._ensure_can_read_class(
            cursor,
            SimpleNamespace(id=50, role="student"),
            100,
        )

    async def test_parent_cannot_read_foreign_grades_class(self):
        cursor = FakeCursor([None])

        with self.assertRaises(HTTPException) as raised:
            await grades._ensure_can_read_class(
                cursor,
                SimpleNamespace(id=60, role="parent"),
                100,
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_school_admin_can_access_own_class(self):
        async def school_one(_current_user):
            return 1

        admin_extras._get_school_id = school_one
        cursor = FakeCursor([{"school_id": 1}])
        current_user = SimpleNamespace(id=10, role="admin")

        school_id = await admin_extras._require_class_access(cursor, current_user, 100)

        self.assertEqual(school_id, 1)
        self.assertEqual(cursor.calls[0][1], (100, 1))

    async def test_unscoped_school_admin_cannot_use_global_admin_analytics(self):
        cursor = FakeCursor([None])

        with self.assertRaises(HTTPException) as raised:
            await admin_analytics._resolve_scope_school_id(
                cursor,
                SimpleNamespace(id=10, role="admin"),
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_owner_keeps_global_admin_analytics_scope(self):
        cursor = FakeCursor([])

        school_id = await admin_analytics._resolve_scope_school_id(
            cursor,
            SimpleNamespace(id=1, role="owner"),
        )

        self.assertIsNone(school_id)

    async def test_school_admin_cannot_award_foreign_user(self):
        async def school_one(_current_user):
            return 1

        api_main.get_school_id_for_user = school_one
        cursor = FakeCursor([None])
        current_user = SimpleNamespace(id=10, role="admin")

        with self.assertRaises(HTTPException) as raised:
            await api_main._ensure_user_in_admin_scope(cursor, current_user, 300)

        self.assertEqual(raised.exception.status_code, 403)

    async def test_school_admin_cannot_manage_foreign_crm_school(self):
        async def school_one(_current_user):
            return 1

        crm_integrations.get_school_id_for_user = school_one

        with self.assertRaises(HTTPException) as raised:
            await crm_integrations._ensure_school_access(
                SimpleNamespace(id=10, role="admin"),
                2,
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_owner_can_manage_any_crm_school(self):
        await crm_integrations._ensure_school_access(
            SimpleNamespace(id=1, role="owner"),
            2,
        )

    async def test_school_admin_cannot_read_foreign_student_analytics(self):
        async def school_one(_current_user):
            return 1

        async def fake_db():
            return FakeConnection([None])

        student_access.get_school_id_for_user = school_one
        student_access.get_db_connection = fake_db

        with self.assertRaises(HTTPException) as raised:
            await student_access.ensure_can_access_student(
                SimpleNamespace(id=10, role="admin"),
                300,
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_school_admin_can_read_own_student_analytics(self):
        async def school_one(_current_user):
            return 1

        async def fake_db():
            return FakeConnection([{"allowed": 1}])

        student_access.get_school_id_for_user = school_one
        student_access.get_db_connection = fake_db

        await student_access.ensure_can_access_student(
            SimpleNamespace(id=10, role="admin"),
            300,
        )

    async def test_teacher_cannot_read_unassigned_student_analytics(self):
        async def fake_db():
            return FakeConnection([None, None])

        student_access.get_db_connection = fake_db

        with self.assertRaises(HTTPException) as raised:
            await student_access.ensure_can_access_student(
                SimpleNamespace(id=20, role="teacher"),
                300,
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_teacher_can_read_assigned_student_analytics(self):
        async def fake_db():
            return FakeConnection([{"allowed": 1}])

        student_access.get_db_connection = fake_db

        await student_access.ensure_can_access_student(
            SimpleNamespace(id=20, role="teacher"),
            300,
        )

    async def test_school_admin_cannot_export_foreign_1c_school(self):
        async def school_one(_current_user):
            return 1

        lessons_1c_integration.get_school_id_for_user = school_one

        with self.assertRaises(HTTPException) as raised:
            await lessons_1c_integration._resolve_export_school_id(
                2,
                SimpleNamespace(id=10, role="admin"),
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_school_admin_1c_export_defaults_to_own_school(self):
        async def school_one(_current_user):
            return 1

        lessons_1c_integration.get_school_id_for_user = school_one

        school_id = await lessons_1c_integration._resolve_export_school_id(
            None,
            SimpleNamespace(id=10, role="admin"),
        )

        self.assertEqual(school_id, 1)

    async def test_unscoped_school_admin_cannot_select_timetable_school(self):
        async def no_school(_current_user):
            return None

        timetable.get_school_id_for_user = no_school

        with self.assertRaises(HTTPException) as raised:
            await timetable._resolve_timetable_school_id(
                SimpleNamespace(id=10, role="admin"),
                2,
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_school_admin_cannot_select_foreign_timetable_school(self):
        async def school_one(_current_user):
            return 1

        timetable.get_school_id_for_user = school_one

        with self.assertRaises(HTTPException) as raised:
            await timetable._resolve_timetable_school_id(
                SimpleNamespace(id=10, role="admin"),
                2,
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_curriculum_rejects_classes_outside_school(self):
        cursor = FakeCursor([{"id": 100}])
        entries = [
            timetable.CurriculumEntry(class_id=100, subject_id=1, hours_per_week=2),
            timetable.CurriculumEntry(class_id=200, subject_id=1, hours_per_week=2),
        ]

        with self.assertRaises(HTTPException) as raised:
            await timetable._ensure_curriculum_classes_in_school(cursor, entries, 1)

        self.assertEqual(raised.exception.status_code, 403)

    async def test_student_cannot_read_foreign_class_timetable(self):
        async def fake_db():
            return FakeConnection([None])

        timetable.get_db_connection = fake_db

        with self.assertRaises(HTTPException) as raised:
            await timetable._ensure_can_read_class_timetable(
                SimpleNamespace(id=50, role="student"),
                200,
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_teacher_can_read_assigned_class_timetable(self):
        async def fake_db():
            return FakeConnection([{"allowed": 1}])

        timetable.get_db_connection = fake_db

        await timetable._ensure_can_read_class_timetable(
            SimpleNamespace(id=20, role="teacher"),
            200,
        )

    async def test_school_admin_cannot_manage_foreign_school_homework(self):
        async def school_one(_current_user):
            return 1

        grades.get_school_id_for_user = school_one

        with self.assertRaises(HTTPException) as raised:
            await grades._ensure_staff_can_access_homework_row(
                {"teacher_id": 20, "school_id": 2},
                SimpleNamespace(id=10, role="admin"),
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_school_admin_can_manage_own_school_homework(self):
        async def school_one(_current_user):
            return 1

        grades.get_school_id_for_user = school_one

        await grades._ensure_staff_can_access_homework_row(
            {"teacher_id": 20, "school_id": 1},
            SimpleNamespace(id=10, role="admin"),
        )

    async def test_teacher_cannot_manage_other_teacher_homework(self):
        with self.assertRaises(HTTPException) as raised:
            await grades._ensure_staff_can_access_homework_row(
                {"teacher_id": 21, "school_id": 1},
                SimpleNamespace(id=20, role="teacher"),
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_school_admin_cannot_manage_foreign_paid_lesson(self):
        async def school_one(_current_user):
            return 1

        lessons.get_school_id_for_user = school_one

        with self.assertRaises(HTTPException) as raised:
            await lessons._assert_can_manage_lesson(
                {"tutor_id": 20, "school_id": 2},
                SimpleNamespace(id=10, role="admin"),
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_school_admin_can_manage_own_paid_lesson(self):
        async def school_one(_current_user):
            return 1

        lessons.get_school_id_for_user = school_one

        await lessons._assert_can_manage_lesson(
            {"tutor_id": 20, "school_id": 1},
            SimpleNamespace(id=10, role="admin"),
        )

    async def test_tutor_can_manage_own_paid_lesson(self):
        await lessons._assert_can_manage_lesson(
            {"tutor_id": 20, "school_id": 2},
            SimpleNamespace(id=20, role="teacher"),
        )

    async def test_school_admin_cannot_create_foreign_paid_lesson(self):
        async def school_one(_current_user):
            return 1

        lessons.get_school_id_for_user = school_one

        with self.assertRaises(HTTPException) as raised:
            await lessons._resolve_lesson_school_id(
                SimpleNamespace(id=10, role="admin"),
                2,
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_school_admin_defaults_paid_lesson_to_own_school(self):
        async def school_one(_current_user):
            return 1

        lessons.get_school_id_for_user = school_one

        school_id = await lessons._resolve_lesson_school_id(
            SimpleNamespace(id=10, role="admin"),
            None,
        )

        self.assertEqual(school_id, 1)

    async def test_student_must_belong_to_paid_lesson_school(self):
        cursor = FakeCursor([None])

        with self.assertRaises(HTTPException) as raised:
            await lessons._assert_student_in_lesson_school(cursor, 50, 2)

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(cursor.calls[0][1], (50, 2))

    async def test_student_can_use_paid_lesson_in_own_school(self):
        cursor = FakeCursor([{"ok": 1}])

        await lessons._assert_student_in_lesson_school(cursor, 50, 2)

        self.assertEqual(cursor.calls[0][1], (50, 2))

    async def test_non_admin_cannot_award_points(self):
        current_user = SimpleNamespace(id=50, role="student")
        payload = api_main.PointsAwardRequest(user_id=50, points=10)

        with self.assertRaises(HTTPException) as raised:
            await api_main.award_points_and_achievement(payload, current_user=current_user)

        self.assertEqual(raised.exception.status_code, 403)

    async def test_subscription_in_grace_keeps_entitlement_available(self):
        async def fake_db():
            return FakeConnection([
                {
                    "status": "active",
                    "current_period_end": datetime.now() - timedelta(days=1),
                    "entitlement_value": "true",
                }
            ])

        entitlements.get_db_connection = fake_db

        check = await entitlements.check_entitlement(
            1,
            "analytics.basic",
            SimpleNamespace(id=10, role="admin"),
        )

        self.assertTrue(check.has_access)

    async def test_expired_subscription_after_grace_blocks_entitlement(self):
        async def fake_db():
            return FakeConnection([
                {
                    "status": "active",
                    "current_period_end": datetime.now() - timedelta(days=8),
                    "entitlement_value": "true",
                }
            ])

        entitlements.get_db_connection = fake_db

        check = await entitlements.check_entitlement(
            1,
            "analytics.basic",
            SimpleNamespace(id=10, role="admin"),
        )

        self.assertFalse(check.has_access)
        self.assertEqual(check.reason, "Subscription period has ended")

    async def test_subscription_sync_marks_expired_as_past_due(self):
        fake_conn = FakeConnection([], rowcount=3)

        async def fake_db():
            return fake_conn

        subscription_sync.get_db_connection = fake_db

        updated = await subscription_sync.sync_expired_school_subscriptions()

        self.assertEqual(updated, 3)
        self.assertIn("UPDATE school_subscriptions", fake_conn.cursor_obj.calls[0][0])


if __name__ == "__main__":
    unittest.main()
