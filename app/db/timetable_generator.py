"""
Автогенерация расписания по учебному плану (curriculum).
Размещает уроки в сетке Пн–Пт, учитывая конфликты учителей.
"""
from typing import List, Dict, Set, Tuple, Optional
from dataclasses import dataclass

# Стандартное время уроков (урок -> (start, end))
LESSON_TIMES = {
    1: ("08:00:00", "08:45:00"),
    2: ("08:55:00", "09:40:00"),
    3: ("10:00:00", "10:45:00"),
    4: ("10:55:00", "11:40:00"),
    5: ("12:00:00", "12:45:00"),
    6: ("12:55:00", "13:40:00"),
    7: ("13:50:00", "14:35:00"),
}

DAYS = [1, 2, 3, 4, 5]  # Пн-Пт
MAX_LESSONS_PER_DAY = 7


@dataclass
class CurriculumItem:
    class_id: int
    subject_id: int
    teacher_id: int
    hours_per_week: int


@dataclass
class PlacedLesson:
    class_id: int
    subject_id: int
    teacher_id: int
    day_of_week: int
    lesson_number: int
    start_time: str
    end_time: str
    room_id: Optional[int] = None


def generate_timetable(
    items: List[CurriculumItem],
    room_id: Optional[int] = None,
) -> List[PlacedLesson]:
    """
    Размещает уроки в сетке. Учитель не может быть в двух местах одновременно.
    Предметы с большим числом часов размещаются первыми.
    """
    result: List[PlacedLesson] = []
    teacher_busy: Set[Tuple[int, int, int]] = set()  # (teacher_id, day, lesson)
    class_busy: Dict[Tuple[int, int, int], bool] = {}  # (class_id, day, lesson)

    # Сортируем по убыванию часов (сначала сложные)
    sorted_items = sorted(items, key=lambda x: -x.hours_per_week)

    for item in sorted_items:
        placed = 0
        hours_left = item.hours_per_week

        # Пробуем разместить все часы
        while hours_left > 0:
            best_slot = None
            for day in DAYS:
                for ln in range(1, MAX_LESSONS_PER_DAY + 1):
                    if (item.teacher_id, day, ln) in teacher_busy:
                        continue
                    if class_busy.get((item.class_id, day, ln)):
                        continue
                    best_slot = (day, ln)
                    break
                if best_slot:
                    break

            if not best_slot:
                break

            day, ln = best_slot
            start, end = LESSON_TIMES.get(ln, ("08:00:00", "08:45:00"))
            result.append(PlacedLesson(
                class_id=item.class_id,
                subject_id=item.subject_id,
                teacher_id=item.teacher_id,
                day_of_week=day,
                lesson_number=ln,
                start_time=start,
                end_time=end,
                room_id=room_id,
            ))
            teacher_busy.add((item.teacher_id, day, ln))
            class_busy[(item.class_id, day, ln)] = True
            hours_left -= 1
            placed += 1

    return result
