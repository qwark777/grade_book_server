# AI Advice API Documentation

## Overview
API для получения персонализированных AI-советов на основе данных об успеваемости, посещаемости и трендах студента.

## Endpoints

### POST /api/v1/ai-advice/analyze
Анализирует переданные данные студента и возвращает AI-советы.

**Request Body:**
```json
{
  "student_id": 123,
  "grades": [
    {"subject": "Математика", "value": 4, "date": "2025-01-15"},
    {"subject": "Физика", "value": 5, "date": "2025-01-16"}
  ],
  "attendance_stats": {
    "present": 46,
    "late": 3,
    "absent": 1,
    "total_lessons": 50
  },
  "performance_trends": [
    {"date": "2025-01-01", "average_score": 3.2},
    {"date": "2025-01-08", "average_score": 3.5}
  ],
  "grade_distribution": {
    "grade_2": 3,
    "grade_3": 7,
    "grade_4": 18,
    "grade_5": 22,
    "total_grades": 50
  }
}
```

**Response:**
```json
[
  {
    "id": "advice_1",
    "title": "Улучшите Математику",
    "description": "Средний балл по предмету 3.2 — добавьте практику и повторение материала.",
    "priority": "high",
    "category": "grades",
    "icon": "📊",
    "date": "2025-01-20",
    "action_text": "Подробнее"
  }
]
```

### GET /api/v1/ai-advice/{student_id}
Получает AI-советы для студента, автоматически собирая данные из базы.

**Response:** Аналогичен POST /analyze

## Models

### AiAdviceResponse
- `id` (string): Уникальный идентификатор совета
- `title` (string): Заголовок совета
- `description` (string): Описание совета
- `priority` (string): Приоритет ("high", "medium", "low")
- `category` (string): Категория ("grades", "attendance", "behavior", "study_habits", "time_management")
- `icon` (string): Эмодзи-иконка
- `date` (string): Дата в формате YYYY-MM-DD
- `action_text` (string, optional): Текст действия, если есть

## Логика анализа

### Анализ оценок
- Определяет предметы с низким средним баллом (< 3.5)
- Генерирует советы для улучшения по этим предметам
- Добавляет похвалу за хорошие результаты (средний балл >= 4.0)

### Анализ посещаемости
- Вычисляет процент пропусков
- Генерирует предупреждения при проценте пропусков > 10%

### Анализ трендов
- Сравнивает последние оценки с предыдущими
- Определяет тренд (улучшение/ухудшение)
- Генерирует соответствующие советы

## Authentication
Все endpoints требуют JWT токен в заголовке Authorization:
```
Authorization: Bearer <token>
```

## Установка нейронной сети (TODO)

В будущем планируется интеграция ML модели для более точных предсказаний и рекомендаций.

Для установки:
1. Добавить зависимости в `requirements.txt`:
   ```
   tensorflow>=2.10.0
   scikit-learn>=1.0.0
   pandas>=1.5.0
   numpy>=1.23.0
   ```

2. Обучить модель на исторических данных
3. Интегрировать модель в endpoint `/analyze`

