import aiomysql
from typing import Optional
from app.db.connection import get_db_connection
from app.models.user import UserInDB


async def get_user(username: str) -> Optional[UserInDB]:
    """Get user by username"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT id, username, hashed_password, role FROM users WHERE username=%s", 
                (username,)
            )
            user = await cursor.fetchone()
            return UserInDB(**user) if user else None
    finally:
        conn.close()


async def create_user(username: str, hashed_password: str) -> bool:
    """Create new user"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            try:
                await cursor.execute(
                    "INSERT INTO users (username, hashed_password) VALUES (%s, %s)",
                    (username, hashed_password)
                )
                await conn.commit()
                return True
            except aiomysql.IntegrityError:
                return False
    finally:
        conn.close()


async def get_user_by_id(user_id: int) -> Optional[UserInDB]:
    """Get user by ID"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT id, username, hashed_password, role FROM users WHERE id=%s", 
                (user_id,)
            )
            user = await cursor.fetchone()
            return UserInDB(**user) if user else None
    finally:
        conn.close()


