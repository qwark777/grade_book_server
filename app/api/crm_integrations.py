"""
API для управления интеграциями CRM для школ
Каждая школа может настроить свою CRM систему и связать её с приложением
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from enum import Enum
import json
from app.db.connection import get_db_connection
from app.core.security import get_current_user
from app.models.user import UserInDB
from app.core.entitlements import get_school_id_for_user

router = APIRouter(prefix="/crm-integrations")


class CRMType(str, Enum):
    one_c = "1c"
    bitrix24 = "bitrix24"
    amocrm = "amocrm"
    custom = "custom"
    other = "other"


class SyncDirection(str, Enum):
    app_to_crm = "app_to_crm"
    crm_to_app = "crm_to_app"
    bidirectional = "bidirectional"


class SyncFrequency(str, Enum):
    realtime = "realtime"
    hourly = "hourly"
    daily = "daily"
    weekly = "weekly"
    manual = "manual"


class CRMIntegrationCreate(BaseModel):
    school_id: int
    crm_type: CRMType
    crm_name: str
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    access_token: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    sync_direction: SyncDirection = SyncDirection.app_to_crm
    sync_frequency: SyncFrequency = SyncFrequency.daily
    field_mapping: Optional[dict] = None
    metadata: Optional[dict] = None
    notes: Optional[str] = None


class CRMIntegrationUpdate(BaseModel):
    crm_name: Optional[str] = None
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    access_token: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    is_active: Optional[bool] = None
    sync_direction: Optional[SyncDirection] = None
    sync_frequency: Optional[SyncFrequency] = None
    field_mapping: Optional[dict] = None
    metadata: Optional[dict] = None
    notes: Optional[str] = None


class CRMIntegration(BaseModel):
    id: int
    school_id: int
    crm_type: str
    crm_name: str
    api_url: Optional[str] = None
    is_active: bool
    sync_direction: str
    sync_frequency: str
    last_sync_at: Optional[datetime] = None
    next_sync_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


@router.get("/schools/{school_id}/integrations", response_model=List[CRMIntegration])
async def get_school_integrations(
    school_id: int,
    current_user: UserInDB = Depends(get_current_user)
):
    """Получить список интеграций CRM для школы"""
    # Проверка доступа - только админы школы или владельцы
    role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
    user_school_id = await get_school_id_for_user(current_user)
    
    if role not in ("admin", "owner", "superadmin") and user_school_id != school_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT 
                    id, school_id, crm_type, crm_name, api_url,
                    is_active, sync_direction, sync_frequency,
                    last_sync_at, next_sync_at,
                    created_at, updated_at
                FROM school_crm_integrations
                WHERE school_id = %s
                ORDER BY created_at DESC
            """, (school_id,))
            
            rows = await cursor.fetchall()
            return [
                {
                    "id": row["id"],
                    "school_id": row["school_id"],
                    "crm_type": row["crm_type"],
                    "crm_name": row["crm_name"],
                    "api_url": row.get("api_url"),
                    "is_active": bool(row["is_active"]),
                    "sync_direction": row["sync_direction"],
                    "sync_frequency": row["sync_frequency"],
                    "last_sync_at": row.get("last_sync_at"),
                    "next_sync_at": row.get("next_sync_at"),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
                for row in rows
            ]
    finally:
        conn.close()


@router.get("/integrations/{integration_id}", response_model=dict)
async def get_integration_details(
    integration_id: int,
    current_user: UserInDB = Depends(get_current_user)
):
    """Получить детали интеграции (без секретных ключей)"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT 
                    id, school_id, crm_type, crm_name, api_url,
                    is_active, sync_direction, sync_frequency,
                    last_sync_at, next_sync_at,
                    field_mapping, metadata, notes,
                    created_at, updated_at
                FROM school_crm_integrations
                WHERE id = %s
            """, (integration_id,))
            
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Integration not found")
            
            # Проверка доступа
            role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
            user_school_id = await get_school_id_for_user(current_user)
            
            if role not in ("admin", "owner", "superadmin") and user_school_id != row["school_id"]:
                raise HTTPException(status_code=403, detail="Access denied")
            
            return {
                "id": row["id"],
                "school_id": row["school_id"],
                "crm_type": row["crm_type"],
                "crm_name": row["crm_name"],
                "api_url": row.get("api_url"),
                "is_active": bool(row["is_active"]),
                "sync_direction": row["sync_direction"],
                "sync_frequency": row["sync_frequency"],
                "last_sync_at": row.get("last_sync_at"),
                "next_sync_at": row.get("next_sync_at"),
                "field_mapping": json.loads(row["field_mapping"]) if row.get("field_mapping") else None,
                "metadata": json.loads(row["metadata"]) if row.get("metadata") else None,
                "notes": row.get("notes"),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
    finally:
        conn.close()


@router.post("/schools/{school_id}/integrations", status_code=201)
async def create_integration(
    school_id: int,
    integration: CRMIntegrationCreate,
    current_user: UserInDB = Depends(get_current_user)
):
    """Создать новую интеграцию CRM для школы"""
    # Проверка доступа
    role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
    if role not in ("admin", "owner", "superadmin"):
        raise HTTPException(status_code=403, detail="Access denied")
    
    if integration.school_id != school_id:
        raise HTTPException(status_code=400, detail="School ID mismatch")
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем, что школа существует
            await cursor.execute("SELECT id FROM schools WHERE id = %s", (school_id,))
            if not await cursor.fetchone():
                raise HTTPException(status_code=404, detail="School not found")
            
            # Создаем интеграцию
            await cursor.execute("""
                INSERT INTO school_crm_integrations (
                    school_id, crm_type, crm_name, api_url,
                    api_key, api_secret, access_token,
                    webhook_url, webhook_secret,
                    sync_direction, sync_frequency,
                    field_mapping, metadata, notes
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                school_id,
                integration.crm_type.value,
                integration.crm_name,
                integration.api_url,
                integration.api_key,
                integration.api_secret,
                integration.access_token,
                integration.webhook_url,
                integration.webhook_secret,
                integration.sync_direction.value,
                integration.sync_frequency.value,
                json.dumps(integration.field_mapping) if integration.field_mapping else None,
                json.dumps(integration.metadata) if integration.metadata else None,
                integration.notes,
            ))
            
            integration_id = cursor.lastrowid
            await conn.commit()
            
            return {"id": integration_id, "status": "created"}
    finally:
        conn.close()


@router.put("/integrations/{integration_id}")
async def update_integration(
    integration_id: int,
    integration_update: CRMIntegrationUpdate,
    current_user: UserInDB = Depends(get_current_user)
):
    """Обновить настройки интеграции"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем существование и получаем school_id
            await cursor.execute("SELECT school_id FROM school_crm_integrations WHERE id = %s", (integration_id,))
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Integration not found")
            
            school_id = row["school_id"]
            
            # Проверка доступа
            role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
            user_school_id = await get_school_id_for_user(current_user)
            
            if role not in ("admin", "owner", "superadmin") and user_school_id != school_id:
                raise HTTPException(status_code=403, detail="Access denied")
            
            # Формируем запрос на обновление
            update_fields = []
            params = []
            
            if integration_update.crm_name is not None:
                update_fields.append("crm_name = %s")
                params.append(integration_update.crm_name)
            
            if integration_update.api_url is not None:
                update_fields.append("api_url = %s")
                params.append(integration_update.api_url)
            
            if integration_update.api_key is not None:
                update_fields.append("api_key = %s")
                params.append(integration_update.api_key)
            
            if integration_update.api_secret is not None:
                update_fields.append("api_secret = %s")
                params.append(integration_update.api_secret)
            
            if integration_update.access_token is not None:
                update_fields.append("access_token = %s")
                params.append(integration_update.access_token)
            
            if integration_update.is_active is not None:
                update_fields.append("is_active = %s")
                params.append(integration_update.is_active)
            
            if integration_update.sync_direction is not None:
                update_fields.append("sync_direction = %s")
                params.append(integration_update.sync_direction.value)
            
            if integration_update.sync_frequency is not None:
                update_fields.append("sync_frequency = %s")
                params.append(integration_update.sync_frequency.value)
            
            if integration_update.field_mapping is not None:
                update_fields.append("field_mapping = %s")
                params.append(json.dumps(integration_update.field_mapping))
            
            if integration_update.metadata is not None:
                update_fields.append("metadata = %s")
                params.append(json.dumps(integration_update.metadata))
            
            if integration_update.notes is not None:
                update_fields.append("notes = %s")
                params.append(integration_update.notes)
            
            if not update_fields:
                raise HTTPException(status_code=400, detail="No fields to update")
            
            params.append(integration_id)
            
            query = f"UPDATE school_crm_integrations SET {', '.join(update_fields)} WHERE id = %s"
            await cursor.execute(query, params)
            await conn.commit()
            
            return {"status": "updated"}
    finally:
        conn.close()


@router.delete("/integrations/{integration_id}")
async def delete_integration(
    integration_id: int,
    current_user: UserInDB = Depends(get_current_user)
):
    """Удалить интеграцию"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем существование и получаем school_id
            await cursor.execute("SELECT school_id FROM school_crm_integrations WHERE id = %s", (integration_id,))
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Integration not found")
            
            school_id = row["school_id"]
            
            # Проверка доступа
            role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
            if role not in ("admin", "owner", "superadmin"):
                raise HTTPException(status_code=403, detail="Access denied")
            
            await cursor.execute("DELETE FROM school_crm_integrations WHERE id = %s", (integration_id,))
            await conn.commit()
            
            return {"status": "deleted"}
    finally:
        conn.close()


@router.post("/integrations/{integration_id}/test")
async def test_integration(
    integration_id: int,
    current_user: UserInDB = Depends(get_current_user)
):
    """Тестировать подключение к CRM"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT school_id, crm_type, api_url, api_key, access_token
                FROM school_crm_integrations
                WHERE id = %s
            """, (integration_id,))
            
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Integration not found")
            
            # Проверка доступа
            role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
            user_school_id = await get_school_id_for_user(current_user)
            
            if role not in ("admin", "owner", "superadmin") and user_school_id != row["school_id"]:
                raise HTTPException(status_code=403, detail="Access denied")
            
            # Здесь должна быть логика тестирования подключения
            # Для примера просто возвращаем успех
            return {
                "status": "success",
                "message": "Connection test completed (mock)",
                "details": {
                    "crm_type": row["crm_type"],
                    "api_url": row.get("api_url"),
                }
            }
    finally:
        conn.close()


@router.post("/integrations/{integration_id}/sync")
async def trigger_sync(
    integration_id: int,
    sync_type: str = Query("lessons", description="Тип синхронизации: lessons, enrollments, financial, full"),
    current_user: UserInDB = Depends(get_current_user)
):
    """Запустить синхронизацию данных с CRM"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT school_id FROM school_crm_integrations WHERE id = %s", (integration_id,))
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Integration not found")
            
            school_id = row["school_id"]
            
            # Проверка доступа
            role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
            if role not in ("admin", "owner", "superadmin"):
                raise HTTPException(status_code=403, detail="Access denied")
            
            # Здесь должна быть логика синхронизации
            # Для примера возвращаем успех
            return {
                "status": "started",
                "sync_type": sync_type,
                "integration_id": integration_id,
                "message": "Synchronization started (mock)"
            }
    finally:
        conn.close()
