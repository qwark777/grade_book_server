from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class AiAdviceFeedbackCreate(BaseModel):
    advice_id: str
    student_id: int
    is_useful: bool
    comment: Optional[str] = None

class AiAdviceFeedback(BaseModel):
    id: int
    advice_id: str
    student_id: int
    is_useful: bool
    comment: Optional[str]
    created_at: datetime
