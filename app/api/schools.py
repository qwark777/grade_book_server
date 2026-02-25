from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional
from pydantic import BaseModel
from app.db.connection import get_db_connection
from app.core.security import get_current_user
from app.core.entitlements import get_school_id_for_user, has_admin_permission, get_admin_info, ADMIN_PERMISSIONS
from app.models.user import User

router = APIRouter(prefix="/schools")


class School(BaseModel):
    id: int
    name: str
    address: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class SchoolUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class ClassBrief(BaseModel):
    id: int
    name: str
    academic_year: str
    student_count: int

class SchoolCreate(BaseModel):
    name: str
    address: Optional[str] = None


@router.get("/", response_model=List[School])
async def list_schools(current_user=Depends(get_current_user)):
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id, name, address FROM schools ORDER BY name")
            rows = await cur.fetchall()
            return rows
    finally:
        conn.close()


@router.get("/{school_id}", response_model=School)
async def get_school(school_id: int, current_user: User = Depends(get_current_user)):
    """Get school by ID. Admin can only access their school, owner can access any."""
    user_school_id = await get_school_id_for_user(current_user)
    if current_user.role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")
    if current_user.role == "admin" and user_school_id != school_id:
        raise HTTPException(status_code=403, detail="Access denied to this school")
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id, name, address FROM schools WHERE id = %s", (school_id,))
            row = await cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="School not found")
            return row
    finally:
        conn.close()


@router.put("/{school_id}", response_model=School)
async def update_school(school_id: int, payload: SchoolUpdate, current_user: User = Depends(get_current_user)):
    """Update school. Only main admin or admin with edit_school permission. Owner can update any."""
    user_school_id = await get_school_id_for_user(current_user)
    if current_user.role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")
    if current_user.role == "admin" and user_school_id != school_id:
        raise HTTPException(status_code=403, detail="Access denied to this school")

    # Check permission: main admin or edit_school
    can_edit = await has_admin_permission(current_user, school_id, "edit_school")
    if not can_edit:
        raise HTTPException(status_code=403, detail="Нет права редактировать школу")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id, name, address FROM schools WHERE id = %s", (school_id,))
            row = await cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="School not found")
            updates = []
            params = []
            if payload.name is not None:
                if not payload.name.strip():
                    raise HTTPException(status_code=400, detail="School name cannot be empty")
                updates.append("name = %s")
                params.append(payload.name.strip())
            if payload.address is not None:
                updates.append("address = %s")
                params.append(payload.address.strip() or None)
            if payload.address is not None:
                updates.append("address = %s")
                params.append(payload.address.strip() or None)
            # email and phone are not in DB currently
            if not updates:
                return row
            params.append(school_id)
            await cur.execute(
                f"UPDATE schools SET {', '.join(updates)} WHERE id = %s",
                params
            )
            await conn.commit()
            # Log audit
            try:
                import json
                uid = current_user.id if hasattr(current_user, 'id') else current_user.get('id')
                await cur.execute(
                    """INSERT INTO audit_log (school_id, user_id, action, entity_type, entity_id, details)
                       VALUES (%s, %s, 'school_updated', 'school', %s, %s)""",
                    (school_id, uid, school_id, json.dumps({"updates": list(updates)}))
                )
                await conn.commit()
            except Exception:
                pass
            await cur.execute("SELECT id, name, address FROM schools WHERE id = %s", (school_id,))
            return await cur.fetchone()
    finally:
        conn.close()


class SchoolAdminItem(BaseModel):
    admin_user_id: int
    full_name: str
    username: str
    is_main_admin: bool
    permissions: List[str]


class SchoolAdminUpdate(BaseModel):
    is_main_admin: Optional[bool] = None
    permissions: Optional[List[str]] = None


@router.get("/{school_id}/admins", response_model=List[SchoolAdminItem])
async def list_school_admins(school_id: int, current_user: User = Depends(get_current_user)):
    """List admins of a school with their roles and permissions. Only main admin or owner."""
    user_school_id = await get_school_id_for_user(current_user)
    if current_user.role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")
    if current_user.role == "admin" and user_school_id != school_id:
        raise HTTPException(status_code=403, detail="Access denied")
    can_manage = await has_admin_permission(current_user, school_id, "manage_admins")
    if not can_manage:
        raise HTTPException(status_code=403, detail="Нет права управлять админами")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT sa.admin_user_id, sa.is_main_admin, COALESCE(p.full_name, u.username) as full_name, u.username
                FROM school_admins sa
                JOIN users u ON u.id = sa.admin_user_id
                LEFT JOIN profiles p ON p.user_id = u.id
                WHERE sa.school_id = %s
            """, (school_id,))
            admins = await cur.fetchall()

            result = []
            for a in admins:
                admin_id = a["admin_user_id"] if isinstance(a, dict) else a[0]
                is_main = bool(a.get("is_main_admin", False) if isinstance(a, dict) else a[1])
                full_name = (a.get("full_name") or a.get("username", "")) if isinstance(a, dict) else (a[2] or a[3])
                username = a.get("username", "") if isinstance(a, dict) else a[3]

                perms = []
                if not is_main:
                    await cur.execute(
                        "SELECT permission_key FROM admin_permissions WHERE school_id = %s AND admin_user_id = %s",
                        (school_id, admin_id)
                    )
                    perms = [r.get("permission_key") if isinstance(r, dict) else r[0] for r in await cur.fetchall()]

                result.append(SchoolAdminItem(
                    admin_user_id=admin_id,
                    full_name=full_name or username,
                    username=username,
                    is_main_admin=is_main,
                    permissions=perms,
                ))
            return result
    finally:
        conn.close()


@router.put("/{school_id}/admins/{admin_user_id}")
async def update_school_admin(
    school_id: int, admin_user_id: int, payload: SchoolAdminUpdate,
    current_user: User = Depends(get_current_user)
):
    """Update admin: set is_main_admin and/or permissions. Only main admin or owner."""
    user_school_id = await get_school_id_for_user(current_user)
    if current_user.role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")
    if current_user.role == "admin" and user_school_id != school_id:
        raise HTTPException(status_code=403, detail="Access denied")
    can_manage = await has_admin_permission(current_user, school_id, "manage_admins")
    if not can_manage:
        raise HTTPException(status_code=403, detail="Нет права управлять админами")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM school_admins WHERE school_id = %s AND admin_user_id = %s",
                (school_id, admin_user_id)
            )
            if not await cur.fetchone():
                raise HTTPException(status_code=404, detail="Admin not found in this school")

            if payload.is_main_admin is not None:
                await cur.execute(
                    "UPDATE school_admins SET is_main_admin = %s WHERE school_id = %s AND admin_user_id = %s",
                    (bool(payload.is_main_admin), school_id, admin_user_id)
                )
                if payload.is_main_admin:
                    await cur.execute(
                        "DELETE FROM admin_permissions WHERE school_id = %s AND admin_user_id = %s",
                        (school_id, admin_user_id)
                    )

            if payload.permissions is not None:
                valid = [p for p in payload.permissions if p in ADMIN_PERMISSIONS]
                await cur.execute(
                    "DELETE FROM admin_permissions WHERE school_id = %s AND admin_user_id = %s",
                    (school_id, admin_user_id)
                )
                for p in valid:
                    await cur.execute(
                        "INSERT INTO admin_permissions (school_id, admin_user_id, permission_key) VALUES (%s, %s, %s)",
                        (school_id, admin_user_id, p)
                    )
                if not payload.is_main_admin:
                    await cur.execute(
                        "UPDATE school_admins SET is_main_admin = FALSE WHERE school_id = %s AND admin_user_id = %s",
                        (school_id, admin_user_id)
                    )

            await conn.commit()
            return {"status": "ok"}
    finally:
        conn.close()


@router.get("/{school_id}/classes", response_model=List[ClassBrief])
async def classes_by_school(school_id: int, current_user=Depends(get_current_user)):
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT c.id, c.name, c.academic_year,
                       (SELECT COUNT(*) FROM class_students cs WHERE cs.class_id = c.id) AS student_count
                FROM classes c
                WHERE c.school_id = %s
                ORDER BY c.name
                """,
                (school_id,)
            )
            rows = await cur.fetchall()
            return rows
    finally:
        conn.close()


@router.post("/", status_code=201)
async def create_school(payload: SchoolCreate, current_user=Depends(get_current_user)):
    """Create a new school. Only owner-level users should call this (basic check by role)."""
    # Simple role check if available
    role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")

    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="School name is required")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            # Ensure unique by name
            await cur.execute("SELECT id FROM schools WHERE name=%s", (payload.name.strip(),))
            row = await cur.fetchone()
            if row:
                return {"id": row["id"], "status": "exists"}

            await cur.execute(
                "INSERT INTO schools (name, address) VALUES (%s, %s)",
                (payload.name.strip(), (payload.address or None))
            )
            await conn.commit()
            new_id = cur.lastrowid
            return {"id": new_id, "status": "created"}
    finally:
        conn.close()


@router.get("/without-admin/count")
async def count_schools_without_admin(current_user=Depends(get_current_user)):
    """Get count of schools that don't have an admin assigned. Owner only."""
    role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT COUNT(*) as count
                FROM schools s
                LEFT JOIN school_admins sa ON s.id = sa.school_id
                WHERE sa.school_id IS NULL
            """)
            row = await cur.fetchone()
            count = row["count"] if isinstance(row, dict) else row[0]
            return {"count": count}
    finally:
        conn.close()


