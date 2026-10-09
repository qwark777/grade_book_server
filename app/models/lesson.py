from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date, time


class LessonBase(BaseModel):
    title: str
    description: Optional[str] = None
    subject: str
    price: Optional[float] = None
    max_students: int = 10
    duration_minutes: int = 60
    is_online: bool = False
    location: Optional[str] = None
    online_link: Optional[str] = None


class LessonCreate(LessonBase):
    school_id: Optional[int] = None


class LessonUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    subject: Optional[str] = None
    price: Optional[float] = None
    max_students: Optional[int] = None
    duration_minutes: Optional[int] = None
    is_online: Optional[bool] = None
    location: Optional[str] = None
    online_link: Optional[str] = None


class LessonInDB(LessonBase):
    id: int
    tutor_id: int
    school_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class Lesson(LessonInDB):
    pass


class LessonEnrollmentBase(BaseModel):
    lesson_id: int
    student_id: int
    status: str = "enrolled"
    payment_status: str = "pending"


class LessonEnrollmentCreate(LessonEnrollmentBase):
    pass


class LessonEnrollmentInDB(LessonEnrollmentBase):
    id: int
    enrolled_at: datetime

    class Config:
        from_attributes = True


class LessonEnrollment(LessonEnrollmentInDB):
    pass


class LessonBalanceIssueRequest(BaseModel):
    student_id: int
    total_lessons: int = 1
    purchase_price: Optional[float] = None
    payment_method: Optional[str] = None
    valid_until: Optional[datetime] = None
    notes: Optional[str] = None
    add_to_existing: bool = True


class LessonBalanceWithDetails(BaseModel):
    enrollment_id: int
    lesson_id: int
    lesson_title: str
    lesson_subject: str
    student_id: int
    student_name: str
    tutor_id: int
    tutor_name: Optional[str] = None
    total_lessons: int = 1
    remaining_lessons: int = 1
    purchase_price: Optional[float] = None
    purchase_date: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    status: str = "enrolled"
    payment_status: str = "pending"
    payment_method: Optional[str] = None
    notes: Optional[str] = None
    next_session_date: Optional[date] = None
    next_session_start_time: Optional[time] = None


class LessonScheduleBase(BaseModel):
    lesson_id: int
    start_datetime: datetime
    end_datetime: datetime
    is_recurring: bool = False
    recurrence_pattern: Optional[str] = None


class LessonScheduleCreate(LessonScheduleBase):
    pass


class LessonScheduleInDB(LessonScheduleBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class LessonSchedule(LessonScheduleInDB):
    pass


class LessonSessionCreate(BaseModel):
    student_id: int
    session_date: date
    start_time: time
    end_time: time
    lessons_reserved: int = 1
    online_link: Optional[str] = None
    notes: Optional[str] = None


class LessonSessionUpdate(BaseModel):
    status: Optional[str] = None
    session_date: Optional[date] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    online_link: Optional[str] = None
    notes: Optional[str] = None


class LessonSessionWithDetails(BaseModel):
    id: int
    lesson_id: int
    enrollment_id: Optional[int] = None
    student_id: int
    student_name: str
    tutor_id: int
    tutor_name: Optional[str] = None
    lesson_title: str
    lesson_subject: str
    session_date: date
    start_time: time
    end_time: time
    is_online: bool = False
    location: Optional[str] = None
    online_link: Optional[str] = None
    status: str = "scheduled"
    lessons_reserved: int = 1
    notes: Optional[str] = None
    remaining_lessons_after: Optional[int] = None
    created_at: datetime


class LessonReviewBase(BaseModel):
    lesson_id: int
    student_id: int
    rating: int
    comment: Optional[str] = None


class LessonReviewCreate(LessonReviewBase):
    pass


class LessonReviewInDB(LessonReviewBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class LessonReview(LessonReviewInDB):
    pass


class LessonWithDetails(Lesson):
    tutor_name: Optional[str] = None
    school_name: Optional[str] = None
    enrolled_count: int = 0
    average_rating: Optional[float] = None
    reviews_count: int = 0
