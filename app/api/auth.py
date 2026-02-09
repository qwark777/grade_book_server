from fastapi import APIRouter, HTTPException, status, Depends, Form
from fastapi.security import OAuth2PasswordRequestForm

from app.core.security import get_password_hash, verify_password, create_access_token, get_current_user
from app.db.user_operations import get_user, create_user
from app.models.user import UserCreate, Token

router = APIRouter()


@router.post("/register")
async def register(user: UserCreate):
    """Register new user"""
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
    
    # Validate new password
    if len(new_password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be at least 6 characters long"
        )
    
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
