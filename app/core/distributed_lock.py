from contextlib import asynccontextmanager

from app.db.connection import get_db_connection


def _lock_result_acquired(row) -> bool:
    if isinstance(row, dict):
        value = row.get("acquired")
    elif isinstance(row, (tuple, list)) and row:
        value = row[0]
    else:
        value = row
    return value == 1


class MySQLLockHandle:
    def __init__(self, conn, name: str, acquired: bool):
        self._conn = conn
        self._name = name
        self.acquired = acquired

    def __bool__(self) -> bool:
        return self.acquired

    async def still_held(self) -> bool:
        if not self.acquired:
            return False
        async with self._conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT IS_USED_LOCK(%s) = CONNECTION_ID() AS acquired
                """,
                (self._name,),
            )
            self.acquired = _lock_result_acquired(await cursor.fetchone())
        return self.acquired


@asynccontextmanager
async def mysql_named_lock(name: str, *, timeout_seconds: int = 0):
    """Hold a MySQL advisory lock for work shared by all API processes."""
    if not name or len(name) > 64:
        raise ValueError("MySQL lock name must contain 1-64 characters")

    conn = await get_db_connection()
    acquired = False
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT GET_LOCK(%s, %s) AS acquired",
                (name, max(0, timeout_seconds)),
            )
            acquired = _lock_result_acquired(await cursor.fetchone())
        yield MySQLLockHandle(conn, name, acquired)
    finally:
        if acquired:
            try:
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        "SELECT RELEASE_LOCK(%s)",
                        (name,),
                    )
                    await cursor.fetchone()
            finally:
                conn.close()
        else:
            conn.close()
