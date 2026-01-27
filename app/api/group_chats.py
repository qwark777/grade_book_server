"""
API endpoints for group chats
"""
import os
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from typing import List, Optional
from datetime import datetime
from app.db.connection import get_db_connection
from app.core.security import get_current_user
from app.core.config import settings
from app.models.user import UserInDB
from app.models.group_chat import (
    GroupChatCreate, GroupChatUpdate, GroupChat, GroupChatDetail,
    GroupChatMember, AddMembersRequest, RemoveMemberRequest, UpdateMemberRoleRequest
)

router = APIRouter(prefix="/group-chats")


@router.post("", response_model=GroupChatDetail)
async def create_group_chat(
    data: GroupChatCreate,
    current_user: UserInDB = Depends(get_current_user)
):
    """Создать новый групповой чат"""
    if not data.name or not data.name.strip():
        raise HTTPException(status_code=400, detail="Group name is required")
    
    if not data.member_ids:
        raise HTTPException(status_code=400, detail="At least one member is required")
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Создаем группу
            await cursor.execute("""
                INSERT INTO group_chats (name, description, created_by)
                VALUES (%s, %s, %s)
            """, (data.name.strip(), data.description, current_user.id))
            await conn.commit()
            group_id = cursor.lastrowid
            
            # Добавляем создателя как админа
            member_ids_with_creator = [current_user.id] + [
                uid for uid in data.member_ids if uid != current_user.id
            ]
            
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
    finally:
        conn.close()


@router.get("", response_model=List[GroupChat])
async def get_my_group_chats(
    current_user: UserInDB = Depends(get_current_user)
):
    """Получить список групповых чатов пользователя"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT 
                    gc.id, gc.name, gc.description, gc.created_by,
                    gc.photo_url, gc.created_at, gc.updated_at,
                    COUNT(DISTINCT gcm.user_id) as member_count
                FROM group_chats gc
                INNER JOIN group_chat_members gcm ON gc.id = gcm.group_chat_id
                WHERE gcm.user_id = %s
                GROUP BY gc.id
                ORDER BY gc.updated_at DESC
            """, (current_user.id,))
            
            rows = await cursor.fetchall()
            
            groups = []
            for row in rows:
                # Получаем последнее сообщение
                await cursor.execute("""
                    SELECT id, sender_id, content, created_at
                    FROM messages
                    WHERE group_chat_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (row["id"],))
                last_msg = await cursor.fetchone()
                
                last_message = None
                if last_msg:
                    # Получаем имя отправителя
                    await cursor.execute("""
                        SELECT full_name FROM profiles WHERE user_id = %s
                    """, (last_msg["sender_id"],))
                    sender = await cursor.fetchone()
                    
                    last_message = {
                        "id": last_msg["id"],
                        "sender_id": last_msg["sender_id"],
                        "sender_name": sender["full_name"] if sender else "Unknown",
                        "content": last_msg["content"],
                        "created_at": last_msg["created_at"].isoformat() if last_msg["created_at"] else None,
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
            
            # Добавляем участников
            added_count = 0
            for user_id in data.user_ids:
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
    finally:
        conn.close()


@router.delete("/{group_id}/members/{user_id}", response_model=GroupChatDetail)
async def remove_member(
    group_id: int,
    user_id: int,
    current_user: UserInDB = Depends(get_current_user)
):
    """Удалить участника из группы"""
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
            
            # Проверяем формат файла
            ext = file.filename.split(".")[-1].lower() if file.filename else ""
            if ext not in ["jpg", "jpeg", "png"]:
                raise HTTPException(status_code=400, detail="Unsupported file type")
            
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
            with open(path, "wb") as buffer:
                buffer.write(await file.read())
            
            # Обновляем photo_url в базе данных
            photo_url = f"/group_photo/{group_id}"
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
