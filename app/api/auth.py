import hashlib
import logging
import secrets
from datetime import datetime, timedelta, UTC
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, status, Depends, Form
from fastapi.security import OAuth2PasswordRequestForm

from app.core.config import settings
from app.core.security import (
    get_password_hash,
    verify_password,
    create_access_token,
    get_current_user,
    validate_password_strength,
)
from app.db.user_operations import get_user, create_user
from app.models.user import UserCreate, Token

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/register")
async def register(user: UserCreate):
    """Register new user"""
    if not settings.ALLOW_PUBLIC_REGISTRATION:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Public registration is disabled",
        )
    try:
        validate_password_strength(user.password, user.username)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    hashed_password = get_password_hash(user.password)
    success = await create_user(user.username, hashed_password)
    if not success:
        raise HTTPException(status_code=400, detail="User already exists")
    return {"message": "User created successfully"}


@router.post("/token", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """Login user and return access token"""
    user = await get_user(form_data.username)
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Log login (background)
    import asyncio
    async def _log():
        from app.db.connection import get_db_connection
        conn = await get_db_connection()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT school_id FROM school_admins WHERE admin_user_id = %s LIMIT 1",
                    (user.id,)
                )
                r = await cur.fetchone()
                sid = r["school_id"] if r and isinstance(r, dict) else None
                if sid is None:
                    await cur.execute(
                        "SELECT c.school_id FROM class_students cs JOIN classes c ON c.id = cs.class_id WHERE cs.student_id = %s LIMIT 1",
                        (user.id,)
                    )
                    r = await cur.fetchone()
                    sid = r["school_id"] if r and isinstance(r, dict) else None
                await cur.execute(
                    "INSERT INTO login_log (user_id, school_id) VALUES (%s, %s)",
                    (user.id, sid)
                )
                await cur.execute(
                    "UPDATE users SET last_seen_at = UTC_TIMESTAMP() WHERE id = %s",
                    (user.id,),
                )
                await conn.commit()
        except Exception:
            pass
        finally:
            conn.close()
    asyncio.create_task(_log())
    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/protected")
async def protected(current_user=Depends(get_current_user)):
    """Protected endpoint for testing authentication"""
    return {"message": f"Welcome, {current_user.username}!", "status": "authenticated"}


@router.get("/verify-token")
async def verify_token_endpoint(current_user=Depends(get_current_user)):
    """Verify token and return user info"""
    from app.core.security import create_access_token
    new_token = create_access_token(data={"sub": current_user.username})
    return {
        "status": "authenticated",
        "username": current_user.username,
        "role": getattr(current_user, "role", "student"),
        "access_token": new_token,
        "token_type": "bearer",
        "user_id": current_user.id
    }


@router.post("/change-password")
async def change_password(
    old_password: str = Form(...),
    new_password: str = Form(...),
    current_user=Depends(get_current_user)
):
    """Change user password"""
    from app.db.connection import get_db_connection
    
    # Verify old password
    user = await get_user(current_user.username)
    if not user or not verify_password(old_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect old password"
        )
    
    if old_password == new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from old password"
        )

    # Validate new password
    try:
        validate_password_strength(new_password, current_user.username)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc)
        ) from exc
    
    # Update password
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            hashed_password = get_password_hash(new_password)
            await cursor.execute(
                "UPDATE users SET hashed_password = %s WHERE id = %s",
                (hashed_password, current_user.id)
            )
            await conn.commit()
        return {"message": "Password changed successfully"}
    finally:
        conn.close()


@router.post("/forgot-password")
async def forgot_password(identifier: str = Form(...)):
    """Create a one-time reset token without disclosing account existence."""
    from app.db.connection import get_db_connection
    from app.services.email_service import send_password_reset_email

    generic = {
        "message": "Если аккаунт найден и к нему привязан email, письмо отправлено"
    }
    identifier = identifier.strip().lower()
    if not identifier:
        return generic

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id, email
                FROM users
                WHERE LOWER(username) = %s OR LOWER(email) = %s
                LIMIT 1
                """,
                (identifier, identifier),
            )
            user = await cursor.fetchone()
            if not user:
                return generic
            user_id = user["id"] if isinstance(user, dict) else user[0]
            email = user["email"] if isinstance(user, dict) else user[1]
            if not email:
                return generic
            await cursor.execute(
                """
                SELECT 1 FROM password_reset_tokens
                WHERE user_id = %s
                  AND created_at > DATE_SUB(NOW(), INTERVAL 60 SECOND)
                LIMIT 1
                """,
                (user_id,),
            )
            if await cursor.fetchone():
                return generic

            token = secrets.token_urlsafe(48)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(
                minutes=settings.PASSWORD_RESET_EXPIRE_MINUTES
            )
            await cursor.execute(
                "UPDATE password_reset_tokens SET used_at = NOW() "
                "WHERE user_id = %s AND used_at IS NULL",
                (user_id,),
            )
            await cursor.execute(
                """
                INSERT INTO password_reset_tokens (user_id, token_hash, expires_at)
                VALUES (%s, %s, %s)
                """,
                (user_id, token_hash, expires_at),
            )
            await conn.commit()

        separator = "&" if "?" in settings.PASSWORD_RESET_URL else "?"
        reset_url = (
            f"{settings.PASSWORD_RESET_URL}{separator}"
            + urlencode({"reset_token": token})
        )
        try:
            await send_password_reset_email(email, reset_url)
        except Exception:
            logger.exception("Could not send password reset email")
        return generic
    finally:
        conn.close()


@router.post("/reset-password")
async def reset_password(
    token: str = Form(...),
    new_password: str = Form(...),
):
    from app.db.connection import get_db_connection

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT prt.id, prt.user_id, u.username
                FROM password_reset_tokens prt
                JOIN users u ON u.id = prt.user_id
                WHERE prt.token_hash = %s
                  AND prt.used_at IS NULL
                  AND prt.expires_at > NOW()
                LIMIT 1
                FOR UPDATE
                """,
                (token_hash,),
            )
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(
                    status_code=400,
                    detail="Ссылка недействительна или срок её действия истёк",
                )
            reset_id = row["id"] if isinstance(row, dict) else row[0]
            user_id = row["user_id"] if isinstance(row, dict) else row[1]
            username = row["username"] if isinstance(row, dict) else row[2]
            try:
                validate_password_strength(new_password, username)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            await cursor.execute(
                "UPDATE users SET hashed_password = %s WHERE id = %s",
                (get_password_hash(new_password), user_id),
            )
            await cursor.execute(
                "UPDATE password_reset_tokens SET used_at = NOW() WHERE id = %s",
                (reset_id,),
            )
            await conn.commit()
        return {"message": "Пароль изменён"}
    finally:
        conn.close()
