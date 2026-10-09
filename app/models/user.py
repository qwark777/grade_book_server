from enum import Enum
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


class User(BaseModel):
    username: str


class UserCreate(User):
    password: str


class UserInDB(User):
    id: int
    hashed_password: str
    role: str = "student"


class Token(BaseModel):
    access_token: str
    token_type: str


class UserRole(str, Enum):
    student = "student"
    teacher = "teacher"


class UserResponse(BaseModel):
    id: int
    full_name: str
    role: Optional[str] = None
    class_name: Optional[str] = None  # для студентов
    school_name: Optional[str] = None
    photo_url: Optional[str] = None
    work_place: Optional[str] = None  # для учителей
    location: Optional[str] = None
    subject: Optional[str] = None
    classes: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    is_online: bool = False
    last_seen_at: Optional[datetime] = None


class ProfileUpdateRequest(BaseModel):
    full_name: str
    work_place: str
    location: str
    bio: str
