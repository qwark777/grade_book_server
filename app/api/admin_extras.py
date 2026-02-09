"""Админ: печать списков, ведомости, посещаемость, объявления, история входов, импорт оценок, дублирование класса."""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from datetime import datetime, timedelta

from app.core.security import get_current_user
from app.core.entitlements import get_school_id_for_user
from app.db.connection import get_db_connection

router = APIRouter(prefix="/admin")


async def _get_school_id(current_user) -> Optional[int]:
    from app.models.user import User
    class _U:
        def __init__(self, id_, role_):
            self.id = id_
            self.role = role_
    uid = current_user.get("id") if isinstance(current_user, dict) else getattr(current_user, "id", None)
    role = current_user.get("role", "") if isinstance(current_user, dict) else getattr(current_user, "role", "")
    return await get_school_id_for_user(_U(uid, role))


def _admin_only(current_user):
    role = current_user.get("role", "") if isinstance(current_user, dict) else getattr(current_user, "role", "")
    if role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(403, "Forbidden")


# ========== Печать списка класса ==========
@router.get("/classes/{class_id}/print-list")
async def get_class_print_list(class_id: int, current_user=Depends(get_current_user)):
    """Список учеников класса для печати."""
    _admin_only(current_user)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT c.name as class_name FROM classes c WHERE c.id = %s""",
                (class_id,)
            )
            row = await cur.fetchone()
            if not row:
                raise HTTPException(404, "Класс не найден")
            class_name = row.get("class_name", row[0]) if row else "Класс"
            await cur.execute("""
                SELECT TRIM(BOTH '"' FROM p.full_name) as full_name
                FROM class_students cs
                JOIN profiles p ON p.user_id = cs.student_id
                WHERE cs.class_id = %s
                ORDER BY p.full_name
            """, (class_id,))
            rows = await cur.fetchall()
            students = [r.get("full_name", r[0]) or "—" for r in rows]
            return {"class_name": class_name, "students": students}
    finally:
        conn.close()


# ========== Ведомость оценок ==========
@router.get("/classes/{class_id}/grade-report")
async def get_grade_report(
    class_id: int,
    subject_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user)
):
    """Ведомость оценок: ученики, предмет, оценки."""
    _admin_only(current_user)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute("SELECT name FROM classes WHERE id = %s", (class_id,))
            cr = await cur.fetchone()
            if not cr:
                raise HTTPException(404, "Класс не найден")
            class_name = cr.get("name", cr[0])
            await cur.execute("SELECT id, name FROM subjects ORDER BY name")
            subjects = [(r.get("id"), r.get("name")) for r in await cur.fetchall()]
            sj = "AND g.subject_id = %s" if subject_id else ""
            params = [class_id]
            if subject_id:
                params.append(subject_id)
            await cur.execute(f"""
                SELECT cs.student_id, TRIM(BOTH '"' FROM p.full_name) as full_name,
                       g.subject_id, s.name as subject_name, g.value, g.date
                FROM class_students cs
                JOIN profiles p ON p.user_id = cs.student_id
                LEFT JOIN grades g ON g.student_id = cs.student_id {sj}
                LEFT JOIN subjects s ON s.id = g.subject_id
                WHERE cs.class_id = %s
                ORDER BY p.full_name, g.date DESC
            """, params)
            rows = await cur.fetchall()
            by_student = {}
            for r in rows:
                sid = r.get("student_id") if isinstance(r, dict) else r[0]
                name = r.get("full_name") or "—"
                if sid not in by_student:
                    by_student[sid] = {"student_id": sid, "full_name": name, "grades": []}
                if r.get("value") is not None:
                    by_student[sid]["grades"].append({
                        "subject_id": r.get("subject_id"),
                        "subject_name": r.get("subject_name"),
                        "value": r.get("value"),
                        "date": str(r.get("date")) if r.get("date") else None,
                    })
            return {
                "class_name": class_name,
                "subjects": [{"id": s[0], "name": s[1]} for s in subjects],
                "students": list(by_student.values()),
            }
    finally:
        conn.close()


# ========== Сводка посещаемости ==========
@router.get("/attendance-summary")
async def get_attendance_summary(
    class_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user=Depends(get_current_user)
):
    """Сводка посещаемости по классу."""
    _admin_only(current_user)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            df = date_from or (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            dt = date_to or datetime.now().strftime("%Y-%m-%d")
            params = [df, dt]
            cf = "AND cs.class_id = %s" if class_id else ""
            if class_id:
                params = [class_id] + params
            await cur.execute(f"""
                SELECT cs.student_id, cs.class_id, c.name as class_name,
                       TRIM(BOTH '"' FROM p.full_name) as full_name,
                       COUNT(a.id) as total,
                       SUM(CASE WHEN a.status = 'present' THEN 1 ELSE 0 END) as present,
                       SUM(CASE WHEN a.status IN ('absent', 'late') THEN 1 ELSE 0 END) as absent
                FROM class_students cs
                JOIN classes c ON c.id = cs.class_id
                JOIN profiles p ON p.user_id = cs.student_id
                LEFT JOIN attendance a ON a.student_id = cs.student_id
                    AND a.date >= %s AND a.date <= %s
                WHERE 1=1 {cf}
                GROUP BY cs.student_id, cs.class_id, c.name, p.full_name
            """, params)
            rows = await cur.fetchall()
            result = []
            for r in rows:
                total = r.get("total") or 0
                present = r.get("present") or 0
                rate = (present / total * 100) if total > 0 else None
                result.append({
                    "student_id": r.get("student_id"),
                    "class_id": r.get("class_id"),
                    "class_name": r.get("class_name"),
                    "full_name": r.get("full_name"),
                    "total": total,
                    "present": present,
                    "absent": r.get("absent") or 0,
                    "rate": round(rate, 1) if rate is not None else None,
                })
            return {"date_from": df, "date_to": dt, "items": result}
    finally:
        conn.close()


# ========== Часто пропускающие ==========
@router.get("/attendance-frequent-absences")
async def get_frequent_absences(
    min_absent_days: int = Query(5, ge=1),
    days: int = Query(30, ge=7),
    current_user=Depends(get_current_user)
):
    """Ученики с большим числом пропусков."""
    _admin_only(current_user)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            await cur.execute("""
                SELECT a.student_id, TRIM(BOTH '"' FROM p.full_name) as full_name,
                       c.name as class_name, cs.class_id,
                       SUM(CASE WHEN a.status IN ('absent','late') THEN 1 ELSE 0 END) as absent_count
                FROM attendance a
                JOIN profiles p ON p.user_id = a.student_id
                JOIN class_students cs ON cs.student_id = a.student_id
                JOIN classes c ON c.id = cs.class_id
                WHERE a.date >= %s
                GROUP BY a.student_id, p.full_name, c.name, cs.class_id
                HAVING absent_count >= %s
                ORDER BY absent_count DESC
            """, (date_from, min_absent_days))
            rows = await cur.fetchall()
            return [
                {
                    "student_id": r.get("student_id"),
                    "full_name": r.get("full_name"),
                    "class_name": r.get("class_name"),
                    "class_id": r.get("class_id"),
                    "absent_count": r.get("absent_count"),
                }
                for r in rows
            ]
    finally:
        conn.close()


# ========== Объявления ==========
class AnnouncementCreate(BaseModel):
    title: str
    content: str
    target_type: str = "all"
    target_class_id: Optional[int] = None


@router.get("/announcements")
async def list_announcements(
    limit: int = Query(50, le=100),
    current_user=Depends(get_current_user)
):
    _admin_only(current_user)
    sid = await _get_school_id(current_user)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            if sid:
                await cur.execute("""
                    SELECT a.id, a.title, a.content, a.target_type, a.target_class_id, a.created_at,
                           c.name as class_name
                    FROM announcements a
                    LEFT JOIN classes c ON c.id = a.target_class_id
                    WHERE a.school_id = %s
                    ORDER BY a.created_at DESC
                    LIMIT %s
                """, (sid, limit))
            else:
                await cur.execute("""
                    SELECT a.id, a.title, a.content, a.target_type, a.target_class_id, a.created_at,
                           c.name as class_name
                    FROM announcements a
                    LEFT JOIN classes c ON c.id = a.target_class_id
                    ORDER BY a.created_at DESC
                    LIMIT %s
                """, (limit,))
            rows = await cur.fetchall()
            return [
                {
                    "id": r.get("id"),
                    "title": r.get("title"),
                    "content": r.get("content"),
                    "target_type": r.get("target_type"),
                    "target_class_id": r.get("target_class_id"),
                    "class_name": r.get("class_name"),
                    "created_at": r.get("created_at").isoformat() if r.get("created_at") else None,
                }
                for r in rows
            ]
    finally:
        conn.close()


@router.post("/announcements")
async def create_announcement(
    payload: AnnouncementCreate,
    school_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user)
):
    _admin_only(current_user)
    sid = await _get_school_id(current_user) or school_id
    uid = current_user.get("id") if isinstance(current_user, dict) else getattr(current_user, "id", None)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO announcements (school_id, title, content, target_type, target_class_id, created_by)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (sid, payload.title[:255], payload.content[:5000], payload.target_type, payload.target_class_id, uid))
            await conn.commit()
            return {"id": cur.lastrowid, "status": "created"}
    finally:
        conn.close()


# ========== История входов ==========
@router.get("/login-history")
async def get_login_history(
    limit: int = Query(50, le=200),
    user_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user)
):
    _admin_only(current_user)
    sid = await _get_school_id(current_user)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            uf = "AND ll.user_id = %s" if user_id else ""
            if sid:
                params = [sid]
                if user_id:
                    params.append(user_id)
                params.append(limit)
                await cur.execute(f"""
                    SELECT ll.id, ll.user_id, ll.created_at, ll.ip_address,
                           TRIM(BOTH '"' FROM p.full_name) as full_name, u.username
                    FROM login_log ll
                    JOIN users u ON u.id = ll.user_id
                    LEFT JOIN profiles p ON p.user_id = ll.user_id
                    WHERE ll.school_id = %s {uf}
                    ORDER BY ll.created_at DESC
                    LIMIT %s
                """, params)
            else:
                params = [user_id, limit] if user_id else [limit]
                await cur.execute(f"""
                    SELECT ll.id, ll.user_id, ll.created_at, ll.ip_address,
                           TRIM(BOTH '"' FROM p.full_name) as full_name, u.username
                    FROM login_log ll
                    JOIN users u ON u.id = ll.user_id
                    LEFT JOIN profiles p ON p.user_id = ll.user_id
                    WHERE 1=1 {uf}
                    ORDER BY ll.created_at DESC
                    LIMIT %s
                """, params)
            rows = await cur.fetchall()
            return [
                {
                    "id": r.get("id"),
                    "user_id": r.get("user_id"),
                    "full_name": r.get("full_name"),
                    "username": r.get("username"),
                    "created_at": r.get("created_at").isoformat() if r.get("created_at") else None,
                    "ip_address": r.get("ip_address"),
                }
                for r in rows
            ]
    finally:
        conn.close()


# ========== Импорт оценок ==========
class GradeImportEntry(BaseModel):
    student_id: int
    subject_id: int
    value: int
    date: str


class GradeImportRequest(BaseModel):
    entries: List[GradeImportEntry]


@router.post("/grades/import")
async def import_grades(
    payload: GradeImportRequest,
    current_user=Depends(get_current_user)
):
    _admin_only(current_user)
    uid = current_user.get("id") if isinstance(current_user, dict) else getattr(current_user, "id", None)
    conn = await get_db_connection()
    created = 0
    try:
        async with conn.cursor() as cur:
            for e in payload.entries:
                if not 1 <= e.value <= 5:
                    continue
                await cur.execute("""
                    INSERT INTO grades (student_id, subject_id, value, date, teacher_id)
                    VALUES (%s, %s, %s, %s, %s)
                """, (e.student_id, e.subject_id, e.value, e.date, uid))
                created += 1
            await conn.commit()
        return {"created": created}
    except Exception as ex:
        await conn.rollback()
        raise HTTPException(500, str(ex))
    finally:
        conn.close()


# ========== Дублирование класса ==========
class DuplicateClassRequest(BaseModel):
    new_name: str


@router.post("/classes/{class_id}/duplicate")
async def duplicate_class(
    class_id: int,
    payload: DuplicateClassRequest,
    school_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user)
):
    _admin_only(current_user)
    sid = await _get_school_id(current_user) or school_id
    if not sid:
        raise HTTPException(400, "Укажите school_id")
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, academic_year FROM classes WHERE id = %s AND school_id = %s",
                (class_id, sid)
            )
            row = await cur.fetchone()
            if not row:
                raise HTTPException(404, "Класс не найден")
            ay = row.get("academic_year") or row[1]
            await cur.execute("""
                INSERT INTO classes (name, academic_year, school_id) VALUES (%s, %s, %s)
            """, (payload.new_name.strip(), ay, sid))
            new_id = cur.lastrowid
            await cur.execute("""
                INSERT INTO class_students (class_id, student_id)
                SELECT %s, student_id FROM class_students WHERE class_id = %s
            """, (new_id, class_id))
            await conn.commit()
        return {"new_class_id": new_id, "status": "created"}
    except HTTPException:
        raise
    except Exception as ex:
        await conn.rollback()
        raise HTTPException(500, str(ex))
    finally:
        conn.close()
