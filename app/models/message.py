from pydantic import BaseModel
from typing import Optional


class MessageIn(BaseModel):
    receiver_id: int
    content: str


class SendMessageRequest(BaseModel):
    receiver_id: Optional[int] = None  # Для личных чатов
    group_chat_id: Optional[int] = None  # Для групповых чатов
    content: str = ""
    attachment_name: Optional[str] = None
    attachment_path: Optional[str] = None

