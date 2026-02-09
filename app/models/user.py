from enum import Enum
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
    photo_url: Optional[str] = None
    work_place: Optional[str] = None  # для учителей
    location: Optional[str] = None
    subject: Optional[str] = None
    classes: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None


class ProfileUpdateRequest(BaseModel):
    full_name: str
    work_place: str
    location: str
    bio: str


