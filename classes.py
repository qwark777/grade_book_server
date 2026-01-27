from enum import Enum
from typing import Optional, List

from pydantic import BaseModel



class User(BaseModel):
    username: str

class UserCreate(User):
    password: str

class UserInDB(User):
    id: int  # добавь это
    hashed_password: str
    role: str = "student"

class Token(BaseModel):
    access_token: str
    token_type: str

class Grade(BaseModel):
    student_id: int
    subject: str
    value: int
    date: str
    teacher_id: int

class Homework(BaseModel):
    class_id: int
    subject: str
    due_date: str
    description: str
    teacher_id: int

class Subject(BaseModel):
    name: str
    coefficient: float = 1.0

class Profile(BaseModel):
    user_id: int
    full_name: str
    bio: str
    photo_url: Optional[str] = None


class ClassItem(BaseModel):
    id: int
    name: str
    student_count: int



class ProfileUpdateRequest(BaseModel):
    full_name: str
    work_place: str
    location: str
    bio: str


class StudentResponse(BaseModel):
    id: int  # Измените id на user_id
    full_name: str
    work_place: Optional[str] = None  # Соответствует полю в БД
    location: Optional[str] = None
    bio: Optional[str] = None
    photo_url: Optional[str] = None

class StudentWithGradesResponse(StudentResponse):
    grades: List[dict] = []
    average_score: Optional[float] = None


class UserRole(str, Enum):
    student = "student"
    teacher = "teacher"

class UserResponse(BaseModel):
    id: int
    full_name: str
    class_name: Optional[str] = None  # для студентов
    photo_url: Optional[str] = None

    work_place: Optional[str] = None  # для учителей
    location: Optional[str] = None
    subject: Optional[str] = None
    classes: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None

class MessageIn(BaseModel):
    receiver_id: int
    content: str

class SendMessageRequest(BaseModel):
    receiver_id: int
    content: str