import os
import unittest
from types import SimpleNamespace

from fastapi import HTTPException


os.environ.setdefault(
    "SECRET_KEY",
    "test_secret_key_123456789012345678901234567890",
)
os.environ.setdefault(
    "ENCRYPTION_KEY",
    "ICkoftk-wbOx89vzo2nuGkPatHZCQ1IKBVpFdRJ1F4k=",
)

from app.api import academic_periods, group_chats, timetable  # noqa: E402
from app.db import timetable_operations  # noqa: E402


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []
        self.lastrowid = 99

    async def execute(self, query, params=None):
        self.calls.append((query, params))

    async def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    async def fetchall(self):
        rows = self.rows
        self.rows = []
        return rows


class SchoolScopingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.academic_get_school = academic_periods.get_school_id_for_user
        self.group_get_school = group_chats.get_school_id_for_user
        self.require_feature = timetable.require_school_feature

    async def asyncTearDown(self):
        academic_periods.get_school_id_for_user = self.academic_get_school
        group_chats.get_school_id_for_user = self.group_get_school
        timetable.require_school_feature = self.require_feature

    async def test_admin_cannot_select_foreign_academic_period_school(self):
        async def school_one(_user):
            return 1

        academic_periods.get_school_id_for_user = school_one
        with self.assertRaises(HTTPException) as raised:
            await academic_periods._resolve_school_id(
                SimpleNamespace(id=10, role="admin"),
                2,
            )
        self.assertEqual(raised.exception.status_code, 403)

    async def test_group_rejects_member_outside_school(self):
        async def school_one(_user):
            return 1

        group_chats.get_school_id_for_user = school_one
        cursor = FakeCursor([{"id": 10}])
        with self.assertRaises(HTTPException) as raised:
            await group_chats._ensure_group_members_allowed(
                cursor,
                SimpleNamespace(id=10, role="admin"),
                [10, 20],
            )
        self.assertEqual(raised.exception.status_code, 403)

    async def test_period_lookup_is_scoped_to_school(self):
        cursor = FakeCursor([{"id": 7}])
        period_id = await timetable._get_school_period_id(cursor, 3)
        self.assertEqual(period_id, 7)
        self.assertEqual(cursor.calls[0][1], (3,))
        self.assertIn("school_id = %s", cursor.calls[0][0])

    async def test_student_cannot_save_timetable_template(self):
        async def feature_enabled(*_args, **_kwargs):
            return None

        timetable.require_school_feature = feature_enabled
        payload = SimpleNamespace(class_id=1, lessons=[])
        with self.assertRaises(HTTPException) as raised:
            await timetable.save_timetable_template(
                payload,
                SimpleNamespace(id=20, role="student"),
            )
        self.assertEqual(raised.exception.status_code, 403)

    async def test_curriculum_rejects_subject_from_another_school(self):
        cursor = FakeCursor([{"subject_id": 10}])
        entries = [
            SimpleNamespace(subject_id=10),
            SimpleNamespace(subject_id=20),
        ]
        with self.assertRaises(HTTPException) as raised:
            await timetable._ensure_curriculum_subjects_in_school(
                cursor,
                entries,
                3,
            )
        self.assertEqual(raised.exception.status_code, 403)
        self.assertIn("school_id = %s", cursor.calls[0][0])
        self.assertEqual(cursor.calls[0][1][0], 3)

    async def test_room_list_query_is_scoped_to_school(self):
        class CursorContext(FakeCursor):
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        class Connection:
            def __init__(self):
                self.cursor_instance = CursorContext([])

            def cursor(self):
                return self.cursor_instance

            def close(self):
                return None

        connection = Connection()
        original = timetable_operations.get_db_connection

        async def fake_connection():
            return connection

        timetable_operations.get_db_connection = fake_connection
        try:
            self.assertEqual(await timetable_operations.get_rooms(7), [])
        finally:
            timetable_operations.get_db_connection = original

        query, params = connection.cursor_instance.calls[0]
        self.assertIn("school_rooms", query)
        self.assertEqual(params, (7,))


if __name__ == "__main__":
    unittest.main()
