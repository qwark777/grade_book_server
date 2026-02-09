from datetime import date, time
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel


class WeekType(str, Enum):
    A = "A"
    B = "B"
    BOTH = "BOTH"


class ChangeType(str, Enum):
    REPLACE = "replace"
    CANCEL = "cancel"
    MOVE = "move"
    ADD = "add"


class HolidayType(str, Enum):
    HOLIDAY = "holiday"
    WEEKEND = "weekend"
    VACATION = "vacation"


class RoomType(str, Enum):
    CLASSROOM = "classroom"
    GYM = "gym"
    LAB = "lab"
    AUDITORIUM = "auditorium"
    LIBRARY = "library"


# ================== БАЗОВЫЕ МОДЕЛИ ==================

class AcademicPeriod(BaseModel):
    id: int
    name: str
    start_date: date
    end_date: date
    is_active: bool = False


class Room(BaseModel):
    id: int
    name: str
    building: Optional[str] = None
    capacity: Optional[int] = None
    room_type: RoomType = RoomType.CLASSROOM
    is_active: bool = True


class Holiday(BaseModel):
    id: int
    date: date
    name: str
    type: HolidayType = HolidayType.HOLIDAY
    affects_classes: bool = True
    description: Optional[str] = None


class AcademicWeek(BaseModel):
    id: int
    week_start_date: date
    week_end_date: date
    week_number: int
    week_type: WeekType
    academic_period_id: int
    is_holiday_week: bool = False


# ================== РАСПИСАНИЕ ==================

class TimetableTemplate(BaseModel):
    id: int
    class_id: int
    subject_id: int
    teacher_id: int
    room_id: Optional[int] = None
    day_of_week: int  # 1=Понедельник, 2=Вторник, ..., 7=Воскресенье
    lesson_number: int  # 1, 2, 3, 4, 5, 6, 7
    start_time: time
    end_time: time
    week_type: WeekType = WeekType.BOTH
    academic_period_id: int
    is_active: bool = True


class TimetableChange(BaseModel):
    id: int
    date: date
    class_id: int
    lesson_number: int
    change_type: ChangeType
    original_teacher_id: Optional[int] = None
    new_teacher_id: Optional[int] = None
    original_subject_id: Optional[int] = None
    new_subject_id: Optional[int] = None
    original_room_id: Optional[int] = None
    new_room_id: Optional[int] = None
    reason: Optional[str] = None
    created_by: int


# ================== ОТВЕТЫ API ==================

class LessonItem(BaseModel):
    lesson_number: int
    start_time: str  # "08:00"
    end_time: str    # "08:45"
    subject: str
    teacher: str
    room: str
    change_type: Optional[ChangeType] = None
    change_reason: Optional[str] = None
    class_id: Optional[int] = None  # для учителя: ID класса
    subject_id: Optional[int] = None  # для учителя: ID предмета


class DaySchedule(BaseModel):
    date: str  # "2024-01-15"
    day_of_week: str  # "Понедельник"
    is_holiday: bool = False
    holiday_name: Optional[str] = None
    holiday_reason: Optional[str] = None
    lessons: List[LessonItem] = []


class WeekSchedule(BaseModel):
    week_start_date: str  # "2024-01-15"
    week_end_date: str    # "2024-01-21"
    week_number: int
    week_type: WeekType
    days: List[DaySchedule] = []


# ================== ЗАПРОСЫ ==================

class TimetableRequest(BaseModel):
    class_id: int
    start_date: date
    end_date: date


class CreateTimetableTemplateRequest(BaseModel):
    class_id: int
    subject_id: int
    teacher_id: int
    room_id: Optional[int] = None
    day_of_week: int
    lesson_number: int
    start_time: time
    end_time: time
    week_type: WeekType = WeekType.BOTH


class CreateTimetableChangeRequest(BaseModel):
    date: date
    class_id: int
    lesson_number: int
    change_type: ChangeType
    original_teacher_id: Optional[int] = None
    new_teacher_id: Optional[int] = None
    original_subject_id: Optional[int] = None
    new_subject_id: Optional[int] = None
    original_room_id: Optional[int] = None
    new_room_id: Optional[int] = None
    reason: Optional[str] = None


class CreateHolidayRequest(BaseModel):
    date: date
    name: str
    type: HolidayType = HolidayType.HOLIDAY
    affects_classes: bool = True
    description: Optional[str] = None


class LessonTemplateData(BaseModel):
    day_of_week: int  # 1-5 (понедельник-пятница)
    lesson_number: int
    start_time: str
    end_time: str
    subject: str
    teacher: str
    room: str


class TimetableTemplateRequest(BaseModel):
    class_id: int
    lessons: List[LessonTemplateData]
