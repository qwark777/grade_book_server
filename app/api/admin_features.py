"""Admin features: audit log, notes, backup export, students import"""
from typing import List, Optional
import random
import secrets
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from datetime import datetime

from app.core.security import get_current_user, get_password_hash
from app.core.entitlements import get_school_id_for_user, has_admin_permission
from app.db.connection import get_db_connection
from app.models.user import User

router = APIRouter(prefix="/admin")


class AuditLogItem(BaseModel):
    id: int
    action: str
    entity_type: Optional[str]
    entity_id: Optional[int]
    details: Optional[dict]
    user_name: Optional[str]
    created_at: datetime


class AdminNoteItem(BaseModel):
    id: int
    content: str
    is_pinned: bool
    created_at: datetime
    updated_at: datetime


class AdminNoteCreate(BaseModel):
    content: str
    is_pinned: bool = False


async def _get_school_id(current_user: User) -> Optional[int]:
    return await get_school_id_for_user(current_user)


def _log_audit(school_id: Optional[int], user_id: int, action: str, entity_type: str = None, entity_id: int = None, details: dict = None):
    """Helper to log audit - call via background task in real impl."""
    import asyncio
    async def _do():
        conn = await get_db_connection()
        try:
            async with conn.cursor() as cur:
                import json
                await cur.execute(
                    """INSERT INTO audit_log (school_id, user_id, action, entity_type, entity_id, details)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (school_id, user_id, action, entity_type, entity_id, json.dumps(details) if details else None)
                )
                await conn.commit()
        except Exception:
            pass
        finally:
            conn.close()
    asyncio.create_task(_do())


@router.get("/audit-log", response_model=List[AuditLogItem])
async def get_audit_log(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    current_user: User = Depends(get_current_user)
):
    """Get audit log for school. Admin only."""
    if current_user.role not in ("admin", "owner", "superadmin"):
        raise HTTPException(403, "Forbidden")
    school_id = await _get_school_id(current_user)
    if current_user.role == "admin" and not school_id:
        return []

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            where = ["1=1"]
            params = []
            if school_id:
                where.append("al.school_id = %s")
                params.append(school_id)
            if date_from:
                where.append("al.created_at >= %s")
                params.append(date_from)
            if date_to:
                where.append("al.created_at <= %s")
                params.append(date_to)
            params.extend([limit, offset])
            await cur.execute(f"""
                SELECT al.id, al.action, al.entity_type, al.entity_id, al.details, al.created_at,
                       COALESCE(p.full_name, u.username) as user_name
                FROM audit_log al
                LEFT JOIN users u ON u.id = al.user_id
                LEFT JOIN profiles p ON p.user_id = u.id
                WHERE {" AND ".join(where)}
                ORDER BY al.created_at DESC
                LIMIT %s OFFSET %s
            """, params)
            rows = await cur.fetchall()
            result = []
            for r in rows:
                import json
                d = r.get("details") if isinstance(r, dict) else r[4]
                details = json.loads(d) if isinstance(d, str) and d else d
                result.append(AuditLogItem(
                    id=r["id"] if isinstance(r, dict) else r[0],
                    action=r["action"] if isinstance(r, dict) else r[1],
                    entity_type=r.get("entity_type") if isinstance(r, dict) else r[2],
                    entity_id=r.get("entity_id") if isinstance(r, dict) else r[3],
                    details=details,
                    user_name=r.get("user_name") if isinstance(r, dict) else r[5],
                    created_at=r["created_at"] if isinstance(r, dict) else r[6],
                ))
            return result
    finally:
        conn.close()


@router.get("/notes", response_model=List[AdminNoteItem])
async def get_admin_notes(current_user: User = Depends(get_current_user)):
    """Get admin quick notes for school."""
    if current_user.role not in ("admin", "owner", "superadmin"):
        raise HTTPException(403, "Forbidden")
    school_id = await _get_school_id(current_user)
    if current_user.role == "admin" and not school_id:
        return []

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            if school_id:
                await cur.execute(
                    """SELECT id, content, is_pinned, created_at, updated_at FROM admin_notes
                       WHERE school_id = %s ORDER BY is_pinned DESC, updated_at DESC""",
                    (school_id,)
                )
            else:
                await cur.execute(
                    """SELECT id, content, is_pinned, created_at, updated_at FROM admin_notes
                       ORDER BY is_pinned DESC, updated_at DESC LIMIT 20"""
                )
            rows = await cur.fetchall()
            return [AdminNoteItem(
                id=r["id"] if isinstance(r, dict) else r[0],
                content=r["content"] if isinstance(r, dict) else r[1],
                is_pinned=bool(r.get("is_pinned") if isinstance(r, dict) else r[2]),
                created_at=r["created_at"] if isinstance(r, dict) else r[3],
                updated_at=r["updated_at"] if isinstance(r, dict) else r[4],
            ) for r in rows]
    finally:
        conn.close()


@router.post("/notes", status_code=201)
async def create_admin_note(payload: AdminNoteCreate, current_user: User = Depends(get_current_user)):
    """Create admin note."""
    if current_user.role not in ("admin", "owner", "superadmin"):
        raise HTTPException(403, "Forbidden")
    school_id = await _get_school_id(current_user)
    if current_user.role == "admin" and not school_id:
        raise HTTPException(400, "School not determined")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            sid = school_id
            if sid is None:
                raise HTTPException(400, "Owner must specify school for notes")
            await cur.execute(
                """INSERT INTO admin_notes (school_id, user_id, content, is_pinned)
                   VALUES (%s, %s, %s, %s)""",
                (sid, current_user.id, payload.content[:2000], payload.is_pinned)
            )
            await conn.commit()
            return {"id": cur.lastrowid, "status": "created"}
    finally:
        conn.close()


@router.put("/notes/{note_id}")
async def update_admin_note(note_id: int, payload: AdminNoteCreate, current_user: User = Depends(get_current_user)):
    """Update admin note."""
    if current_user.role not in ("admin", "owner", "superadmin"):
        raise HTTPException(403, "Forbidden")
    school_id = await _get_school_id(current_user)

    if school_id is None:
        raise HTTPException(400, "School not determined")
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE admin_notes SET content=%s, is_pinned=%s WHERE id=%s AND school_id=%s",
                (payload.content[:2000], payload.is_pinned, note_id, school_id)
            )
            await conn.commit()
            if cur.rowcount == 0:
                raise HTTPException(404, "Note not found")
            return {"status": "ok"}
    finally:
        conn.close()


@router.delete("/notes/{note_id}")
async def delete_admin_note(note_id: int, current_user: User = Depends(get_current_user)):
    """Delete admin note."""
    if current_user.role not in ("admin", "owner", "superadmin"):
        raise HTTPException(403, "Forbidden")
    school_id = await _get_school_id(current_user)
    if school_id is None:
        raise HTTPException(400, "School not determined")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM admin_notes WHERE id=%s AND school_id=%s",
                (note_id, school_id)
            )
            await conn.commit()
            if cur.rowcount == 0:
                raise HTTPException(404, "Note not found")
            return {"status": "deleted"}
    finally:
        conn.close()


@router.get("/backup")
async def export_school_backup(
    school_id_param: Optional[int] = Query(None, alias="school_id"),
    current_user: User = Depends(get_current_user)
):
    """Export school data as JSON backup. Admin only. Owner may pass school_id."""
    if current_user.role not in ("admin", "owner", "superadmin"):
        raise HTTPException(403, "Forbidden")
    school_id = await _get_school_id(current_user)
    if school_id is None:
        school_id = school_id_param
    if school_id is None:
        raise HTTPException(400, "School not determined. Pass school_id for owner.")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            backup = {"exported_at": datetime.utcnow().isoformat(), "school_id": school_id, "data": {}}

            if school_id:
                await cur.execute(
                    "SELECT id, name, address, COALESCE(email,'') as email, COALESCE(phone,'') as phone FROM schools WHERE id=%s",
                    (school_id,)
                )
                row = await cur.fetchone()
                if row:
                    backup["data"]["school"] = dict(row) if hasattr(row, "keys") else {
                        "id": row[0], "name": row[1], "address": row[2],
                        "email": getattr(row, "email", None) or (row[3] if len(row) > 3 else None),
                        "phone": getattr(row, "phone", None) or (row[4] if len(row) > 4 else None),
                    }

            await cur.execute(
                "SELECT id, name, academic_year, school_id, COALESCE(is_archived,0) as is_archived FROM classes WHERE school_id=%s",
                (school_id,)
            )
            classes = await cur.fetchall()
            backup["data"]["classes"] = [dict(c) if hasattr(c, "keys") else {"id": c[0], "name": c[1], "academic_year": c[2], "school_id": c[3], "is_archived": c[4]} for c in classes]

            class_ids = [c["id"] if hasattr(c, "keys") else c[0] for c in classes] if classes else []
            if class_ids:
                placeholders = ",".join(["%s"] * len(class_ids))
                await cur.execute(f"SELECT * FROM class_students WHERE class_id IN ({placeholders})", tuple(class_ids))
                backup["data"]["class_students"] = [dict(r) if hasattr(r, "keys") else {} for r in await cur.fetchall()]

            return backup
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()


class StudentImportEntry(BaseModel):
    full_name: str
    username: Optional[str] = None
    password: Optional[str] = None
    class_name: Optional[str] = None


class StudentImportRequest(BaseModel):
    entries: List[StudentImportEntry]


@router.post("/students/import")
async def import_students(
    payload: StudentImportRequest,
    school_id: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user)
):
    """Массовый импорт учеников. Формат: full_name, username (опц), password (опц), class_name (опц)."""
    if current_user.role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(403, "Forbidden")
    sid = await _get_school_id(current_user)
    if sid is None:
        sid = school_id
    if sid is None and current_user.role == "admin":
        raise HTTPException(400, "Укажите school_id")

    conn = await get_db_connection()
    created = 0
    errors = []
    try:
        async with conn.cursor() as cur:
            for i, e in enumerate(payload.entries):
                if not e.full_name or not e.full_name.strip():
                    errors.append(f"Строка {i+1}: пустое имя")
                    continue
                full_name = e.full_name.strip()

                username = (e.username or "").strip()
                if not username:
                    base = full_name.lower().replace(" ", "_").replace(".", "_")
                    username = f"{base}_{random.randint(1000, 9999)}"
                while True:
                    await cur.execute("SELECT id FROM users WHERE username=%s", (username,))
                    if await cur.fetchone():
                        username = f"{username}_{random.randint(100, 999)}"
                    else:
                        break

                password = (e.password or "").strip() or secrets.token_urlsafe(12)
                hashed = get_password_hash(password)

                await cur.execute(
                    "INSERT INTO users (username, hashed_password, role) VALUES (%s, %s, 'student')",
                    (username, hashed)
                )
                student_id = cur.lastrowid
                await cur.execute(
                    "INSERT INTO profiles (user_id, full_name, work_place, location, bio) VALUES (%s, %s, '', '', '')",
                    (student_id, full_name)
                )

                class_name = (e.class_name or "").strip()
                if class_name and sid:
                    await cur.execute(
                        "SELECT id FROM classes WHERE school_id=%s AND name=%s AND COALESCE(is_archived,0)=0 LIMIT 1",
                        (sid, class_name)
                    )
                    row = await cur.fetchone()
                    if row:
                        cid = row["id"] if isinstance(row, dict) else row[0]
                        await cur.execute(
                            "INSERT IGNORE INTO class_students (class_id, student_id) VALUES (%s, %s)",
                            (cid, student_id)
                        )
                created += 1
            await conn.commit()
        return {"created": created, "errors": errors[:20]}
    except Exception as ex:
        await conn.rollback()
        raise HTTPException(500, str(ex))
    finally:
        conn.close()
