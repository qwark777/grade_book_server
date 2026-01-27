from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional
from pydantic import BaseModel
from app.db.connection import get_db_connection
from app.core.security import get_current_user

router = APIRouter(prefix="/schools")


class School(BaseModel):
    id: int
    name: str
    address: Optional[str] = None


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


