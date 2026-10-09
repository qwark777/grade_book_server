import unittest

from app.core import distributed_lock


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def execute(self, query, params=None):
        self.connection.calls.append((query, params))

    async def fetchone(self):
        return self.connection.rows.pop(0)


class FakeConnection:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


class DistributedLockTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_get_db = distributed_lock.get_db_connection

    async def asyncTearDown(self):
        distributed_lock.get_db_connection = self.original_get_db

    async def test_named_lock_is_checked_and_released(self):
        connection = FakeConnection(
            [{"acquired": 1}, {"acquired": 1}, {"released": 1}],
        )

        async def fake_db():
            return connection

        distributed_lock.get_db_connection = fake_db

        async with distributed_lock.mysql_named_lock("job") as lock:
            self.assertTrue(lock)
            self.assertTrue(await lock.still_held())

        self.assertTrue(connection.closed)
        self.assertIn("GET_LOCK", connection.calls[0][0])
        self.assertIn("IS_USED_LOCK", connection.calls[1][0])
        self.assertIn("RELEASE_LOCK", connection.calls[2][0])

    async def test_unavailable_lock_does_not_release_foreign_owner(self):
        connection = FakeConnection([{"acquired": 0}])

        async def fake_db():
            return connection

        distributed_lock.get_db_connection = fake_db

        async with distributed_lock.mysql_named_lock("job") as lock:
            self.assertFalse(lock)

        self.assertEqual(len(connection.calls), 1)
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
