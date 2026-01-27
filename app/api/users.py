from typing import List
import aiomysql
import random
import secrets
from fastapi import APIRouter, Depends, HTTPException, Form, UploadFile, File

from app.core.security import get_current_user
from app.db.connection import get_db_connection
from app.models.user import UserResponse, UserRole
from app.core.security import get_password_hash

router = APIRouter()


@router.get("/users/all", response_model=List[UserResponse])
async def get_all_users(
    include: str, 
    role: UserRole, 
    page: int = 1, 
    per_page: int = 20, 
    current_user=Depends(get_current_user)
):
    """Get all users with pagination and filtering"""
    conn = await get_db_connection()
    try:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            offset = (page - 1) * per_page
            if role == UserRole.student:
                if include:
                    await cursor.execute("""
                        SELECT u.id, TRIM(BOTH '"' FROM p.full_name) AS full_name,
                               (SELECT c.name FROM classes c 
                                JOIN class_students cs ON c.id = cs.class_id 
                                WHERE cs.student_id = u.id LIMIT 1) as class_name,
                               COALESCE(p.photo_url, CONCAT('/api/v1/profile_photo/', u.id)) AS photo_url
                        FROM users u
                        JOIN profiles p ON u.id = p.user_id
                        WHERE u.role = 'student' and LOCATE(%s, p.full_name) != 0
                        LIMIT %s OFFSET %s
                    """, (include, per_page, offset))
                else:
                    await cursor.execute("""
                        SELECT u.id, TRIM(BOTH '"' FROM p.full_name) AS full_name,
                               (SELECT c.name FROM classes c 
                                JOIN class_students cs ON c.id = cs.class_id 
                                WHERE cs.student_id = u.id LIMIT 1) as class_name,
                               COALESCE(p.photo_url, CONCAT('/api/v1/profile_photo/', u.id)) AS photo_url
                        FROM users u
                        JOIN profiles p ON u.id = p.user_id
                        WHERE u.role = 'student'
                        LIMIT %s OFFSET %s
                    """, (per_page, offset))
            else:
                if include:
                    await cursor.execute("""
                        SELECT u.id, TRIM(BOTH '"' FROM p.full_name) AS full_name,
                               NULL as class_name,
                               COALESCE(p.photo_url, CONCAT('/api/v1/profile_photo/', u.id)) AS photo_url
                        FROM users u
                        JOIN profiles p ON u.id = p.user_id
                        WHERE u.role = 'teacher' and LOCATE(%s, p.full_name) != 0
                        LIMIT %s OFFSET %s
                    """, (include, per_page, offset))
                else:
                    await cursor.execute("""
                        SELECT u.id, TRIM(BOTH '"' FROM p.full_name) AS full_name,
                               NULL as class_name,
                               COALESCE(p.photo_url, CONCAT('/api/v1/profile_photo/', u.id)) AS photo_url
                        FROM users u
                        JOIN profiles p ON u.id = p.user_id
                        WHERE u.role = 'teacher'
                        LIMIT %s OFFSET %s
                    """, (per_page, offset))
            return await cursor.fetchall()
    finally:
        conn.close()


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user_info(user_id: int, current_user=Depends(get_current_user)):
    """Get user information by ID"""
    conn = await get_db_connection()
    try:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("SELECT role FROM users WHERE id = %s", (user_id,))
            role_row = await cursor.fetchone()
            if not role_row:
                raise HTTPException(status_code=404, detail="User not found")
            role = role_row["role"]

            if role == "student":
                await cursor.execute("""
                    SELECT u.id, TRIM(BOTH '"' FROM p.full_name) AS full_name,
                           (SELECT c.name FROM classes c 
                            JOIN class_students cs ON c.id = cs.class_id 
                            WHERE cs.student_id = u.id LIMIT 1) as class_name,
                           COALESCE(p.photo_url, CONCAT('/profile_photo/', u.id)) AS photo_url
                    FROM users u
                    JOIN profiles p ON u.id = p.user_id
                    WHERE u.id = %s
                """, (user_id,))
            else:
                await cursor.execute("""
                    SELECT u.id, TRIM(BOTH '"' FROM p.full_name) AS full_name, p.work_place, p.location,
                           NULL as subject,
                           (SELECT GROUP_CONCAT(c.name SEPARATOR ', ')
                            FROM class_teachers ct 
                            JOIN classes c ON ct.class_id = c.id 
                            WHERE ct.teacher_id = u.id) as classes,
                           u.username, NULL as password, COALESCE(p.photo_url, CONCAT('/profile_photo/', u.id)) AS photo_url
                    FROM users u
                    JOIN profiles p ON u.id = p.user_id
                    WHERE u.id = %s
                """, (user_id,))
            user = await cursor.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="Profile not found")
            return user
    finally:
        conn.close()


@router.post("/owners/create-admin")
async def owner_create_admin(
    username: str = Form(...),
    password: str = Form(...),
    school_id: int = Form(...),
    current_user=Depends(get_current_user)
):
    """Owner creates an admin user and assigns to a school."""
    role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")

    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            hashed = get_password_hash(password)
            try:
                await cursor.execute(
                    "INSERT INTO users (username, hashed_password, role) VALUES (%s, %s, 'admin')",
                    (username, hashed)
                )
                await conn.commit()
            except Exception:
                raise HTTPException(status_code=400, detail="User already exists")

            await cursor.execute("SELECT id FROM users WHERE username=%s", (username,))
            row = await cursor.fetchone()
            admin_id = row[0] if isinstance(row, (list, tuple)) else row.get("id")

            await cursor.execute(
                "INSERT IGNORE INTO school_admins (school_id, admin_user_id) VALUES (%s, %s)",
                (school_id, admin_id)
            )
            await conn.commit()
            return {"status": "created", "admin_user_id": admin_id}
    finally:
        conn.close()


@router.post("/admin/create-teacher")
async def admin_create_teacher(
    full_name: str = Form(...),
    username: str = Form(None),
    password: str = Form(None),
    email: str = Form(None),
    phone: str = Form(None),
    position: str = Form(None),
    subjects: str = Form(None),  # Comma-separated list of subject names
    note: str = Form(None),
    current_user=Depends(get_current_user)
):
    """Admin creates a teacher user with profile information."""
    # Check if user is admin or owner
    role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")

    if not full_name or not full_name.strip():
        raise HTTPException(status_code=400, detail="Full name is required")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Use provided username or generate from email or full name
            final_username = None
            if username and username.strip():
                final_username = username.strip()
            elif email and email.strip():
                final_username = email.split("@")[0].lower().replace(".", "_")
            else:
                # Generate username from full name
                final_username = full_name.lower().replace(" ", "_").replace(".", "_")
                # Add random suffix to avoid conflicts
                final_username = f"{final_username}_{random.randint(1000, 9999)}"

            # Check if username already exists
            await cursor.execute("SELECT id FROM users WHERE username=%s", (final_username,))
            if await cursor.fetchone():
                if username and username.strip():
                    # If username was provided, raise error
                    raise HTTPException(status_code=400, detail="Username already exists")
                else:
                    # If auto-generated, add more random suffix
                    final_username = f"{final_username}_{random.randint(10000, 99999)}"

            # Use provided password or generate a temporary one
            final_password = None
            if password and password.strip():
                final_password = password.strip()
            else:
                # Generate a temporary password (should be changed on first login)
                final_password = secrets.token_urlsafe(12)

            # Create user with role 'teacher'
            hashed = get_password_hash(final_password)
            try:
                await cursor.execute(
                    "INSERT INTO users (username, hashed_password, role) VALUES (%s, %s, 'teacher')",
                    (final_username, hashed)
                )
                await conn.commit()
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to create user: {str(e)}")

            # Get the created user ID
            await cursor.execute("SELECT id FROM users WHERE username=%s", (final_username,))
            row = await cursor.fetchone()
            teacher_id = row[0] if isinstance(row, (list, tuple)) else row.get("id")

            # Create profile
            work_place = position or ""
            location = ""  # Can be added later
            bio = note or ""
            
            await cursor.execute("""
                INSERT INTO profiles (user_id, full_name, work_place, location, bio)
                VALUES (%s, %s, %s, %s, %s)
            """, (teacher_id, full_name, work_place, location, bio))
            await conn.commit()

            # Link subjects if provided
            if subjects and subjects.strip():
                subject_names = [s.strip() for s in subjects.split(",") if s.strip()]
                for subject_name in subject_names:
                    # Get subject ID by name
                    await cursor.execute("SELECT id FROM subjects WHERE name=%s", (subject_name,))
                    subject_row = await cursor.fetchone()
                    if subject_row:
                        subject_id = subject_row[0] if isinstance(subject_row, (list, tuple)) else subject_row.get("id")
                        # Link teacher to subject
                        await cursor.execute("""
                            INSERT IGNORE INTO teacher_subjects (teacher_id, subject_id)
                            VALUES (%s, %s)
                        """, (teacher_id, subject_id))
                await conn.commit()

            return {
                "status": "created",
                "teacher_id": teacher_id,
                "username": final_username,
                "password": final_password if not (password and password.strip()) else None  # Only return if auto-generated
            }
    finally:
        conn.close()


@router.get("/admin/count-admins")
async def count_admins(current_user=Depends(get_current_user)):
    """Get count of admin users (for owner analytics)."""
    # Check if user is owner
    role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT COUNT(*) as count FROM users WHERE role='admin'")
            result = await cursor.fetchone()
            count = result[0] if isinstance(result, (list, tuple)) else result.get("count", 0)
            return {"count": count}
    finally:
        conn.close()


@router.put("/admin/users/{user_id}")
async def admin_update_user(
    user_id: int,
    full_name: str = Form(None),
    work_place: str = Form(None),
    location: str = Form(None),
    bio: str = Form(None),
    username: str = Form(None),
    password: str = Form(None),
    email: str = Form(None),
    phone: str = Form(None),
    file: UploadFile = File(None),
    current_user=Depends(get_current_user)
):
    """Admin updates a user (student or teacher) with profile information."""
    # Check if user is admin or owner
    role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Check if user exists
            await cursor.execute("SELECT role FROM users WHERE id = %s", (user_id,))
            user_row = await cursor.fetchone()
            if not user_row:
                raise HTTPException(status_code=404, detail="User not found")
            
            user_role = user_row[0] if isinstance(user_row, (list, tuple)) else user_row.get("role")

            # Update username if provided
            if username and username.strip():
                await cursor.execute(
                    "UPDATE users SET username = %s WHERE id = %s",
                    (username.strip(), user_id)
                )
                await conn.commit()

            # Update password if provided
            if password and password.strip():
                hashed = get_password_hash(password.strip())
                await cursor.execute(
                    "UPDATE users SET hashed_password = %s WHERE id = %s",
                    (hashed, user_id)
                )
                await conn.commit()

            # Update profile fields
            updates = []
            params = []
            
            if full_name is not None and full_name.strip():
                updates.append("full_name = %s")
                params.append(full_name.strip())
            
            if work_place is not None:
                updates.append("work_place = %s")
                params.append(work_place or "")
            
            if location is not None:
                updates.append("location = %s")
                params.append(location or "")
            
            if bio is not None:
                updates.append("bio = %s")
                params.append(bio or "")

            if updates:
                params.append(user_id)
                await cursor.execute(
                    f"UPDATE profiles SET {', '.join(updates)} WHERE user_id = %s",
                    params
                )
                await conn.commit()

            # Handle photo upload if provided
            if file and file.filename:
                import os
                from app.core.config import settings
                
                ext = file.filename.split('.')[-1].lower()
                if ext not in ["jpg", "jpeg", "png"]:
                    raise HTTPException(status_code=400, detail="Unsupported file type")
                
                # Remove old photos
                for e in ["jpg", "jpeg", "png"]:
                    old_path = os.path.join(settings.PROFILE_PHOTOS_DIR, f"{user_id}.{e}")
                    if os.path.exists(old_path):
                        os.remove(old_path)
                
                # Save new photo
                path = os.path.join(settings.PROFILE_PHOTOS_DIR, f"{user_id}.{ext}")
                with open(path, "wb") as f:
                    f.write(await file.read())
                
                # Update photo_url in profiles
                photo_url = f"/api/v1/profile_photo/{user_id}"
                await cursor.execute(
                    "UPDATE profiles SET photo_url = %s WHERE user_id = %s",
                    (photo_url, user_id)
                )
                await conn.commit()

            return {"message": "User updated successfully", "user_id": user_id}
    finally:
        conn.close()

