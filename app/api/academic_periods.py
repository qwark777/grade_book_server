"""API для учебных периодов (семестров). Админ задаёт начало и конец периода."""
from datetime import date
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException

from app.core.security import get_current_user
from app.db.connection import get_db_connection
from pydantic import BaseModel

router = APIRouter()


class AcademicPeriodResponse(BaseModel):
    id: int
    name: str
    start_date: str
    end_date: str
    is_active: bool


class AcademicPeriodUpdate(BaseModel):
    name: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    is_active: Optional[bool] = None


class AcademicPeriodCreate(BaseModel):
    name: str
    start_date: str
    end_date: str
    is_active: bool = False


def _role(current_user) -> str:
    return current_user.get("role", "") if isinstance(current_user, dict) else getattr(current_user, "role", "")


def _check_admin(current_user):
    if _role(current_user) not in ("admin", "owner", "superadmin"):
        raise HTTPException(status_code=403, detail="Только админ может управлять периодами")


def _periods_overlap(start1: str, end1: str, start2: str, end2: str) -> bool:
    """Проверяет пересечение двух периодов (включительно по границам)."""
    return start1 <= end2 and end1 >= start2


@router.post("/academic-periods", response_model=AcademicPeriodResponse)
async def create_academic_period(
    payload: AcademicPeriodCreate,
    current_user=Depends(get_current_user),
):
    """Создать новый учебный период. Только для админа. Периоды не должны пересекаться."""
    _check_admin(current_user)
    if payload.start_date > payload.end_date:
        raise HTTPException(status_code=400, detail="Дата начала не может быть позже даты окончания")
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT start_date, end_date, name FROM academic_periods
                """
            )
            existing = await cursor.fetchall()
            for r in existing:
                ex_start = str(r["start_date"])
                ex_end = str(r["end_date"])
                ex_name = r.get("name", "?")
                if _periods_overlap(
                    payload.start_date, payload.end_date,
                    ex_start, ex_end
                ):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Период пересекается с «{ex_name}» ({ex_start} — {ex_end})"
                    )
            await cursor.execute(
                """
                INSERT INTO academic_periods (name, start_date, end_date, is_active)
                VALUES (%s, %s, %s, %s)
                """,
                (payload.name, payload.start_date, payload.end_date, payload.is_active),
            )
            await conn.commit()
            period_id = cursor.lastrowid
            return {
                "id": period_id,
                "name": payload.name,
                "start_date": payload.start_date,
                "end_date": payload.end_date,
                "is_active": payload.is_active,
            }
    finally:
        conn.close()


@router.get("/academic-periods/current")
async def get_current_academic_period(current_user=Depends(get_current_user)):
    """
    Текущий активный учебный период (семестр). Используется для ограничения
    отображения оценок и расписания до конца семестра.
    """
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id, name, start_date, end_date, is_active
                FROM academic_periods
                WHERE is_active = TRUE
                ORDER BY end_date DESC
                LIMIT 1
                """
            )
            row = await cursor.fetchone()
            if not row:
                # Fallback: последний период по дате окончания
                await cursor.execute(
                    """
                    SELECT id, name, start_date, end_date, is_active
                    FROM academic_periods
                    ORDER BY end_date DESC
                    LIMIT 1
                    """
                )
                row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "name": row["name"],
                "start_date": str(row["start_date"]),
                "end_date": str(row["end_date"]),
                "is_active": bool(row["is_active"]),
            }
    finally:
        conn.close()


@router.get("/academic-periods", response_model=List[AcademicPeriodResponse])
async def list_academic_periods(current_user=Depends(get_current_user)):
    """Список всех учебных периодов. Только для админа/владельца."""
    _check_admin(current_user)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id, name, start_date, end_date, is_active
                FROM academic_periods
                ORDER BY start_date DESC
                """
            )
            rows = await cursor.fetchall()
            return [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "start_date": str(r["start_date"]),
                    "end_date": str(r["end_date"]),
                    "is_active": bool(r["is_active"]),
                }
                for r in rows
            ]
    finally:
        conn.close()


@router.put("/academic-periods/{period_id}")
async def update_academic_period(
    period_id: int,
    payload: AcademicPeriodUpdate,
    current_user=Depends(get_current_user),
):
    """Обновить учебный период (в т.ч. конец семестра). Только для админа. Периоды не должны пересекаться."""
    _check_admin(current_user)
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT start_date, end_date, name FROM academic_periods WHERE id = %s",
                (period_id,),
            )
            current = await cursor.fetchone()
            if not current:
                raise HTTPException(status_code=404, detail="Период не найден")

            new_start = payload.start_date if payload.start_date is not None else str(current["start_date"])
            new_end = payload.end_date if payload.end_date is not None else str(current["end_date"])
            if new_start > new_end:
                raise HTTPException(status_code=400, detail="Дата начала не может быть позже даты окончания")

            await cursor.execute(
                "SELECT id, start_date, end_date, name FROM academic_periods WHERE id != %s",
                (period_id,),
            )
            others = await cursor.fetchall()
            for r in others:
                ex_start = str(r["start_date"])
                ex_end = str(r["end_date"])
                ex_name = r.get("name", "?")
                if _periods_overlap(new_start, new_end, ex_start, ex_end):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Период пересекается с «{ex_name}» ({ex_start} — {ex_end})"
                    )

        updates = []
        params = []
        if payload.name is not None:
            updates.append("name = %s")
            params.append(payload.name)
        if payload.start_date is not None:
            updates.append("start_date = %s")
            params.append(payload.start_date)
        if payload.end_date is not None:
            updates.append("end_date = %s")
            params.append(payload.end_date)
        if payload.is_active is not None:
            updates.append("is_active = %s")
            params.append(payload.is_active)
        if not updates:
            return {"message": "Nothing to update"}
        params.append(period_id)
        async with conn.cursor() as cursor:
            await cursor.execute(
                f"UPDATE academic_periods SET {', '.join(updates)} WHERE id = %s",
                tuple(params),
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Период не найден")
            await conn.commit()
        return {"message": "OK"}
    finally:
        conn.close()
