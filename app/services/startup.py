import logging
import os
from app.core.config import settings
from app.core.security import get_password_hash
from app.db.connection import get_db_connection

logger = logging.getLogger(__name__)

_PLACEHOLDER_OWNER_PASSWORDS = {
    "change_me",
    "changeme",
    "change_me_to_a_long_random_value",
    "replace_with_long_random_owner_password",
    "password",
    "admin",
    "owner",
}


def _allow_insecure_dev_defaults() -> bool:
    return os.getenv("ALLOW_INSECURE_DEV_DEFAULTS", "").lower() in ("1", "true", "yes")


def _is_unsafe_initial_owner_password(password: str) -> bool:
    normalized = (password or "").strip().lower()
    return len(password or "") < 12 or normalized in _PLACEHOLDER_OWNER_PASSWORDS


async def create_first_owner() -> None:
    """Create a default superuser/owner if DB is empty users-wise"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Check if any user exists
            await cursor.execute("SELECT id FROM users LIMIT 1")
            existing_user = await cursor.fetchone()
            
            if not existing_user:
                if not settings.FIRST_SUPERUSER or not settings.FIRST_SUPERUSER_PASSWORD:
                    logger.warning(
                        "No users found, but FIRST_SUPERUSER/OWNER_PASSWORD is not configured. "
                        "Skipping automatic owner creation."
                    )
                    return

                logger.info("No users found. Creating first owner user.")
                username = settings.FIRST_SUPERUSER
                password = settings.FIRST_SUPERUSER_PASSWORD
                if (
                    _is_unsafe_initial_owner_password(password)
                    and not _allow_insecure_dev_defaults()
                ):
                    logger.error(
                        "Refusing to create first owner with an unsafe placeholder "
                        "or short password. Set OWNER_PASSWORD/FIRST_SUPERUSER_PASSWORD "
                        "to a long random value."
                    )
                    return

                hashed_password = get_password_hash(password)
                role = "owner"
                
                await cursor.execute(
                    "INSERT INTO users (username, hashed_password, role) VALUES (%s, %s, %s)",
                    (username, hashed_password, role)
                )
                user_id = cursor.lastrowid
                
                # Also create a profile
                await cursor.execute(
                    "INSERT INTO profiles (user_id, full_name, bio) VALUES (%s, %s, %s)",
                    (user_id, "System Owner", "Created automatically on first startup")
                )
                
                await conn.commit()
                logger.info(f"First owner created: username={username}")
            else:
                logger.info("Users already exist. Skipping first owner creation.")
    except Exception as e:
        logger.error(f"Error creating first owner: {e}")
        # Re-raise or handle? For startup, maybe we want to log and continue, or fail.
        # Logging is enough for now.
    finally:
        conn.close()
