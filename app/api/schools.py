from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional
from pydantic import BaseModel
from app.db.connection import get_db_connection
from app.core.security import get_current_user
from app.core.entitlements import get_school_id_for_user, has_admin_permission, get_admin_info, ADMIN_PERMISSIONS
from app.models.user import User
from datetime import datetime
import json

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


DEFAULT_FEATURE_FLAGS = {
    "messages_enabled": True,
    "homeworks_enabled": True,
    "ratings_enabled": True,
    "teacher_feedback_enabled": True,
    "parent_access_enabled": True,
    "attendance_enabled": True,
    "schedule_enabled": True,
    "analytics_enabled": True,
    "notifications_enabled": True,
}

DEFAULT_WORKFLOW_SETTINGS = {
    "homework_review": {
        "rework_enabled": True,
        "labels": {
            "pending": "Ждёт проверки",
            "reviewed": "Проверено",
            "rework": "На доработку",
            "done_without_file": "Сделал",
            "done_without_file_marked": "Отмечено как сделано",
            "file_submitted": "Есть файл",
        },
    },
    "final_grade_thresholds": {
        "allow_teacher_override": True,
        "thresholds": {
            "3": 2.51,
            "4": 3.51,
            "5": 4.51,
        },
    },
}


class SchoolFeatureSettings(BaseModel):
    feature_flags: dict[str, bool]
    workflow_settings: dict
    updated_at: Optional[datetime] = None


class SchoolFeatureSettingsUpdate(BaseModel):
    feature_flags: Optional[dict[str, bool]] = None
    workflow_settings: Optional[dict] = None


def _normalize_feature_flags(raw: Optional[dict]) -> dict[str, bool]:
    merged = dict(DEFAULT_FEATURE_FLAGS)
    if raw:
        for key, value in raw.items():
            if key in merged:
                merged[key] = bool(value)
    return merged


def _normalize_workflow_settings(raw: Optional[dict]) -> dict:
    merged = json.loads(json.dumps(DEFAULT_WORKFLOW_SETTINGS))
    homework_review = raw.get("homework_review") if isinstance(raw, dict) else None
    if isinstance(homework_review, dict):
        merged["homework_review"]["rework_enabled"] = bool(
            homework_review.get("rework_enabled", True)
        )
        labels = homework_review.get("labels")
        if isinstance(labels, dict):
            for key, default_value in merged["homework_review"]["labels"].items():
                value = labels.get(key)
                if isinstance(value, str) and value.strip():
                    merged["homework_review"]["labels"][key] = value.strip()[:80]
                else:
                    merged["homework_review"]["labels"][key] = default_value

    final_grade = raw.get("final_grade_thresholds") if isinstance(raw, dict) else None
    if isinstance(final_grade, dict):
        merged["final_grade_thresholds"]["allow_teacher_override"] = bool(
            final_grade.get("allow_teacher_override", True)
        )
        raw_thresholds = final_grade.get("thresholds")
        if isinstance(raw_thresholds, dict):
            normalized_thresholds = dict(merged["final_grade_thresholds"]["thresholds"])
            for key in ("3", "4", "5"):
                value = raw_thresholds.get(key)
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    continue
                normalized_thresholds[key] = round(min(max(numeric, 2.0), 5.0), 2)
            if (
                normalized_thresholds["3"]
                <= normalized_thresholds["4"]
                <= normalized_thresholds["5"]
            ):
                merged["final_grade_thresholds"]["thresholds"] = normalized_thresholds
    return merged


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


@router.get("/{school_id}/settings", response_model=SchoolFeatureSettings)
async def get_school_settings(
    school_id: int,
    current_user: User = Depends(get_current_user),
):
    user_school_id = await get_school_id_for_user(current_user)
    if current_user.role in ("owner", "superadmin", "root"):
        pass
    elif user_school_id != school_id:
        raise HTTPException(status_code=403, detail="Access denied to this school")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT feature_flags, workflow_settings, updated_at FROM school_settings WHERE school_id = %s",
                (school_id,),
            )
            row = await cur.fetchone()
            if not row:
                return SchoolFeatureSettings(
                    feature_flags=dict(DEFAULT_FEATURE_FLAGS),
                    workflow_settings=json.loads(json.dumps(DEFAULT_WORKFLOW_SETTINGS)),
                )

            raw_flags = row.get("feature_flags") if isinstance(row, dict) else row[0]
            raw_workflow = row.get("workflow_settings") if isinstance(row, dict) else row[1]
            if isinstance(raw_flags, str):
                try:
                    raw_flags = json.loads(raw_flags)
                except Exception:
                    raw_flags = {}
            elif raw_flags is None:
                raw_flags = {}

            if isinstance(raw_workflow, str):
                try:
                    raw_workflow = json.loads(raw_workflow)
                except Exception:
                    raw_workflow = {}
            elif raw_workflow is None:
                raw_workflow = {}

            updated_at = row.get("updated_at") if isinstance(row, dict) else row[2]
            return SchoolFeatureSettings(
                feature_flags=_normalize_feature_flags(raw_flags),
                workflow_settings=_normalize_workflow_settings(raw_workflow),
                updated_at=updated_at,
            )
    finally:
        conn.close()


@router.put("/{school_id}/settings", response_model=SchoolFeatureSettings)
async def update_school_settings(
    school_id: int,
    payload: SchoolFeatureSettingsUpdate,
    current_user: User = Depends(get_current_user),
):
    user_school_id = await get_school_id_for_user(current_user)
    if current_user.role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Forbidden")
    if current_user.role == "admin" and user_school_id != school_id:
        raise HTTPException(status_code=403, detail="Access denied to this school")

    can_edit = await has_admin_permission(current_user, school_id, "edit_school")
    if not can_edit:
        raise HTTPException(status_code=403, detail="Нет права редактировать настройки школы")

    user_id = current_user.id if hasattr(current_user, "id") else current_user.get("id")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT feature_flags, workflow_settings FROM school_settings WHERE school_id = %s",
                (school_id,),
            )
            existing = await cur.fetchone()

            existing_flags = existing.get("feature_flags") if isinstance(existing, dict) else None
            existing_workflow = existing.get("workflow_settings") if isinstance(existing, dict) else None
            if isinstance(existing_flags, str):
                try:
                    existing_flags = json.loads(existing_flags)
                except Exception:
                    existing_flags = {}
            if isinstance(existing_workflow, str):
                try:
                    existing_workflow = json.loads(existing_workflow)
                except Exception:
                    existing_workflow = {}

            feature_flags = _normalize_feature_flags(
                payload.feature_flags if payload.feature_flags is not None else existing_flags
            )
            workflow_settings = _normalize_workflow_settings(
                payload.workflow_settings if payload.workflow_settings is not None else existing_workflow
            )

            await cur.execute(
                """
                INSERT INTO school_settings (school_id, feature_flags, workflow_settings, updated_by)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    feature_flags = VALUES(feature_flags),
                    workflow_settings = VALUES(workflow_settings),
                    updated_by = VALUES(updated_by),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    school_id,
                    json.dumps(feature_flags),
                    json.dumps(workflow_settings),
                    user_id,
                ),
            )
            await conn.commit()

            try:
                await cur.execute(
                    """
                    INSERT INTO audit_log (school_id, user_id, action, entity_type, entity_id, details)
                    VALUES (%s, %s, 'school_settings_updated', 'school', %s, %s)
                    """,
                    (
                        school_id,
                        user_id,
                        school_id,
                        json.dumps(
                            {
                                "feature_flags": feature_flags,
                                "workflow_settings": workflow_settings,
                            }
                        ),
                    ),
                )
                await conn.commit()
            except Exception:
                pass

            await cur.execute(
                "SELECT feature_flags, workflow_settings, updated_at FROM school_settings WHERE school_id = %s",
                (school_id,),
            )
            row = await cur.fetchone()
            updated_at = row.get("updated_at") if isinstance(row, dict) else row[2]
            return SchoolFeatureSettings(
                feature_flags=feature_flags,
                workflow_settings=workflow_settings,
                updated_at=updated_at,
            )
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
    role = (
        current_user.get("role")
        if isinstance(current_user, dict)
        else getattr(current_user, "role", None)
    )
    if role not in ("owner", "superadmin", "root"):
        user_school_id = await get_school_id_for_user(current_user)
        if user_school_id != school_id:
            raise HTTPException(status_code=403, detail="Access denied")
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
