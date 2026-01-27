from datetime import datetime, UTC
from fastapi import APIRouter, Depends

from app.core.security import get_current_user, encrypt_message, decrypt_message
from app.db.connection import get_db_connection
from app.models.message import SendMessageRequest
from app.websocket.manager import ws_manager

router = APIRouter()


@router.post("/messages/send")
async def send_message(data: SendMessageRequest, current_user=Depends(get_current_user)):
    """Send message to another user or group chat"""
    if not data.receiver_id and not data.group_chat_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Either receiver_id or group_chat_id is required")
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Личный чат
            if data.receiver_id:
                user1_id = current_user.id
                user2_id = data.receiver_id
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

                # Encrypt and save message
                encrypted = encrypt_message(data.content)
                await cursor.execute("""
                    INSERT INTO messages (conversation_id, sender_id, content)
                    VALUES (%s, %s, %s)
                """, (conversation_id, current_user.id, encrypted))
                await conn.commit()

                # Prepare payload for clients
                message_row = {
                    "id": cursor.lastrowid,
                    "conversation_id": conversation_id,
                    "group_chat_id": None,
                    "sender_id": current_user.id,
                    "recipient_id": user2_id,
                    "content": data.content,
                    "created_at": datetime.now(UTC).isoformat(),
                    "read_at": None,
                    "is_read": False
                }

                # Send via WebSocket
                await ws_manager.send_to_user(user2_id, {"type": "message_new", "data": message_row})
                await ws_manager.send_to_user(user1_id, {"type": "message_echo", "data": message_row})

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

                # Encrypt and save message
                encrypted = encrypt_message(data.content)
                await cursor.execute("""
                    INSERT INTO messages (group_chat_id, sender_id, content)
                    VALUES (%s, %s, %s)
                """, (data.group_chat_id, current_user.id, encrypted))
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
                    "content": data.content,
                    "created_at": datetime.now(UTC).isoformat(),
                    "read_at": None,
                    "is_read": False
                }

                # Send via WebSocket всем участникам группы
                for member_id in member_ids:
                    if member_id != current_user.id:
                        await ws_manager.send_to_user(member_id, {"type": "group_message_new", "data": message_row})
                    else:
                        await ws_manager.send_to_user(member_id, {"type": "group_message_echo", "data": message_row})

                return {"status": "ok", "group_chat_id": data.group_chat_id, "message": message_row}
    finally:
        conn.close()


@router.get("/messages/{user_id}")
async def get_messages(user_id: int, current_user=Depends(get_current_user)):
    """Get messages with specific user"""
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
                SELECT id, sender_id, content, created_at, read_at
                FROM messages
                WHERE conversation_id = %s
                ORDER BY created_at
            """, (conversation_id,))
            rows = await cursor.fetchall()

            # Находим последнее сообщение в чате
            last_message_id = None
            if rows:
                last_message_id = rows[-1]["id"]
            
            # Получаем текущий last_read_message_id перед обновлением
            await cursor.execute("""
                SELECT user1_id, user2_id, user1_last_read_message_id, user2_last_read_message_id
                FROM conversations 
                WHERE id = %s
            """, (conversation_id,))
            conv = await cursor.fetchone()
            
            current_last_read_id = None
            if conv:
                if current_user.id == conv["user1_id"]:
                    current_last_read_id = conv["user1_last_read_message_id"]
                else:
                    current_last_read_id = conv["user2_last_read_message_id"]
            
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

            messages = []
            # Используем обновленный last_read_message_id (или last_message_id, если это первый раз)
            last_read_id_to_use = last_message_id if last_message_id else current_last_read_id
            
            for row in rows:
                try:
                    plaintext = decrypt_message(row["content"])
                except Exception:
                    plaintext = "[не удалось расшифровать]"
                
                # Сообщение считается прочитанным, если:
                # 1. У него есть read_at (обратная совместимость)
                # 2. Его id <= last_read_message_id для сообщений от другого пользователя
                # 3. Сообщение от текущего пользователя всегда прочитано
                is_read_message = False
                if row["read_at"]:
                    is_read_message = True
                elif row["sender_id"] == current_user.id:
                    is_read_message = True  # Свои сообщения всегда прочитаны
                elif last_read_id_to_use and row["id"] <= last_read_id_to_use:
                    is_read_message = True  # Сообщение было прочитано (id <= last_read_message_id)
                
                messages.append({
                    "id": row["id"],
                    "sender_id": row["sender_id"],
                    "content": plaintext,
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
            await conn.commit()
            
            return {"status": "ok"}
    finally:
        conn.close()


@router.get("/conversations")
async def get_user_conversations(current_user=Depends(get_current_user)):
    """Get user conversations"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT c.id AS conversation_id,
                       u.id AS user_id,
                       TRIM(BOTH '"' FROM p.full_name) AS full_name,
                       p.photo_url,
                       m.content,
                       m.created_at,
                       m.sender_id
                FROM conversations c
                JOIN users u ON u.id = IF(c.user1_id = %s, c.user2_id, c.user1_id)
                JOIN profiles p ON p.user_id = u.id
                LEFT JOIN (
                    SELECT conversation_id, MAX(created_at) AS max_time
                    FROM messages GROUP BY conversation_id
                ) latest ON latest.conversation_id = c.id
                LEFT JOIN messages m ON m.conversation_id = c.id AND m.created_at = latest.max_time
                WHERE %s IN (c.user1_id, c.user2_id)
                ORDER BY m.created_at DESC
            """, (current_user.id, current_user.id))
            rows = await cursor.fetchall()
            connected_users = ws_manager.get_connected_users()
            return [{
                "conversation_id": r["conversation_id"],
                "user_id": r["user_id"],
                "full_name": r["full_name"],
                "photo_url": r["photo_url"],
                "last_message": decrypt_message(r["content"]) if r["content"] else None,
                "last_time": r["created_at"],
                "last_sender_id": r["sender_id"],
                "is_online": r["user_id"] in connected_users
            } for r in rows if r["content"]]
    finally:
        conn.close()


@router.get("/group-chats/{group_id}/messages")
async def get_group_chat_messages(group_id: int, current_user=Depends(get_current_user)):
    """Get messages from a group chat"""
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
                SELECT id, sender_id, content, created_at, read_at
                FROM messages
                WHERE group_chat_id = %s
                ORDER BY created_at
            """, (group_id,))
            rows = await cursor.fetchall()

            # Получаем информацию об отправителях
            messages = []
            for row in rows:
                await cursor.execute("""
                    SELECT full_name FROM profiles WHERE user_id = %s
                """, (row["sender_id"],))
                sender = await cursor.fetchone()

                decrypted_content = decrypt_message(row["content"])
                messages.append({
                    "id": row["id"],
                    "group_chat_id": group_id,
                    "conversation_id": None,
                    "sender_id": row["sender_id"],
                    "sender_name": sender["full_name"] if sender else "Unknown",
                    "content": decrypted_content,
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

