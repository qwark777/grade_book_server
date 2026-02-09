from typing import Optional, List
from pydantic import BaseModel


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
    id: Optional[int] = None
    name: str
    coefficient: float = 1.0


class ClassItem(BaseModel):
    id: int
    name: str
    student_count: int
    is_archived: bool = False


class StudentResponse(BaseModel):
    id: int
    full_name: str
    work_place: Optional[str] = None
    location: Optional[str] = None
    bio: Optional[str] = None
    photo_url: Optional[str] = None


class StudentWithGradesResponse(StudentResponse):
    grades: List[dict] = []
    average_score: Optional[float] = None


class AttendanceStats(BaseModel):
    present: int
    late: int
    absent: int
    total_lessons: int


class AttendanceHeatmapData(BaseModel):
    week: int
    day: int
    status: int  # 0 - нет урока, 1 - присутствовал, 2 - опоздал, 3 - отсутствовал


class AttendanceDetail(BaseModel):
    lesson_number: int
    subject_name: str
    status: str  # 'present', 'late', 'absent'
    teacher_name: str = None


class GradeDistribution(BaseModel):
    grade_2: int
    grade_3: int
    grade_4: int
    grade_5: int
    total_grades: int


class PerformanceTrend(BaseModel):
    date: str
    average_score: float


class SubjectPerformance(BaseModel):
    subject: str
    average_score: float
    grade_distribution: GradeDistribution
    trends: List[PerformanceTrend]


