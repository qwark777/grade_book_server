import asyncio
import logging

from app.core.config import settings
from app.core.distributed_lock import mysql_named_lock
from app.db.connection import get_db_connection

logger = logging.getLogger(__name__)


async def sync_expired_school_subscriptions() -> int:
    """Move expired active/trial school subscriptions to past_due."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE school_subscriptions
                SET status = 'past_due',
                    updated_at = CURRENT_TIMESTAMP
                WHERE status IN ('active', 'trial')
                  AND current_period_end < NOW()
                """
            )
            updated = cursor.rowcount or 0
            await conn.commit()
            if updated:
                logger.info(
                    "subscription_sync_marked_past_due",
                    extra={"updated_count": updated},
                )
            return updated
    finally:
        conn.close()


async def run_subscription_sync_loop() -> None:
    """Run one failover-capable subscription sync leader across all workers."""
    interval = max(60, settings.SUBSCRIPTION_SYNC_INTERVAL_SECONDS)
    try:
        while True:
            async with mysql_named_lock(
                "gradebook:subscription-sync-leader",
            ) as leader:
                if not leader:
                    await asyncio.sleep(min(30, interval))
                    continue
                logger.info("subscription_sync_leader_acquired")
                while True:
                    if not await leader.still_held():
                        logger.warning("subscription_sync_leader_lost")
                        break
                    try:
                        await sync_expired_school_subscriptions()
                    except Exception:
                        logger.exception("subscription_sync_failed")
                    await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("subscription_sync_stopped")
        raise
