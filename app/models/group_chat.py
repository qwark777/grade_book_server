"""Models for group chats"""
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class GroupChatCreate(BaseModel):
    name: str
    description: Optional[str] = None
    member_ids: List[int]  # Список ID пользователей для добавления в группу


class GroupChatUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    photo_url: Optional[str] = None


class GroupChatMember(BaseModel):
    id: int
    user_id: int
    full_name: str
    photo_url: Optional[str] = None
    role: str  # 'admin' or 'member'
    joined_at: datetime


class GroupChat(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    created_by: int
    photo_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    member_count: int = 0
    last_message: Optional[dict] = None  # Последнее сообщение в группе


class GroupChatDetail(GroupChat):
    members: List[GroupChatMember] = []


class AddMembersRequest(BaseModel):
    user_ids: List[int]


class RemoveMemberRequest(BaseModel):
    user_id: int


class UpdateMemberRoleRequest(BaseModel):
    user_id: int
    role: str  # 'admin' or 'member'
