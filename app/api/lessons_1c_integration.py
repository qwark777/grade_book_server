"""
API endpoints for 1C integration with additional lessons
Provides data export in formats compatible with 1C
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from datetime import datetime, date
from app.core.security import get_current_user
from app.models.user import UserInDB
from app.db.connection import get_db_connection
from pydantic import BaseModel
import json
import xml.etree.ElementTree as ET

router = APIRouter()


class Lesson1CExport(BaseModel):
    """Формат данных занятия для экспорта в 1С"""
    id: int
    title: str
    subject: str
    tutor_name: str
    tutor_id: int
    price: Optional[float] = None
    duration_minutes: int
    is_online: bool
    location: Optional[str] = None
    max_students: int
    enrolled_count: int
    created_at: str
    updated_at: str


class Enrollment1CExport(BaseModel):
    """Формат данных записи на занятие для экспорта в 1С"""
    enrollment_id: int
    lesson_id: int
    lesson_title: str
    student_id: int
    student_name: str
    enrollment_date: str
    status: str
    price: Optional[float] = None
    payment_status: Optional[str] = None


@router.get("/lessons/export/1c/json")
async def export_lessons_to_1c_json(
    date_from: Optional[str] = Query(None, description="Дата начала периода (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Дата окончания периода (YYYY-MM-DD)"),
    school_id: Optional[int] = Query(None),
    current_user: UserInDB = Depends(get_current_user)
):
    """
    Экспорт дополнительных занятий в JSON формате для 1С
    """
    # Проверка прав доступа (только админы)
    if current_user.role not in ["admin", "owner", "superadmin"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            query = """
                SELECT 
                    l.id,
                    l.title,
                    l.subject,
                    p.full_name as tutor_name,
                    l.tutor_id,
                    l.price,
                    l.duration_minutes,
                    l.is_online,
                    l.location,
                    l.max_students,
                    COUNT(e.id) as enrolled_count,
                    l.created_at,
                    l.updated_at
                FROM additional_lessons l
                LEFT JOIN profiles p ON l.tutor_id = p.user_id
                LEFT JOIN lesson_enrollments e ON l.id = e.lesson_id AND e.status = 'enrolled'
                WHERE 1=1
            """
            params = []
            
            if school_id:
                query += " AND l.school_id = %s"
                params.append(school_id)
            
            if date_from:
                query += " AND DATE(l.created_at) >= %s"
                params.append(date_from)
            
            if date_to:
                query += " AND DATE(l.created_at) <= %s"
                params.append(date_to)
            
            query += " GROUP BY l.id ORDER BY l.created_at DESC"
            
            await cursor.execute(query, params)
            lessons = await cursor.fetchall()
            
            result = []
            for lesson in lessons:
                result.append({
                    "id": lesson["id"],
                    "title": lesson["title"],
                    "subject": lesson["subject"],
                    "tutor_name": lesson.get("tutor_name", ""),
                    "tutor_id": lesson["tutor_id"],
                    "price": float(lesson["price"]) if lesson.get("price") else None,
                    "duration_minutes": lesson["duration_minutes"],
                    "is_online": bool(lesson["is_online"]),
                    "location": lesson.get("location"),
                    "max_students": lesson["max_students"],
                    "enrolled_count": lesson["enrolled_count"],
                    "created_at": lesson["created_at"].isoformat() if lesson.get("created_at") else None,
                    "updated_at": lesson["updated_at"].isoformat() if lesson.get("updated_at") else None,
                })
            
            return {
                "export_date": datetime.now().isoformat(),
                "total_lessons": len(result),
                "lessons": result
            }
    finally:
        conn.close()


@router.get("/lessons/export/1c/xml")
async def export_lessons_to_1c_xml(
    date_from: Optional[str] = Query(None, description="Дата начала периода (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Дата окончания периода (YYYY-MM-DD)"),
    school_id: Optional[int] = Query(None),
    current_user: UserInDB = Depends(get_current_user)
):
    """
    Экспорт дополнительных занятий в XML формате для 1С
    """
    # Проверка прав доступа
    if current_user.role not in ["admin", "owner", "superadmin"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            query = """
                SELECT 
                    l.id,
                    l.title,
                    l.subject,
                    p.full_name as tutor_name,
                    l.tutor_id,
                    l.price,
                    l.duration_minutes,
                    l.is_online,
                    l.location,
                    l.max_students,
                    COUNT(e.id) as enrolled_count,
                    l.created_at,
                    l.updated_at
                FROM additional_lessons l
                LEFT JOIN profiles p ON l.tutor_id = p.user_id
                LEFT JOIN lesson_enrollments e ON l.id = e.lesson_id AND e.status = 'enrolled'
                WHERE 1=1
            """
            params = []
            
            if school_id:
                query += " AND l.school_id = %s"
                params.append(school_id)
            
            if date_from:
                query += " AND DATE(l.created_at) >= %s"
                params.append(date_from)
            
            if date_to:
                query += " AND DATE(l.created_at) <= %s"
                params.append(date_to)
            
            query += " GROUP BY l.id ORDER BY l.created_at DESC"
            
            await cursor.execute(query, params)
            lessons = await cursor.fetchall()
            
            # Создаем XML структуру
            root = ET.Element("lessons_export")
            root.set("export_date", datetime.now().isoformat())
            root.set("total_lessons", str(len(lessons)))
            
            for lesson in lessons:
                lesson_elem = ET.SubElement(root, "lesson")
                ET.SubElement(lesson_elem, "id").text = str(lesson["id"])
                ET.SubElement(lesson_elem, "title").text = lesson["title"]
                ET.SubElement(lesson_elem, "subject").text = lesson["subject"]
                ET.SubElement(lesson_elem, "tutor_name").text = lesson.get("tutor_name", "")
                ET.SubElement(lesson_elem, "tutor_id").text = str(lesson["tutor_id"])
                if lesson.get("price"):
                    ET.SubElement(lesson_elem, "price").text = str(float(lesson["price"]))
                ET.SubElement(lesson_elem, "duration_minutes").text = str(lesson["duration_minutes"])
                ET.SubElement(lesson_elem, "is_online").text = "true" if lesson["is_online"] else "false"
                if lesson.get("location"):
                    ET.SubElement(lesson_elem, "location").text = lesson["location"]
                ET.SubElement(lesson_elem, "max_students").text = str(lesson["max_students"])
                ET.SubElement(lesson_elem, "enrolled_count").text = str(lesson["enrolled_count"])
                if lesson.get("created_at"):
                    ET.SubElement(lesson_elem, "created_at").text = lesson["created_at"].isoformat()
                if lesson.get("updated_at"):
                    ET.SubElement(lesson_elem, "updated_at").text = lesson["updated_at"].isoformat()
            
            xml_string = ET.tostring(root, encoding='unicode')
            return {
                "content": xml_string,
                "content_type": "application/xml"
            }
    finally:
        conn.close()


@router.get("/lessons/enrollments/export/1c/json")
async def export_enrollments_to_1c_json(
    date_from: Optional[str] = Query(None, description="Дата начала периода (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Дата окончания периода (YYYY-MM-DD)"),
    lesson_id: Optional[int] = Query(None),
    school_id: Optional[int] = Query(None),
    current_user: UserInDB = Depends(get_current_user)
):
    """
    Экспорт записей на занятия в JSON формате для 1С
    Включает информацию о студентах и платежах
    """
    if current_user.role not in ["admin", "owner", "superadmin"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            query = """
                SELECT 
                    e.id as enrollment_id,
                    e.lesson_id,
                    l.title as lesson_title,
                    l.price,
                    e.student_id,
                    p.full_name as student_name,
                    e.enrolled_at as enrollment_date,
                    e.status,
                    e.payment_status
                FROM lesson_enrollments e
                JOIN additional_lessons l ON e.lesson_id = l.id
                LEFT JOIN profiles p ON e.student_id = p.user_id
                WHERE 1=1
            """
            params = []
            
            if lesson_id:
                query += " AND e.lesson_id = %s"
                params.append(lesson_id)
            
            if school_id:
                query += " AND l.school_id = %s"
                params.append(school_id)
            
            if date_from:
                query += " AND DATE(e.enrolled_at) >= %s"
                params.append(date_from)
            
            if date_to:
                query += " AND DATE(e.enrolled_at) <= %s"
                params.append(date_to)
            
            query += " ORDER BY e.enrollment_date DESC"
            
            await cursor.execute(query, params)
            enrollments = await cursor.fetchall()
            
            result = []
            for enrollment in enrollments:
                result.append({
                    "enrollment_id": enrollment["enrollment_id"],
                    "lesson_id": enrollment["lesson_id"],
                    "lesson_title": enrollment["lesson_title"],
                    "student_id": enrollment["student_id"],
                    "student_name": enrollment.get("student_name", ""),
                    "enrollment_date": enrollment["enrollment_date"].isoformat() if enrollment.get("enrollment_date") and hasattr(enrollment["enrollment_date"], 'isoformat') else str(enrollment.get("enrollment_date", "")),
                    "status": enrollment["status"],
                    "price": float(enrollment["price"]) if enrollment.get("price") else None,
                    "payment_status": enrollment.get("payment_status"),
                })
            
            return {
                "export_date": datetime.now().isoformat(),
                "total_enrollments": len(result),
                "enrollments": result
            }
    finally:
        conn.close()


@router.get("/lessons/financial/export/1c/json")
async def export_financial_data_to_1c(
    date_from: Optional[str] = Query(None, description="Дата начала периода (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Дата окончания периода (YYYY-MM-DD)"),
    school_id: Optional[int] = Query(None),
    current_user: UserInDB = Depends(get_current_user)
):
    """
    Экспорт финансовых данных по дополнительным занятиям для 1С
    Суммирует доходы, количество записей, статистику по занятиям
    """
    if current_user.role not in ["admin", "owner", "superadmin"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Общая статистика по занятиям и записям
            query = """
                SELECT 
                    COUNT(DISTINCT l.id) as total_lessons,
                    COUNT(e.id) as total_enrollments,
                    SUM(CASE WHEN e.status = 'enrolled' THEN l.price ELSE 0 END) as total_revenue,
                    SUM(CASE WHEN e.status = 'enrolled' AND e.payment_status = 'paid' THEN l.price ELSE 0 END) as paid_revenue,
                    SUM(CASE WHEN e.status = 'enrolled' AND e.payment_status = 'pending' THEN l.price ELSE 0 END) as pending_revenue
                FROM additional_lessons l
                LEFT JOIN lesson_enrollments e ON l.id = e.lesson_id
                WHERE 1=1
            """
            params = []
            
            if school_id:
                query += " AND l.school_id = %s"
                params.append(school_id)
            
            if date_from:
                query += " AND DATE(COALESCE(e.enrolled_at, l.created_at)) >= %s"
                params.append(date_from)
            
            if date_to:
                query += " AND DATE(COALESCE(e.enrolled_at, l.created_at)) <= %s"
                params.append(date_to)
            
            await cursor.execute(query, params)
            summary = await cursor.fetchone()
            
            return {
                "export_date": datetime.now().isoformat(),
                "period": {
                    "from": date_from,
                    "to": date_to
                },
                "summary": {
                    "total_lessons": summary.get("total_lessons", 0),
                    "total_enrollments": summary.get("total_enrollments", 0),
                    "total_revenue": float(summary.get("total_revenue", 0) or 0),
                    "paid_revenue": float(summary.get("paid_revenue", 0) or 0),
                    "pending_revenue": float(summary.get("pending_revenue", 0) or 0),
                }
            }
    finally:
        conn.close()
