"""baseline existing init_db schema

Revision ID: 20260611_0001
Revises:
Create Date: 2026-06-11 00:01:00 UTC
"""

import asyncio

from app.db.connection import close_db_pool, init_db


revision = "20260611_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    async def bootstrap_schema() -> None:
        try:
            await init_db()
        finally:
            await close_db_pool()

    asyncio.run(bootstrap_schema())


def downgrade() -> None:
    pass
