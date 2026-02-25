import logging
from app.core.config import settings
from app.core.security import get_password_hash
from app.db.connection import get_db_connection

logger = logging.getLogger(__name__)

async def create_first_owner() -> None:
    """Create a default superuser/owner if DB is empty users-wise"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Check if any user exists
            await cursor.execute("SELECT id FROM users LIMIT 1")
            existing_user = await cursor.fetchone()
            
            if not existing_user:
                logger.info("No users found. Creating first owner user.")
                username = settings.FIRST_SUPERUSER
                password = settings.FIRST_SUPERUSER_PASSWORD
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
                # Print to console as well to be sure it's seen
                print(f"----- FIRST OWNER CREATED -----")
                print(f"Username: {username}")
                print(f"Password: {password}")
                print(f"-------------------------------")
                logger.info(f"First owner created: username={username}")
            else:
                logger.info("Users already exist. Skipping first owner creation.")
    except Exception as e:
        logger.error(f"Error creating first owner: {e}")
        # Re-raise or handle? For startup, maybe we want to log and continue, or fail.
        # Logging is enough for now.
    finally:
        conn.close()
