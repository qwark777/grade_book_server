import os
import unittest

from fastapi import HTTPException


os.environ.setdefault(
    "SECRET_KEY",
    "test_secret_key_123456789012345678901234567890",
)
os.environ.setdefault(
    "ENCRYPTION_KEY",
    "ICkoftk-wbOx89vzo2nuGkPatHZCQ1IKBVpFdRJ1F4k=",
)

from app.api.grades import (  # noqa: E402
    _ensure_can_modify_grade,
    _ensure_can_read_grade,
)


class FakeCursor:
    def __init__(self, rows):
        self._rows = iter(rows)
        self.executed = []

    async def execute(self, query, params=()):
        self.executed.append((query, params))

    async def fetchone(self):
        return next(self._rows, None)


class GradeAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_student_can_read_own_grade(self):
        cursor = FakeCursor([])
        await _ensure_can_read_grade(
            cursor,
            {"id": 10, "role": "student"},
            student_id=10,
            subject_id=3,
        )
        self.assertEqual(cursor.executed, [])

    async def test_student_cannot_read_classmates_grade(self):
        cursor = FakeCursor([{"class_id": 4}])
        with self.assertRaises(HTTPException) as raised:
            await _ensure_can_read_grade(
                cursor,
                {"id": 10, "role": "student"},
                student_id=11,
                subject_id=3,
            )
        self.assertEqual(raised.exception.status_code, 403)

    async def test_parent_can_read_linked_child_grade(self):
        cursor = FakeCursor([{"allowed": 1}])
        await _ensure_can_read_grade(
            cursor,
            {"id": 20, "role": "parent"},
            student_id=10,
            subject_id=3,
        )

    async def test_orphan_grade_cannot_be_modified(self):
        cursor = FakeCursor([None])
        with self.assertRaises(HTTPException) as raised:
            await _ensure_can_modify_grade(
                cursor,
                {"id": 30, "role": "teacher"},
                student_id=10,
                subject_id=3,
            )
        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
