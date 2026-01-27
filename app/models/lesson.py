from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


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
