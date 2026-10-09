import mimetypes
import os
import uuid
import logging
from datetime import datetime, UTC

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from starlette.responses import FileResponse

from app.core.config import settings
from app.core.entitlements import get_school_id_for_user, require_school_feature
from app.core.subscription_limits import change_school_storage, consume_school_quota
from app.core.file_uploads import (
    MAX_MESSAGE_ATTACHMENT_BYTES,
    MESSAGE_ATTACHMENT_EXTS,
    safe_original_filename,
    safe_storage_path,
    save_upload_limited,
    validate_upload_metadata,
)
from app.core.security import (
    decrypt_message,
    encrypt_message,
    get_current_user,
)
from app.db.connection import get_db_connection
from app.models.message import SendMessageRequest
from app.services.notifications import create_notification
from app.services.achievement_service import evaluate_and_award_achievements
from app.websocket.manager import ws_manager

router = APIRouter()
logger = logging.getLogger(__name__)

APPLICATION_OWNER_ROLES = {"owner", "superadmin", "root"}


def _safe_message_text(content: str | None, attachment_name: str | None = None) -> str:
    text = (content or "").strip()
    if text:
        return text
    if attachment_name:
        return f"Файл: {attachment_name}"
    return ""


async def _can_send_direct_message(
    cursor,
    current_user,
    receiver_id: int,
    sender_school_id: int | None,
) -> tuple[bool, str]:
    if receiver_id == current_user.id:
        return False, "Нельзя отправить сообщение самому себе"

    await cursor.execute(
        "SELECT id, role FROM users WHERE id = %s",
        (receiver_id,),
    )
    receiver = await cursor.fetchone()
    if not receiver:
        return False, "Пользователь не найден"

    receiver_role = receiver["role"] if isinstance(receiver, dict) else receiver[1]
    if current_user.role in APPLICATION_OWNER_ROLES:
        return True, ""
    if receiver_role in APPLICATION_OWNER_ROLES:
        return True, ""
    if sender_school_id is None:
        return False, "Нельзя написать этому пользователю"

    await cursor.execute(
        """
        SELECT 1
        FROM users u
        LEFT JOIN school_users su ON su.user_id = u.id
        LEFT JOIN school_admins sa ON sa.admin_user_id = u.id
        LEFT JOIN class_students cs ON cs.student_id = u.id
        LEFT JOIN classes c_student ON c_student.id = cs.class_id
        LEFT JOIN class_teachers ct ON ct.teacher_id = u.id
        LEFT JOIN classes c_teacher ON c_teacher.id = ct.class_id
        LEFT JOIN class_subject_teachers cst ON cst.teacher_id = u.id
        LEFT JOIN classes c_subject_teacher ON c_subject_teacher.id = cst.class_id
        LEFT JOIN parent_students ps_contact ON ps_contact.parent_id = u.id
        LEFT JOIN class_students cs_parent ON cs_parent.student_id = ps_contact.student_id
        LEFT JOIN classes c_parent ON c_parent.id = cs_parent.class_id
        WHERE u.id = %s
          AND (
            su.school_id = %s
            OR sa.school_id = %s
            OR c_student.school_id = %s
            OR c_teacher.school_id = %s
            OR c_subject_teacher.school_id = %s
            OR c_parent.school_id = %s
          )
        LIMIT 1
        """,
        (
            receiver_id,
            sender_school_id,
            sender_school_id,
            sender_school_id,
            sender_school_id,
            sender_school_id,
            sender_school_id,
        ),
    )
    if await cursor.fetchone():
        return True, ""
    return False, "Нельзя написать этому пользователю: он не относится к вашей школе"


@router.post("/messages/send")
async def send_message(data: SendMessageRequest, current_user=Depends(get_current_user)):
    """Send message to another user or group chat"""
    await require_school_feature(
        current_user,
        "messages_enabled",
        detail="Сообщения отключены в настройках школы",
    )
    if not data.receiver_id and not data.group_chat_id:
        raise HTTPException(status_code=400, detail="Either receiver_id or group_chat_id is required")
    if not (data.content or "").strip() and not data.attachment_path:
        raise HTTPException(status_code=400, detail="Message content or attachment is required")
    school_id = await get_school_id_for_user(current_user)
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT COALESCE(NULLIF(TRIM(BOTH '"' FROM p.full_name), ''), u.username) AS sender_name
                FROM users u
                LEFT JOIN profiles p ON p.user_id = u.id
                WHERE u.id = %s
                """,
                (current_user.id,),
            )
            sender_row = await cursor.fetchone()
            sender_name = sender_row["sender_name"] if sender_row else f"Пользователь {current_user.id}"

            # Личный чат
            if data.receiver_id:
                user1_id = current_user.id
                user2_id = data.receiver_id
                can_send, deny_reason = await _can_send_direct_message(
                    cursor,
                    current_user,
                    user2_id,
                    school_id,
                )
                if not can_send:
                    status_code = 404 if deny_reason == "Пользователь не найден" else 403
                    raise HTTPException(status_code=status_code, detail=deny_reason)
                user_min_id = min(user1_id, user2_id)
                user_max_id = max(user1_id, user2_id)

                # Find or create conversation
                await cursor.execute("""
                    SELECT id FROM conversations
                    WHERE user_min_id = %s AND user_max_id = %s
                """, (user_min_id, user_max_id))
                conv = await cursor.fetchone()
                if not conv:
                    await cursor.execute("""
                        INSERT INTO conversations (user1_id, user2_id, user_min_id, user_max_id)
                        VALUES (%s, %s, %s, %s)
                    """, (user1_id, user2_id, user_min_id, user_max_id))
                    await conn.commit()
                    conversation_id = cursor.lastrowid
                else:
                    conversation_id = conv["id"]

                if school_id is not None:
                    await consume_school_quota(
                        cursor,
                        school_id=school_id,
                        entitlement_key="messages.daily",
                        unlimited_key="messages.unlimited",
                        user_id=current_user.id,
                        daily=True,
                    )
                # Encrypt and save message
                encrypted = encrypt_message(data.content or "")
                await cursor.execute("""
                    INSERT INTO messages (
                        conversation_id, sender_id, content, attachment_name, attachment_path
                    )
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    conversation_id,
                    current_user.id,
                    encrypted,
                    data.attachment_name,
                    data.attachment_path,
                ))
                await conn.commit()

                # Prepare payload for clients
                message_row = {
                    "id": cursor.lastrowid,
                    "conversation_id": conversation_id,
                    "group_chat_id": None,
                    "sender_id": current_user.id,
                    "recipient_id": user2_id,
                    "content": data.content or "",
                    "attachment_name": data.attachment_name,
                    "attachment_path": data.attachment_path,
                    "created_at": datetime.now(UTC).isoformat(),
                    "read_at": None,
                    "is_read": False
                }

                # Send via WebSocket
                await ws_manager.send_to_user(user2_id, {"type": "message_new", "data": message_row})
                await ws_manager.send_to_user(user1_id, {"type": "message_echo", "data": message_row})
                await create_notification(
                    user2_id,
                    f"Новое сообщение от {sender_name}",
                    _safe_message_text(data.content, data.attachment_name)[:160],
                    notification_type="message",
                    sender_id=current_user.id,
                    entity_type="conversation",
                    entity_id=conversation_id,
                    route_name="messages",
                    route_params={"user_id": user1_id},
                )
                try:
                    await evaluate_and_award_achievements(
                        current_user.id,
                        only_codes={"heart_of_class"},
                    )
                except Exception:
                    logger.exception("Failed to evaluate message achievements")

                return {"status": "ok", "conversation_id": conversation_id, "message": message_row}
            
            # Групповой чат
            else:
                # Проверяем, что пользователь является участником группы
                await cursor.execute("""
                    SELECT user_id FROM group_chat_members
                    WHERE group_chat_id = %s AND user_id = %s
                """, (data.group_chat_id, current_user.id))
                member = await cursor.fetchone()
                if not member:
                    from fastapi import HTTPException
                    raise HTTPException(status_code=403, detail="You are not a member of this group")

                # Получаем список участников группы
                await cursor.execute("""
                    SELECT user_id FROM group_chat_members
                    WHERE group_chat_id = %s
                """, (data.group_chat_id,))
                members = await cursor.fetchall()
                member_ids = [row["user_id"] for row in members]

                if school_id is not None:
                    await consume_school_quota(
                        cursor,
                        school_id=school_id,
                        entitlement_key="messages.daily",
                        unlimited_key="messages.unlimited",
                        user_id=current_user.id,
                        daily=True,
                    )
                # Encrypt and save message
                encrypted = encrypt_message(data.content or "")
                await cursor.execute("""
                    INSERT INTO messages (
                        group_chat_id, sender_id, content, attachment_name, attachment_path
                    )
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    data.group_chat_id,
                    current_user.id,
                    encrypted,
                    data.attachment_name,
                    data.attachment_path,
                ))
                await conn.commit()
                message_id = cursor.lastrowid

                # Обновляем updated_at группы
                await cursor.execute("""
                    UPDATE group_chats
                    SET updated_at = NOW()
                    WHERE id = %s
                """, (data.group_chat_id,))
                await conn.commit()

                # Prepare payload for clients
                message_row = {
                    "id": message_id,
                    "conversation_id": None,
                    "group_chat_id": data.group_chat_id,
                    "sender_id": current_user.id,
                    "recipient_id": None,
                    "content": data.content or "",
                    "attachment_name": data.attachment_name,
                    "attachment_path": data.attachment_path,
                    "created_at": datetime.now(UTC).isoformat(),
                    "read_at": None,
                    "is_read": False
                }

                # Send via WebSocket всем участникам группы
                for member_id in member_ids:
                    if member_id != current_user.id:
                        await ws_manager.send_to_user(member_id, {"type": "group_message_new", "data": message_row})
                        await create_notification(
                            member_id,
                            f"Новое сообщение в группе от {sender_name}",
                            _safe_message_text(data.content, data.attachment_name)[:160],
                            notification_type="group_message",
                            sender_id=current_user.id,
                            entity_type="group_chat",
                            entity_id=data.group_chat_id,
                            route_name="messages",
                            route_params={"group_chat_id": data.group_chat_id},
                        )
                    else:
                        await ws_manager.send_to_user(member_id, {"type": "group_message_echo", "data": message_row})

                try:
                    await evaluate_and_award_achievements(
                        current_user.id,
                        only_codes={"heart_of_class"},
                    )
                except Exception:
                    logger.exception("Failed to evaluate message achievements")

                return {"status": "ok", "group_chat_id": data.group_chat_id, "message": message_row}
    finally:
        conn.close()


@router.post("/messages/attachment")
async def upload_message_attachment(
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    await require_school_feature(
        current_user,
        "messages_enabled",
        detail="Сообщения отключены в настройках школы",
    )
    filename, ext = validate_upload_metadata(
        file,
        allowed_exts=MESSAGE_ATTACHMENT_EXTS,
    )
    filename = safe_original_filename(filename)
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    full_path = os.path.join(settings.MESSAGE_ATTACHMENTS_DIR, stored_name)
    written = await save_upload_limited(
        file,
        full_path,
        max_bytes=MAX_MESSAGE_ATTACHMENT_BYTES,
    )
    school_id = await get_school_id_for_user(current_user)
    if school_id is not None:
        conn = await get_db_connection()
        try:
            async with conn.cursor() as cursor:
                await change_school_storage(
                    cursor,
                    school_id=school_id,
                    delta_bytes=written,
                )
                await conn.commit()
        except Exception:
            if os.path.isfile(full_path):
                os.remove(full_path)
            await conn.rollback()
            raise
        finally:
            conn.close()

    return {
        "status": "ok",
        "attachment_name": filename,
        "attachment_path": stored_name,
    }


@router.get("/messages/{message_id}/attachment/file")
async def download_message_attachment(
    message_id: int,
    current_user=Depends(get_current_user),
):
    await require_school_feature(
        current_user,
        "messages_enabled",
        detail="Сообщения отключены в настройках школы",
    )
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id, conversation_id, group_chat_id, sender_id,
                       attachment_path, attachment_name
                FROM messages
                WHERE id = %s
                """,
                (message_id,),
            )
            row = await cursor.fetchone()
            if not row or not row.get("attachment_path"):
                raise HTTPException(status_code=404, detail="Attachment not found")

            if row.get("conversation_id"):
                await cursor.execute(
                    """
                    SELECT id FROM conversations
                    WHERE id = %s AND %s IN (user1_id, user2_id)
                    """,
                    (row["conversation_id"], current_user.id),
                )
                access = await cursor.fetchone()
                if not access:
                    raise HTTPException(status_code=403, detail="Access denied")
            elif row.get("group_chat_id"):
                await cursor.execute(
                    """
                    SELECT 1 FROM group_chat_members
                    WHERE group_chat_id = %s AND user_id = %s
                    """,
                    (row["group_chat_id"], current_user.id),
                )
                access = await cursor.fetchone()
                if not access:
                    raise HTTPException(status_code=403, detail="Access denied")
            else:
                raise HTTPException(status_code=404, detail="Message chat not found")

            full_path = safe_storage_path(
                settings.MESSAGE_ATTACHMENTS_DIR,
                row["attachment_path"],
            )
            if not os.path.exists(full_path):
                raise HTTPException(status_code=404, detail="File missing")

            media, _ = mimetypes.guess_type(full_path)
            return FileResponse(
                full_path,
                media_type=media or "application/octet-stream",
                filename=row["attachment_name"],
            )
    finally:
        conn.close()


@router.get("/messages/{user_id}")
async def get_messages(user_id: int, current_user=Depends(get_current_user)):
    """Get messages with specific user"""
    await require_school_feature(
        current_user,
        "messages_enabled",
        detail="Сообщения отключены в настройках школы",
    )
    conn = await get_db_connection()
    try:
        user_min_id = min(current_user.id, user_id)
        user_max_id = max(current_user.id, user_id)

        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT id FROM conversations
                WHERE user_min_id = %s AND user_max_id = %s
            """, (user_min_id, user_max_id))
            conv = await cursor.fetchone()
            if not conv:
                return []

            conversation_id = conv["id"]
            
            # При получении сообщений считаем, что пользователь прочитал все сообщения
            # Отправляем "эхо" сообщение от текущего пользователя, чтобы обновить последнее сообщение
            # Это нужно для правильного подсчета непрочитанных
            await cursor.execute("""
                SELECT MAX(created_at) AS last_sent_time
                FROM messages
                WHERE conversation_id = %s AND sender_id = %s
            """, (conversation_id, current_user.id))
            last_sent = await cursor.fetchone()
            
            # Если пользователь еще не отправлял сообщения, отправляем "системное" сообщение для отметки прочтения
            if not last_sent or not last_sent["last_sent_time"]:
                # Отправляем пустое системное сообщение (не сохраняем в БД, только для логики)
                # Вместо этого просто обновляем логику подсчета непрочитанных
                pass
            
            await cursor.execute("""
                SELECT id, sender_id, content, attachment_name, attachment_path, created_at, read_at
                FROM messages
                WHERE conversation_id = %s
                ORDER BY created_at
            """, (conversation_id,))
            rows = await cursor.fetchall()

            # Находим последнее сообщение в чате
            last_message_id = None
            if rows:
                last_message_id = rows[-1]["id"]
            
            # Получаем last_read_message_id обоих участников перед обновлением
            await cursor.execute("""
                SELECT user1_id, user2_id, user1_last_read_message_id, user2_last_read_message_id
                FROM conversations 
                WHERE id = %s
            """, (conversation_id,))
            conv = await cursor.fetchone()
            
            current_last_read_id = None
            other_last_read_id = None
            if conv:
                if current_user.id == conv["user1_id"]:
                    current_last_read_id = conv["user1_last_read_message_id"]
                    other_last_read_id = conv["user2_last_read_message_id"]
                else:
                    current_last_read_id = conv["user2_last_read_message_id"]
                    other_last_read_id = conv["user1_last_read_message_id"]
            
            # Обновляем last_read_message_id для текущего пользователя в conversations
            if conv and last_message_id:
                if current_user.id == conv["user1_id"]:
                    await cursor.execute("""
                        UPDATE conversations
                        SET user1_last_read_message_id = %s
                        WHERE id = %s
                    """, (last_message_id, conversation_id))
                else:
                    await cursor.execute("""
                        UPDATE conversations
                        SET user2_last_read_message_id = %s
                        WHERE id = %s
                    """, (last_message_id, conversation_id))
                
                # Отмечаем все сообщения от другого пользователя как прочитанные (для обратной совместимости)
                await cursor.execute("""
                    UPDATE messages
                    SET read_at = NOW()
                    WHERE conversation_id = %s
                      AND sender_id = %s
                      AND read_at IS NULL
                """, (conversation_id, user_id))
            
            await conn.commit()
            if last_message_id:
                await ws_manager.send_to_user(
                    user_id,
                    {
                        "type": "messages_read",
                        "data": {
                            "conversation_id": conversation_id,
                            "reader_id": current_user.id,
                            "last_read_message_id": last_message_id,
                            "read_at": datetime.now(UTC).isoformat(),
                        },
                    },
                )

            messages = []
            # После открытия чата текущий пользователь считается прочитавшим все сообщения до последнего.
            current_last_read_after_open = last_message_id if last_message_id else current_last_read_id
            
            for row in rows:
                try:
                    plaintext = decrypt_message(row["content"])
                except Exception:
                    plaintext = "[не удалось расшифровать]"
                
                # Для исходящих сообщений статус "прочитано" зависит от того,
                # открыл ли чат второй пользователь. Для входящих — от текущего пользователя.
                if row["read_at"]:
                    is_read_message = True
                elif row["sender_id"] == current_user.id:
                    is_read_message = bool(
                        other_last_read_id and row["id"] <= other_last_read_id
                    )
                else:
                    is_read_message = bool(
                        current_last_read_after_open and row["id"] <= current_last_read_after_open
                    )
                
                messages.append({
                    "id": row["id"],
                    "sender_id": row["sender_id"],
                    "content": plaintext,
                    "attachment_name": row.get("attachment_name"),
                    "attachment_path": row.get("attachment_path"),
                    "created_at": row["created_at"].isoformat(),
                    "read_at": row["read_at"].isoformat() if row["read_at"] else None,
                    "is_read": is_read_message
                })
            return messages
    finally:
        conn.close()


@router.get("/conversations/{user_id}/unread")
async def get_unread_count(user_id: int, current_user=Depends(get_current_user)):
    """Get unread message count for a conversation with specific user"""
    await require_school_feature(
        current_user,
        "messages_enabled",
        detail="Сообщения отключены в настройках школы",
    )
    conn = await get_db_connection()
    try:
        user_min_id = min(current_user.id, user_id)
        user_max_id = max(current_user.id, user_id)
        
        async with conn.cursor() as cursor:
            # Find conversation
            await cursor.execute("""
                SELECT id FROM conversations
                WHERE user_min_id = %s AND user_max_id = %s
            """, (user_min_id, user_max_id))
            conv = await cursor.fetchone()
            if not conv:
                return {"unread_count": 0}
            
            conversation_id = conv["id"]
            
            # Получаем last_read_message_id для текущего пользователя
            await cursor.execute("""
                SELECT user1_id, user2_id, user1_last_read_message_id, user2_last_read_message_id
                FROM conversations
                WHERE id = %s
            """, (conversation_id,))
            conv_data = await cursor.fetchone()
            
            # Определяем, какой это user (user1 или user2)
            last_read_message_id = None
            if conv_data:
                if current_user.id == conv_data["user1_id"]:
                    last_read_message_id = conv_data["user1_last_read_message_id"]
                else:
                    last_read_message_id = conv_data["user2_last_read_message_id"]
            
            # Подсчитываем непрочитанные сообщения от другого пользователя после last_read_message_id
            if last_read_message_id:
                # Считаем сообщения от другого пользователя, созданные после последнего прочитанного
                await cursor.execute("""
                    SELECT COUNT(*) AS unread_count
                    FROM messages m1
                    WHERE m1.conversation_id = %s
                      AND m1.sender_id = %s
                      AND m1.id > %s
                """, (conversation_id, user_id, last_read_message_id))
            else:
                # Если last_read_message_id нет, считаем все сообщения от другого пользователя
                await cursor.execute("""
                    SELECT COUNT(*) AS unread_count
                    FROM messages
                    WHERE conversation_id = %s
                      AND sender_id = %s
                """, (conversation_id, user_id))
            
            result = await cursor.fetchone()
            return {"unread_count": result["unread_count"] if result else 0}
    finally:
        conn.close()


@router.post("/messages/{message_id}/mark-read")
async def mark_message_as_read(message_id: int, current_user=Depends(get_current_user)):
    """Mark a specific message as read"""
    await require_school_feature(
        current_user,
        "messages_enabled",
        detail="Сообщения отключены в настройках школы",
    )
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем, что сообщение существует и адресовано текущему пользователю
            await cursor.execute("""
                SELECT m.id, m.sender_id, m.conversation_id, c.user1_id, c.user2_id
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                WHERE m.id = %s
            """, (message_id,))
            message = await cursor.fetchone()
            
            if not message:
                return {"status": "error", "message": "Message not found"}
            
            # Проверяем, что сообщение адресовано текущему пользователю
            recipient_id = message["user1_id"] if message["user2_id"] == message["sender_id"] else message["user2_id"]
            if recipient_id != current_user.id:
                return {"status": "error", "message": "Unauthorized"}
            
            # Отмечаем сообщение как прочитанное
            await cursor.execute("""
                UPDATE messages
                SET read_at = NOW()
                WHERE id = %s AND read_at IS NULL
            """, (message_id,))
            if current_user.id == message["user1_id"]:
                await cursor.execute(
                    """
                    UPDATE conversations
                    SET user1_last_read_message_id = GREATEST(
                        COALESCE(user1_last_read_message_id, 0),
                        %s
                    )
                    WHERE id = %s
                    """,
                    (message_id, message["conversation_id"]),
                )
            else:
                await cursor.execute(
                    """
                    UPDATE conversations
                    SET user2_last_read_message_id = GREATEST(
                        COALESCE(user2_last_read_message_id, 0),
                        %s
                    )
                    WHERE id = %s
                    """,
                    (message_id, message["conversation_id"]),
                )
            await conn.commit()

            read_at = datetime.now(UTC).isoformat()
            await ws_manager.send_to_user(
                message["sender_id"],
                {
                    "type": "messages_read",
                    "data": {
                        "conversation_id": message["conversation_id"],
                        "reader_id": current_user.id,
                        "last_read_message_id": message_id,
                        "read_at": read_at,
                    },
                },
            )

            return {"status": "ok", "read_at": read_at}
    finally:
        conn.close()


@router.get("/conversations")
async def get_user_conversations(current_user=Depends(get_current_user)):
    """Get user conversations"""
    await require_school_feature(
        current_user,
        "messages_enabled",
        detail="Сообщения отключены в настройках школы",
    )
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT c.id AS conversation_id,
                       u.id AS user_id,
                       COALESCE(NULLIF(TRIM(BOTH '"' FROM p.full_name), ''), u.username) AS full_name,
                       COALESCE(p.photo_url, CONCAT('/api/v1/profile_photo/', u.id)) AS photo_url,
                       m.content,
                       m.attachment_name,
                       m.created_at,
                       m.sender_id,
                       (
                           SELECT COUNT(*)
                           FROM messages unread
                           WHERE unread.conversation_id = c.id
                             AND unread.sender_id != %s
                             AND unread.id > COALESCE(
                                 CASE
                                     WHEN c.user1_id = %s
                                         THEN c.user1_last_read_message_id
                                     ELSE c.user2_last_read_message_id
                                 END,
                                 0
                             )
                       ) AS unread_count
                FROM conversations c
                JOIN users u ON u.id = IF(c.user1_id = %s, c.user2_id, c.user1_id)
                LEFT JOIN profiles p ON p.user_id = u.id
                LEFT JOIN (
                    SELECT conversation_id, MAX(id) AS max_message_id
                    FROM messages
                    WHERE conversation_id IS NOT NULL
                    GROUP BY conversation_id
                ) latest ON latest.conversation_id = c.id
                LEFT JOIN messages m ON m.id = latest.max_message_id
                WHERE %s IN (c.user1_id, c.user2_id)
                ORDER BY m.created_at DESC
            """, (current_user.id, current_user.id, current_user.id, current_user.id))
            rows = await cursor.fetchall()
            connected_users = await ws_manager.get_connected_users()
            return [{
                "conversation_id": r["conversation_id"],
                "user_id": r["user_id"],
                "full_name": r["full_name"],
                "photo_url": r["photo_url"],
                "last_message": _safe_message_text(
                    decrypt_message(r["content"]) if r["content"] else "",
                    r.get("attachment_name"),
                ),
                "last_time": r["created_at"],
                "last_sender_id": r["sender_id"],
                "is_online": r["user_id"] in connected_users,
                "unread_count": int(r.get("unread_count") or 0),
            } for r in rows if r["content"] or r.get("attachment_name")]
    finally:
        conn.close()


@router.get("/group-chats/{group_id}/messages")
async def get_group_chat_messages(group_id: int, current_user=Depends(get_current_user)):
    """Get messages from a group chat"""
    await require_school_feature(
        current_user,
        "messages_enabled",
        detail="Сообщения отключены в настройках школы",
    )
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем, что пользователь является участником группы
            await cursor.execute("""
                SELECT user_id FROM group_chat_members
                WHERE group_chat_id = %s AND user_id = %s
            """, (group_id, current_user.id))
            member = await cursor.fetchone()
            if not member:
                from fastapi import HTTPException
                raise HTTPException(status_code=403, detail="You are not a member of this group")

            await cursor.execute("""
                SELECT m.id, m.sender_id, m.content, m.attachment_name,
                       m.attachment_path, m.created_at, m.read_at,
                       COALESCE(
                           NULLIF(TRIM(BOTH '"' FROM p.full_name), ''),
                           u.username,
                           'Unknown'
                       ) AS sender_name
                FROM messages m
                LEFT JOIN users u ON u.id = m.sender_id
                LEFT JOIN profiles p ON p.user_id = m.sender_id
                WHERE m.group_chat_id = %s
                ORDER BY m.id
            """, (group_id,))
            rows = await cursor.fetchall()

            messages = []
            for row in rows:
                decrypted_content = decrypt_message(row["content"])
                messages.append({
                    "id": row["id"],
                    "group_chat_id": group_id,
                    "conversation_id": None,
                    "sender_id": row["sender_id"],
                    "sender_name": row["sender_name"],
                    "content": decrypted_content,
                    "attachment_name": row.get("attachment_name"),
                    "attachment_path": row.get("attachment_path"),
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "read_at": row["read_at"].isoformat() if row["read_at"] else None,
                    "is_read": row["read_at"] is not None
                })

            # Обновляем статус прочтения для текущего пользователя
            if messages:
                last_message_id = messages[-1]["id"]
                await cursor.execute("""
                    INSERT INTO group_chat_read_status (group_chat_id, user_id, last_read_message_id)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE last_read_message_id = %s
                """, (group_id, current_user.id, last_message_id, last_message_id))
                await conn.commit()

            return messages
    finally:
        conn.close()
