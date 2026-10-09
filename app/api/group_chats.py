"""
API endpoints for group chats
"""
import os
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from typing import List, Optional
from datetime import datetime
from app.db.connection import get_db_connection
from app.core.security import decrypt_message, get_current_user
from app.core.config import settings
from app.core.entitlements import get_school_id_for_user, require_school_feature
from app.core.file_uploads import IMAGE_EXTS, MAX_IMAGE_BYTES, save_upload_limited, validate_upload_metadata
from app.models.user import UserInDB
from app.models.group_chat import (
    GroupChatCreate, GroupChatUpdate, GroupChat, GroupChatDetail,
    GroupChatMember, AddMembersRequest, RemoveMemberRequest, UpdateMemberRoleRequest
)

router = APIRouter(prefix="/group-chats")
APPLICATION_OWNER_ROLES = {"owner", "superadmin", "root"}


async def _ensure_group_members_allowed(
    cursor,
    current_user: UserInDB,
    user_ids: List[int],
) -> List[int]:
    unique_ids = list(dict.fromkeys(int(user_id) for user_id in user_ids))
    if not unique_ids:
        return []
    if current_user.role in APPLICATION_OWNER_ROLES:
        await cursor.execute(
            "SELECT id FROM users WHERE id IN ({})".format(
                ",".join(["%s"] * len(unique_ids))
            ),
            tuple(unique_ids),
        )
    else:
        school_id = await get_school_id_for_user(current_user)
        if school_id is None:
            raise HTTPException(status_code=403, detail="No school access")
        await cursor.execute(
            """
            SELECT DISTINCT u.id
            FROM users u
            LEFT JOIN school_users su ON su.user_id = u.id
            LEFT JOIN school_admins sa ON sa.admin_user_id = u.id
            LEFT JOIN class_students cs ON cs.student_id = u.id
            LEFT JOIN classes student_class ON student_class.id = cs.class_id
            LEFT JOIN class_teachers ct ON ct.teacher_id = u.id
            LEFT JOIN classes teacher_class ON teacher_class.id = ct.class_id
            LEFT JOIN class_subject_teachers cst ON cst.teacher_id = u.id
            LEFT JOIN classes subject_teacher_class ON subject_teacher_class.id = cst.class_id
            LEFT JOIN parent_students ps ON ps.parent_id = u.id
            LEFT JOIN class_students child_cs ON child_cs.student_id = ps.student_id
            LEFT JOIN classes parent_class ON parent_class.id = child_cs.class_id
            WHERE u.id IN ({})
              AND (
                  u.role IN ('owner', 'superadmin', 'root')
                  OR su.school_id = %s
                  OR sa.school_id = %s
                  OR student_class.school_id = %s
                  OR teacher_class.school_id = %s
                  OR subject_teacher_class.school_id = %s
                  OR parent_class.school_id = %s
              )
            """.format(",".join(["%s"] * len(unique_ids))),
            tuple(unique_ids) + (school_id,) * 6,
        )
    allowed_ids = {
        int(row["id"]) for row in await cursor.fetchall()
    }
    missing_ids = set(unique_ids) - allowed_ids
    if missing_ids:
        raise HTTPException(
            status_code=403,
            detail="Нельзя добавить пользователя другой школы",
        )
    return unique_ids


@router.post("", response_model=GroupChatDetail)
async def create_group_chat(
    data: GroupChatCreate,
    current_user: UserInDB = Depends(get_current_user)
):
    """Создать новый групповой чат"""
    await require_school_feature(
        current_user,
        "messages_enabled",
        detail="Сообщения отключены в настройках школы",
    )
    if not data.name or not data.name.strip():
        raise HTTPException(status_code=400, detail="Group name is required")
    
    if not data.member_ids:
        raise HTTPException(status_code=400, detail="At least one member is required")
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            member_ids_with_creator = await _ensure_group_members_allowed(
                cursor,
                current_user,
                [current_user.id, *data.member_ids],
            )
            # Создаем группу
            await cursor.execute("""
                INSERT INTO group_chats (name, description, created_by)
                VALUES (%s, %s, %s)
            """, (data.name.strip(), data.description, current_user.id))
            group_id = cursor.lastrowid
            
            # Добавляем участников
            for user_id in member_ids_with_creator:
                role = 'admin' if user_id == current_user.id else 'member'
                await cursor.execute("""
                    INSERT INTO group_chat_members (group_chat_id, user_id, role)
                    VALUES (%s, %s, %s)
                """, (group_id, user_id, role))
            
            await conn.commit()
            
            # Получаем информацию о группе
            return await _get_group_chat_detail(cursor, conn, group_id, current_user.id)
    except Exception:
        await conn.rollback()
        raise
    finally:
        conn.close()


@router.get("", response_model=List[GroupChat])
async def get_my_group_chats(
    current_user: UserInDB = Depends(get_current_user)
):
    """Получить список групповых чатов пользователя"""
    await require_school_feature(
        current_user,
        "messages_enabled",
        detail="Сообщения отключены в настройках школы",
    )
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT 
                    gc.id, gc.name, gc.description, gc.created_by,
                    gc.photo_url, gc.created_at, gc.updated_at,
                    member_counts.member_count,
                    last_message.id AS last_message_id,
                    last_message.sender_id AS last_sender_id,
                    last_message.content AS last_content,
                    last_message.attachment_name AS last_attachment_name,
                    last_message.created_at AS last_message_created_at,
                    COALESCE(
                        NULLIF(TRIM(BOTH '"' FROM sender_profile.full_name), ''),
                        sender.username,
                        'Unknown'
                    ) AS last_sender_name
                FROM group_chats gc
                INNER JOIN group_chat_members own_membership
                    ON own_membership.group_chat_id = gc.id
                   AND own_membership.user_id = %s
                LEFT JOIN (
                    SELECT group_chat_id, COUNT(*) AS member_count
                    FROM group_chat_members
                    GROUP BY group_chat_id
                ) member_counts ON member_counts.group_chat_id = gc.id
                LEFT JOIN (
                    SELECT group_chat_id, MAX(id) AS message_id
                    FROM messages
                    WHERE group_chat_id IS NOT NULL
                    GROUP BY group_chat_id
                ) latest ON latest.group_chat_id = gc.id
                LEFT JOIN messages last_message ON last_message.id = latest.message_id
                LEFT JOIN users sender ON sender.id = last_message.sender_id
                LEFT JOIN profiles sender_profile
                    ON sender_profile.user_id = last_message.sender_id
                ORDER BY gc.updated_at DESC
            """, (current_user.id,))
            
            rows = await cursor.fetchall()
            
            groups = []
            for row in rows:
                last_message = None
                if row.get("last_message_id"):
                    try:
                        last_content = decrypt_message(row.get("last_content") or "")
                    except Exception:
                        last_content = ""
                    last_message = {
                        "id": row["last_message_id"],
                        "sender_id": row["last_sender_id"],
                        "sender_name": row["last_sender_name"],
                        "content": (
                            f"Файл: {row['last_attachment_name']}"
                            if row.get("last_attachment_name") and not last_content
                            else last_content
                        ),
                        "created_at": (
                            row["last_message_created_at"].isoformat()
                            if row.get("last_message_created_at")
                            else None
                        ),
                    }
                
                groups.append({
                    "id": row["id"],
                    "name": row["name"],
                    "description": row.get("description"),
                    "created_by": row["created_by"],
                    "photo_url": row.get("photo_url"),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "member_count": row["member_count"],
                    "last_message": last_message,
                })
            
            return groups
    finally:
        conn.close()


@router.get("/{group_id}", response_model=GroupChatDetail)
async def get_group_chat(
    group_id: int,
    current_user: UserInDB = Depends(get_current_user)
):
    """Получить информацию о групповом чате"""
    await require_school_feature(
        current_user,
        "messages_enabled",
        detail="Сообщения отключены в настройках школы",
    )
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем, что пользователь является участником
            await cursor.execute("""
                SELECT user_id FROM group_chat_members
                WHERE group_chat_id = %s AND user_id = %s
            """, (group_id, current_user.id))
            member = await cursor.fetchone()
            if not member:
                raise HTTPException(status_code=403, detail="You are not a member of this group")
            
            return await _get_group_chat_detail(cursor, conn, group_id, current_user.id)
    finally:
        conn.close()


async def _get_group_chat_detail(cursor, conn, group_id: int, current_user_id: int) -> dict:
    """Вспомогательная функция для получения детальной информации о группе"""
    # Получаем информацию о группе
    await cursor.execute("""
        SELECT id, name, description, created_by, photo_url, created_at, updated_at
        FROM group_chats
        WHERE id = %s
    """, (group_id,))
    group = await cursor.fetchone()
    
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    # Получаем участников
    await cursor.execute("""
        SELECT gcm.user_id, gcm.role, gcm.joined_at,
               p.full_name, p.photo_url
        FROM group_chat_members gcm
        LEFT JOIN profiles p ON gcm.user_id = p.user_id
        WHERE gcm.group_chat_id = %s
        ORDER BY gcm.joined_at
    """, (group_id,))
    member_rows = await cursor.fetchall()
    
    members = [
        {
            "id": idx + 1,  # Временный ID для совместимости с моделью
            "user_id": row["user_id"],
            "full_name": row["full_name"] or "Unknown",
            "photo_url": row.get("photo_url"),
            "role": row["role"],
            "joined_at": row["joined_at"],
        }
        for idx, row in enumerate(member_rows)
    ]
    
    return {
        "id": group["id"],
        "name": group["name"],
        "description": group.get("description"),
        "created_by": group["created_by"],
        "photo_url": group.get("photo_url"),
        "created_at": group["created_at"],
        "updated_at": group["updated_at"],
        "member_count": len(members),
        "members": members,
    }


@router.put("/{group_id}", response_model=GroupChatDetail)
async def update_group_chat(
    group_id: int,
    data: GroupChatUpdate,
    current_user: UserInDB = Depends(get_current_user)
):
    """Обновить информацию о групповом чате (только админы)"""
    await require_school_feature(
        current_user,
        "messages_enabled",
        detail="Сообщения отключены в настройках школы",
    )
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем права доступа
            await cursor.execute("""
                SELECT role FROM group_chat_members
                WHERE group_chat_id = %s AND user_id = %s
            """, (group_id, current_user.id))
            member = await cursor.fetchone()
            
            if not member:
                raise HTTPException(status_code=403, detail="You are not a member of this group")
            if member["role"] != "admin":
                raise HTTPException(status_code=403, detail="Only admins can update group")
            
            # Обновляем группу
            updates = []
            params = []
            
            if data.name is not None:
                if not data.name.strip():
                    raise HTTPException(
                        status_code=400,
                        detail="Group name is required",
                    )
                updates.append("name = %s")
                params.append(data.name.strip())
            if data.description is not None:
                updates.append("description = %s")
                params.append(data.description)
            if data.photo_url is not None:
                updates.append("photo_url = %s")
                params.append(data.photo_url)
            
            if updates:
                params.append(group_id)
                await cursor.execute(f"""
                    UPDATE group_chats
                    SET {', '.join(updates)}
                    WHERE id = %s
                """, params)
                await conn.commit()
            
            return await _get_group_chat_detail(cursor, conn, group_id, current_user.id)
    finally:
        conn.close()


@router.post("/{group_id}/members", response_model=GroupChatDetail)
async def add_members(
    group_id: int,
    data: AddMembersRequest,
    current_user: UserInDB = Depends(get_current_user)
):
    """Добавить участников в группу (только админы)"""
    await require_school_feature(
        current_user,
        "messages_enabled",
        detail="Сообщения отключены в настройках школы",
    )
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем права доступа
            await cursor.execute("""
                SELECT role FROM group_chat_members
                WHERE group_chat_id = %s AND user_id = %s
            """, (group_id, current_user.id))
            member = await cursor.fetchone()
            
            if not member or member["role"] != "admin":
                raise HTTPException(status_code=403, detail="Only admins can add members")

            allowed_user_ids = await _ensure_group_members_allowed(
                cursor,
                current_user,
                data.user_ids,
            )
            
            # Добавляем участников
            added_count = 0
            for user_id in allowed_user_ids:
                # Проверяем, не является ли уже участником
                await cursor.execute("""
                    SELECT id FROM group_chat_members
                    WHERE group_chat_id = %s AND user_id = %s
                """, (group_id, user_id))
                existing = await cursor.fetchone()
                
                if not existing:
                    await cursor.execute("""
                        INSERT INTO group_chat_members (group_chat_id, user_id, role)
                        VALUES (%s, %s, 'member')
                    """, (group_id, user_id))
                    added_count += 1
            
            await conn.commit()
            
            return await _get_group_chat_detail(cursor, conn, group_id, current_user.id)
    except Exception:
        await conn.rollback()
        raise
    finally:
        conn.close()


@router.delete("/{group_id}/members/{user_id}", response_model=GroupChatDetail)
async def remove_member(
    group_id: int,
    user_id: int,
    current_user: UserInDB = Depends(get_current_user)
):
    """Удалить участника из группы"""
    await require_school_feature(
        current_user,
        "messages_enabled",
        detail="Сообщения отключены в настройках школы",
    )
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем права доступа
            await cursor.execute("""
                SELECT role FROM group_chat_members
                WHERE group_chat_id = %s AND user_id = %s
            """, (group_id, current_user.id))
            current_member = await cursor.fetchone()
            
            if not current_member:
                raise HTTPException(status_code=403, detail="You are not a member of this group")
            
            # Удалять может только админ или сам пользователь
            if current_member["role"] != "admin" and current_user.id != user_id:
                raise HTTPException(status_code=403, detail="Only admins can remove other members")
            
            # Нельзя удалить создателя группы
            await cursor.execute("""
                SELECT created_by FROM group_chats WHERE id = %s
            """, (group_id,))
            group = await cursor.fetchone()
            if group and group["created_by"] == user_id:
                raise HTTPException(status_code=400, detail="Cannot remove group creator")
            
            await cursor.execute("""
                DELETE FROM group_chat_members
                WHERE group_chat_id = %s AND user_id = %s
            """, (group_id, user_id))
            await conn.commit()
            
            return await _get_group_chat_detail(cursor, conn, group_id, current_user.id)
    finally:
        conn.close()


@router.delete("/{group_id}")
async def delete_group_chat(
    group_id: int,
    current_user: UserInDB = Depends(get_current_user)
):
    """Удалить групповой чат (только создатель)"""
    await require_school_feature(
        current_user,
        "messages_enabled",
        detail="Сообщения отключены в настройках школы",
    )
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем, что пользователь является создателем
            await cursor.execute("""
                SELECT created_by FROM group_chats WHERE id = %s
            """, (group_id,))
            group = await cursor.fetchone()
            
            if not group:
                raise HTTPException(status_code=404, detail="Group not found")
            if group["created_by"] != current_user.id:
                raise HTTPException(status_code=403, detail="Only group creator can delete the group")
            
            # Удаляем группу (каскадное удаление удалит все связанные записи)
            await cursor.execute("DELETE FROM group_chats WHERE id = %s", (group_id,))
            await conn.commit()
            
            return {"status": "success", "message": "Group deleted"}
    finally:
        conn.close()


@router.post("/{group_id}/photo")
async def upload_group_photo(
    group_id: int,
    file: UploadFile = File(...),
    current_user: UserInDB = Depends(get_current_user)
):
    """Загрузить фото группы (только админы)"""
    await require_school_feature(
        current_user,
        "messages_enabled",
        detail="Сообщения отключены в настройках школы",
    )
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем права доступа
            await cursor.execute("""
                SELECT role FROM group_chat_members
                WHERE group_chat_id = %s AND user_id = %s
            """, (group_id, current_user.id))
            member = await cursor.fetchone()
            
            if not member or member["role"] != "admin":
                raise HTTPException(status_code=403, detail="Only admins can upload group photo")
            
            _, ext = validate_upload_metadata(
                file,
                allowed_exts=IMAGE_EXTS - {"gif", "webp"},
                allowed_mime_prefixes=("image/",),
            )
            
            # Создаем директорию для фото групп, если её нет
            group_photos_dir = os.path.join(settings.PROFILE_PHOTOS_DIR, "groups")
            os.makedirs(group_photos_dir, exist_ok=True)
            
            # Удаляем старые фото группы
            for e in ["jpg", "jpeg", "png"]:
                old_path = os.path.join(group_photos_dir, f"{group_id}.{e}")
                if os.path.exists(old_path):
                    os.remove(old_path)
            
            # Сохраняем новое фото
            path = os.path.join(group_photos_dir, f"{group_id}.{ext}")
            await save_upload_limited(file, path, max_bytes=MAX_IMAGE_BYTES)
            
            # Обновляем photo_url в базе данных
            photo_url = f"{settings.API_V1_STR}/group-chats/{group_id}/photo"
            await cursor.execute("""
                UPDATE group_chats
                SET photo_url = %s
                WHERE id = %s
            """, (photo_url, group_id))
            await conn.commit()
            
            return {"photo_url": photo_url}
    finally:
        conn.close()


@router.get("/{group_id}/photo")
async def get_group_photo(group_id: int):
    """Получить фото группы"""
    from starlette.responses import FileResponse
    
    group_photos_dir = os.path.join(settings.PROFILE_PHOTOS_DIR, "groups")
    for ext in ["jpg", "jpeg", "png"]:
        file_path = os.path.join(group_photos_dir, f"{group_id}.{ext}")
        if os.path.exists(file_path):
            media_type = f"image/{'jpeg' if ext in ['jpg', 'jpeg'] else 'png'}"
            return FileResponse(file_path, media_type=media_type)
    raise HTTPException(status_code=404, detail="Group photo not found")
