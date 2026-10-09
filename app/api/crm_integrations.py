"""
API для управления интеграциями CRM для школ
Каждая школа может настроить свою CRM систему и связать её с приложением
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from enum import Enum
from urllib import request as urlrequest
from urllib import error as urlerror
from urllib.parse import urlencode, urlparse
import asyncio
import ipaddress
import json
import socket
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


def _current_user_role(current_user) -> Optional[str]:
    return current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)


def _is_owner_role(role: Optional[str]) -> bool:
    return role in ("owner", "superadmin", "root")


async def _ensure_school_access(current_user, school_id: int) -> None:
    role = _current_user_role(current_user)
    if role not in ("admin", "owner", "superadmin", "root"):
        raise HTTPException(status_code=403, detail="Access denied")
    if _is_owner_role(role):
        return
    user_school_id = await get_school_id_for_user(current_user)
    if user_school_id != school_id:
        raise HTTPException(status_code=403, detail="Access denied")


async def _ensure_crm_integrations_table(cursor) -> None:
    await cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS school_crm_integrations (
            id INT AUTO_INCREMENT PRIMARY KEY,
            school_id INT NOT NULL,
            crm_type VARCHAR(50) NOT NULL,
            crm_name VARCHAR(255) NOT NULL,
            api_url VARCHAR(1024) NULL,
            api_key TEXT NULL,
            api_secret TEXT NULL,
            access_token TEXT NULL,
            webhook_url VARCHAR(1024) NULL,
            webhook_secret TEXT NULL,
            is_active BOOLEAN DEFAULT FALSE,
            sync_direction VARCHAR(32) DEFAULT 'app_to_crm',
            sync_frequency VARCHAR(32) DEFAULT 'daily',
            last_sync_at TIMESTAMP NULL,
            next_sync_at TIMESTAMP NULL,
            field_mapping JSON NULL,
            metadata JSON NULL,
            notes TEXT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_school_crm_integrations_school (school_id),
            INDEX idx_school_crm_integrations_type (crm_type)
        )
        """
    )


async def _ensure_crm_sync_logs_table(cursor) -> None:
    await cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS crm_sync_logs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            integration_id INT NOT NULL,
            school_id INT NOT NULL,
            sync_type VARCHAR(64) NOT NULL,
            status VARCHAR(32) NOT NULL,
            message TEXT NULL,
            request_url VARCHAR(1024) NULL,
            response_status INT NULL,
            items_sent INT DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_crm_sync_logs_integration (integration_id),
            INDEX idx_crm_sync_logs_school (school_id)
        )
        """
    )


async def _record_sync_log(
    cursor,
    *,
    integration_id: int,
    school_id: int,
    sync_type: str,
    status: str,
    message: str,
    request_url: Optional[str],
    response_status: Optional[int],
    items_sent: int = 0,
) -> None:
    await _ensure_crm_sync_logs_table(cursor)
    await cursor.execute(
        """
        INSERT INTO crm_sync_logs (
            integration_id, school_id, sync_type, status, message,
            request_url, response_status, items_sent
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            integration_id,
            school_id,
            sync_type,
            status,
            message[:2000],
            request_url,
            response_status,
            items_sent,
        ),
    )


def _validate_api_url(api_url: Optional[str]) -> str:
    return _validate_external_url(api_url, "API URL")


def _validate_webhook_url(webhook_url: Optional[str]) -> str:
    return _validate_external_url(webhook_url, "Webhook URL")


def _validate_external_url(raw_url: Optional[str], label: str) -> str:
    if not raw_url or not raw_url.strip():
        raise HTTPException(status_code=400, detail=f"{label} is required")
    parsed = urlparse(raw_url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(status_code=400, detail=f"{label} must be http(s)")
    if not parsed.hostname:
        raise HTTPException(status_code=400, detail=f"{label} host is required")
    _validate_public_host(parsed.hostname, label)
    return raw_url.strip()


def _validate_public_host(hostname: str, label: str = "API URL") -> None:
    if hostname.lower() in {"localhost", "localhost.localdomain"}:
        raise HTTPException(status_code=400, detail=f"{label} host is not allowed")

    try:
        addresses = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HTTPException(status_code=400, detail=f"{label} host cannot be resolved") from exc

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise HTTPException(status_code=400, detail=f"{label} host is not allowed")


def _build_crm_headers(row: dict) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "GradeBook-CRM-Connector/1.0",
    }
    api_key = row.get("api_key")
    access_token = row.get("access_token")
    api_secret = row.get("api_secret")
    if api_key:
        headers["X-API-Key"] = str(api_key)
    if api_secret:
        headers["X-API-Secret"] = str(api_secret)
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


def _crm_http_request(
    *,
    method: str,
    api_url: str,
    headers: dict,
    payload: Optional[dict] = None,
    timeout_seconds: int = 8,
) -> dict:
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urlrequest.Request(
        api_url,
        data=body,
        headers=headers,
        method=method.upper(),
    )
    try:
        with urlrequest.urlopen(req, timeout=timeout_seconds) as response:  # nosec B310 # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            raw = response.read(65536).decode("utf-8", errors="replace")
            parsed = None
            if raw:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = raw[:1000]
            return {
                "ok": 200 <= response.status < 300,
                "status_code": response.status,
                "body": parsed,
            }
    except urlerror.HTTPError as exc:
        raw = exc.read(4096).decode("utf-8", errors="replace")
        return {"ok": False, "status_code": exc.code, "body": raw[:1000]}
    except Exception as exc:
        return {"ok": False, "status_code": None, "body": str(exc)}


async def _call_crm(
    *,
    method: str,
    api_url: str,
    headers: dict,
    payload: Optional[dict] = None,
) -> dict:
    return await asyncio.to_thread(
        _crm_http_request,
        method=method,
        api_url=api_url,
        headers=headers,
        payload=payload,
    )


def _bitrix_method_url(api_url: Optional[str], method: str) -> str:
    base_url = _validate_api_url(api_url).rstrip("/")
    if base_url.endswith(f"/{method}.json"):
        return base_url
    if base_url.endswith(f"/{method}"):
        return f"{base_url}.json"
    return f"{base_url}/{method}.json"


def _flatten_bitrix_params(params: Optional[dict], prefix: Optional[str] = None) -> list[tuple[str, str]]:
    if not params:
        return []

    values = []
    for key, value in params.items():
        param_key = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            values.extend(_flatten_bitrix_params(value, param_key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                item_key = f"{param_key}[{index}]"
                if isinstance(item, dict):
                    values.extend(_flatten_bitrix_params(item, item_key))
                elif item is not None:
                    values.append((item_key, str(item)))
        elif value is not None:
            values.append((param_key, str(value)))
    return values


def _bitrix_http_request(
    *,
    api_url: str,
    params: Optional[dict] = None,
    timeout_seconds: int = 12,
) -> dict:
    body = urlencode(_flatten_bitrix_params(params), doseq=True).encode("utf-8")
    req = urlrequest.Request(
        api_url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "GradeBook-Bitrix24-Connector/1.0",
        },
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=timeout_seconds) as response:  # nosec B310 # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            raw = response.read(131072).decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = raw[:1000]
            return {
                "ok": 200 <= response.status < 300,
                "status_code": response.status,
                "body": parsed,
            }
    except urlerror.HTTPError as exc:
        raw = exc.read(4096).decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = raw[:1000]
        return {"ok": False, "status_code": exc.code, "body": parsed}
    except Exception as exc:
        return {"ok": False, "status_code": None, "body": str(exc)}


async def _call_bitrix_method(row: dict, method: str, params: Optional[dict] = None) -> dict:
    method_url = _bitrix_method_url(row.get("api_url"), method)
    result = await asyncio.to_thread(
        _bitrix_http_request,
        api_url=method_url,
        params=params,
    )
    body = result.get("body")
    if isinstance(body, dict) and body.get("error"):
        return {
            "ok": False,
            "status_code": result.get("status_code"),
            "body": body,
            "method_url": method_url,
            "error": body.get("error_description") or body.get("error"),
        }
    return {
        "ok": bool(result.get("ok")),
        "status_code": result.get("status_code"),
        "body": body,
        "method_url": method_url,
        "result": body.get("result") if isinstance(body, dict) else body,
    }


def _parse_json_object(raw_value: Optional[str]) -> dict:
    if not raw_value:
        return {}
    if isinstance(raw_value, dict):
        return raw_value
    try:
        parsed = json.loads(raw_value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _split_full_name(full_name: Optional[str]) -> tuple[str, str, str]:
    parts = [part for part in (full_name or "").strip().split() if part]
    if not parts:
        return "", "Без имени", ""
    if len(parts) == 1:
        return "", parts[0], ""
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return parts[0], parts[1], " ".join(parts[2:])


def _aggregate_bitrix_users(users: list[dict]) -> list[dict]:
    aggregated = {}
    for row in users:
        user_id = row.get("id")
        if user_id is None:
            continue
        item = aggregated.setdefault(
            user_id,
            {
                "id": user_id,
                "full_name": row.get("full_name"),
                "role": row.get("role"),
                "classes": [],
            },
        )
        class_id = row.get("class_id")
        if class_id is not None and not any(cls["id"] == class_id for cls in item["classes"]):
            item["classes"].append(
                {
                    "id": class_id,
                    "name": row.get("class_name"),
                }
            )
    return list(aggregated.values())


def _bitrix_company_fields(class_item: dict) -> dict:
    class_name = class_item.get("name") or f"Класс {class_item.get('id')}"
    comments = [f"GradeBook class_id={class_item.get('id')}"]
    if class_item.get("academic_year"):
        comments.append(f"Учебный год: {class_item.get('academic_year')}")
    return {
        "TITLE": class_name,
        "COMMENTS": "\n".join(comments),
    }


def _bitrix_contact_fields(user: dict, company_id: Optional[int]) -> dict:
    last_name, name, second_name = _split_full_name(user.get("full_name"))
    class_names = [
        str(class_item.get("name"))
        for class_item in user.get("classes", [])
        if class_item.get("name")
    ]
    fields = {
        "NAME": name,
        "LAST_NAME": last_name,
        "SECOND_NAME": second_name,
        "POST": "Ученик" if user.get("role") == "student" else "Учитель",
        "COMMENTS": "\n".join(
            [
                f"GradeBook user_id={user.get('id')}",
                f"Роль: {user.get('role') or '-'}",
                f"Классы: {', '.join(class_names) if class_names else '-'}",
            ]
        ),
    }
    if company_id:
        fields["COMPANY_ID"] = company_id
    return fields


async def _sync_bitrix(cursor, integration_row: dict, integration_id: int, sync_type: str) -> dict:
    school_id = integration_row["school_id"]
    payload, _ = await _collect_school_sync_payload(
        cursor,
        school_id,
        "full" if sync_type in ("full", "enrollments") else sync_type,
    )
    metadata = _parse_json_object(integration_row.get("metadata"))
    bitrix_meta = metadata.setdefault("bitrix", {})
    company_ids = bitrix_meta.setdefault("companies", {})
    contact_ids = bitrix_meta.setdefault("contacts", {})

    classes = payload.get("data", {}).get("classes", [])
    users = _aggregate_bitrix_users(payload.get("data", {}).get("users", []))
    sent = 0
    errors = []
    last_http_status = None

    if sync_type in ("classes", "full", "enrollments"):
        for class_item in classes:
            local_key = f"class_{class_item.get('id')}"
            external_id = company_ids.get(local_key)
            fields = _bitrix_company_fields(class_item)
            method = "crm.company.update" if external_id else "crm.company.add"
            params = {"id": external_id, "fields": fields} if external_id else {"fields": fields}
            result = await _call_bitrix_method(integration_row, method, params)
            last_http_status = result.get("status_code")
            if result["ok"]:
                if not external_id:
                    company_ids[local_key] = result.get("result")
                sent += 1
            else:
                errors.append(
                    {
                        "entity": "class",
                        "id": class_item.get("id"),
                        "message": result.get("error") or str(result.get("body")),
                    }
                )

    if sync_type in ("enrollments", "full"):
        for user in users:
            local_key = f"user_{user.get('id')}"
            external_id = contact_ids.get(local_key)
            primary_class_id = None
            if user.get("classes"):
                primary_class_id = user["classes"][0].get("id")
            company_id = company_ids.get(f"class_{primary_class_id}") if primary_class_id else None
            fields = _bitrix_contact_fields(user, company_id)
            method = "crm.contact.update" if external_id else "crm.contact.add"
            params = {"id": external_id, "fields": fields} if external_id else {"fields": fields}
            result = await _call_bitrix_method(integration_row, method, params)
            last_http_status = result.get("status_code")
            if result["ok"]:
                if not external_id:
                    contact_ids[local_key] = result.get("result")
                sent += 1
            else:
                errors.append(
                    {
                        "entity": "user",
                        "id": user.get("id"),
                        "message": result.get("error") or str(result.get("body")),
                    }
                )

    if sync_type in ("lessons", "financial"):
        message = (
            "Расписание пока не маппится в Bitrix24 CRM"
            if sync_type == "lessons"
            else "Финансовые данные пока не маппятся в Bitrix24 CRM"
        )
        await _record_sync_log(
            cursor,
            integration_id=integration_id,
            school_id=school_id,
            sync_type=sync_type,
            status="completed",
            message=message,
            request_url=_validate_api_url(integration_row.get("api_url")),
            response_status=None,
            items_sent=0,
        )
        return {
            "status": "completed",
            "sync_type": sync_type,
            "integration_id": integration_id,
            "items_sent": 0,
            "message": message,
            "errors": [],
        }

    if errors and sent == 0:
        status = "error"
    elif errors:
        status = "partial"
    else:
        status = "completed"

    message = (
        f"Bitrix24 sync completed: {sent} records"
        if status == "completed"
        else f"Bitrix24 sync completed partially: {sent} records, errors: {len(errors)}"
        if status == "partial"
        else f"Bitrix24 sync failed: {len(errors)} errors"
    )
    await _record_sync_log(
        cursor,
        integration_id=integration_id,
        school_id=school_id,
        sync_type=sync_type,
        status=status,
        message=message,
        request_url=_validate_api_url(integration_row.get("api_url")),
        response_status=last_http_status,
        items_sent=sent,
    )
    if status in ("completed", "partial"):
        await cursor.execute(
            """
            UPDATE school_crm_integrations
            SET last_sync_at = NOW(), metadata = %s
            WHERE id = %s
            """,
            (json.dumps(metadata, ensure_ascii=False), integration_id),
        )

    return {
        "status": status,
        "sync_type": sync_type,
        "integration_id": integration_id,
        "items_sent": sent,
        "http_status": last_http_status,
        "message": message,
        "errors": errors[:20],
        "errors_total": len(errors),
    }


async def _collect_school_sync_payload(cursor, school_id: int, sync_type: str) -> tuple[dict, int]:
    payload = {
        "source": "grade_book",
        "sync_type": sync_type,
        "school_id": school_id,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "data": {},
    }
    items_sent = 0

    if sync_type in ("classes", "full"):
        await cursor.execute(
            """
            SELECT id, name, academic_year
            FROM classes
            WHERE school_id = %s AND COALESCE(is_archived, 0) = 0
            ORDER BY name
            """,
            (school_id,),
        )
        classes = await cursor.fetchall()
        payload["data"]["classes"] = [
            {
                "id": row["id"],
                "name": row.get("name"),
                "academic_year": row.get("academic_year"),
            }
            for row in classes
        ]
        items_sent += len(classes)

    if sync_type in ("enrollments", "full"):
        await cursor.execute(
            """
            SELECT DISTINCT u.id, p.full_name, u.role, c.id AS class_id, c.name AS class_name
            FROM users u
            JOIN profiles p ON p.user_id = u.id
            JOIN class_students cs ON cs.student_id = u.id
            JOIN classes c ON c.id = cs.class_id
            WHERE c.school_id = %s
            UNION
            SELECT DISTINCT u.id, p.full_name, u.role, c.id AS class_id, c.name AS class_name
            FROM users u
            JOIN profiles p ON p.user_id = u.id
            JOIN class_teachers ct ON ct.teacher_id = u.id
            JOIN classes c ON c.id = ct.class_id
            WHERE c.school_id = %s
            UNION
            SELECT DISTINCT u.id, p.full_name, u.role, c.id AS class_id, c.name AS class_name
            FROM users u
            JOIN profiles p ON p.user_id = u.id
            JOIN class_subject_teachers cst ON cst.teacher_id = u.id
            JOIN classes c ON c.id = cst.class_id
            WHERE c.school_id = %s
            ORDER BY role, full_name
            """,
            (school_id, school_id, school_id),
        )
        users = await cursor.fetchall()
        payload["data"]["users"] = [
            {
                "id": row["id"],
                "full_name": row.get("full_name"),
                "role": row.get("role"),
                "class_id": row.get("class_id"),
                "class_name": row.get("class_name"),
            }
            for row in users
        ]
        items_sent += len(users)

    if sync_type in ("financial", "full"):
        payload["data"]["financial"] = {
            "message": "Financial sync endpoint is ready; payment export mapping is not configured yet."
        }

    if sync_type == "lessons":
        payload["data"]["lessons"] = {
            "message": "Lesson sync endpoint is ready; use the dedicated 1C lessons integration for timetable import."
        }

    return payload, items_sent


@router.get("/schools/{school_id}/integrations", response_model=List[CRMIntegration])
async def get_school_integrations(
    school_id: int,
    current_user: UserInDB = Depends(get_current_user)
):
    """Получить список интеграций CRM для школы"""
    await _ensure_school_access(current_user, school_id)
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await _ensure_crm_integrations_table(cursor)
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
            await _ensure_crm_integrations_table(cursor)
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
            
            await _ensure_school_access(current_user, row["school_id"])
            
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
    if integration.school_id != school_id:
        raise HTTPException(status_code=400, detail="School ID mismatch")
    await _ensure_school_access(current_user, school_id)

    api_url = _validate_api_url(integration.api_url) if integration.api_url else None
    webhook_url = (
        _validate_webhook_url(integration.webhook_url)
        if integration.webhook_url
        else None
    )
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await _ensure_crm_integrations_table(cursor)
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
                api_url,
                integration.api_key,
                integration.api_secret,
                integration.access_token,
                webhook_url,
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
            await _ensure_crm_integrations_table(cursor)
            # Проверяем существование и получаем school_id
            await cursor.execute("SELECT school_id FROM school_crm_integrations WHERE id = %s", (integration_id,))
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Integration not found")
            
            school_id = row["school_id"]
            
            await _ensure_school_access(current_user, school_id)
            
            # Формируем запрос на обновление
            update_fields = []
            params = []
            
            if integration_update.crm_name is not None:
                update_fields.append("crm_name = %s")
                params.append(integration_update.crm_name)
            
            if integration_update.api_url is not None:
                update_fields.append("api_url = %s")
                params.append(_validate_api_url(integration_update.api_url))
            
            if integration_update.api_key is not None:
                update_fields.append("api_key = %s")
                params.append(integration_update.api_key)
            
            if integration_update.api_secret is not None:
                update_fields.append("api_secret = %s")
                params.append(integration_update.api_secret)
            
            if integration_update.access_token is not None:
                update_fields.append("access_token = %s")
                params.append(integration_update.access_token)

            if integration_update.webhook_url is not None:
                update_fields.append("webhook_url = %s")
                params.append(_validate_webhook_url(integration_update.webhook_url))

            if integration_update.webhook_secret is not None:
                update_fields.append("webhook_secret = %s")
                params.append(integration_update.webhook_secret)
            
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
            await _ensure_crm_integrations_table(cursor)
            # Проверяем существование и получаем school_id
            await cursor.execute("SELECT school_id FROM school_crm_integrations WHERE id = %s", (integration_id,))
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Integration not found")
            
            school_id = row["school_id"]
            
            await _ensure_school_access(current_user, school_id)
            
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
            await _ensure_crm_integrations_table(cursor)
            await cursor.execute("""
                SELECT school_id, crm_type, api_url, api_key, api_secret, access_token
                FROM school_crm_integrations
                WHERE id = %s
            """, (integration_id,))
            
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Integration not found")
            
            await _ensure_school_access(current_user, row["school_id"])

            api_url = _validate_api_url(row.get("api_url"))
            if row.get("crm_type") == CRMType.bitrix24.value:
                result = await _call_bitrix_method(row, "user.current")
                request_url = result.get("method_url")
            else:
                result = await _call_crm(
                    method="GET",
                    api_url=api_url,
                    headers=_build_crm_headers(row),
                )
                request_url = api_url
            status = "success" if result["ok"] else "error"
            message = (
                "Connection test completed"
                if result["ok"]
                else f"Connection test failed: {result.get('body')}"
            )
            await _record_sync_log(
                cursor,
                integration_id=integration_id,
                school_id=row["school_id"],
                sync_type="test",
                status=status,
                message=message,
                request_url=request_url,
                response_status=result.get("status_code"),
            )
            await conn.commit()
            return {
                "status": status,
                "message": message,
                "details": {
                    "crm_type": row["crm_type"],
                    "api_url": api_url,
                    "http_status": result.get("status_code"),
                    "response": result.get("body"),
                },
            }
    finally:
        conn.close()


@router.post("/integrations/{integration_id}/sync")
async def trigger_sync(
    integration_id: int,
    sync_type: str = Query("lessons", description="Тип синхронизации: lessons, classes, enrollments, financial, full"),
    current_user: UserInDB = Depends(get_current_user)
):
    """Запустить синхронизацию данных с CRM"""
    allowed_sync_types = {"lessons", "classes", "enrollments", "financial", "full"}
    if sync_type not in allowed_sync_types:
        raise HTTPException(status_code=400, detail="Unsupported sync type")

    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await _ensure_crm_integrations_table(cursor)
            await cursor.execute(
                """
                SELECT school_id, crm_type, api_url, api_key, api_secret, access_token,
                       is_active, sync_direction, metadata
                FROM school_crm_integrations
                WHERE id = %s
                """,
                (integration_id,),
            )
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Integration not found")
            
            school_id = row["school_id"]
            
            await _ensure_school_access(current_user, school_id)

            if not row.get("is_active"):
                raise HTTPException(status_code=400, detail="Integration is not active")

            if row.get("crm_type") == CRMType.bitrix24.value:
                result = await _sync_bitrix(cursor, row, integration_id, sync_type)
                await conn.commit()
                return result

            api_url = _validate_api_url(row.get("api_url"))
            payload, items_sent = await _collect_school_sync_payload(
                cursor,
                school_id,
                sync_type,
            )
            result = await _call_crm(
                method="POST",
                api_url=api_url,
                headers=_build_crm_headers(row),
                payload=payload,
            )
            status = "completed" if result["ok"] else "error"
            message = (
                "Synchronization completed"
                if result["ok"]
                else f"Synchronization failed: {result.get('body')}"
            )
            await _record_sync_log(
                cursor,
                integration_id=integration_id,
                school_id=school_id,
                sync_type=sync_type,
                status=status,
                message=message,
                request_url=api_url,
                response_status=result.get("status_code"),
                items_sent=items_sent,
            )
            if result["ok"]:
                await cursor.execute(
                    """
                    UPDATE school_crm_integrations
                    SET last_sync_at = NOW()
                    WHERE id = %s
                    """,
                    (integration_id,),
                )
            await conn.commit()
            return {
                "status": status,
                "sync_type": sync_type,
                "integration_id": integration_id,
                "items_sent": items_sent,
                "http_status": result.get("status_code"),
                "message": message,
                "response": result.get("body"),
            }
    finally:
        conn.close()
