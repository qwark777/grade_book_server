# Управление достижениями

## Где хранятся достижения

Достижения хранятся в двух таблицах:

1. **`achievements`** - каталог всех достижений:
   - `id` - уникальный идентификатор
   - `code` - код достижения (например, "first_grade")
   - `title` - название (например, "Первая оценка")
   - `description` - описание (опционально)
   - `image_url` - URL изображения (например, "/static/achievements/first_grade.png")
   - `rarity` - редкость (опционально)

2. **`user_achievements`** - связь пользователей с полученными достижениями:
   - `user_id` - ID пользователя
   - `achievement_id` - ID достижения
   - `earned_at` - дата получения

## Способы добавления достижений

### 1. Через скрипт (рекомендуется)

#### Добавить стандартные достижения в каталог:
```bash
cd grade_book_server
python3 scripts/add_achievements.py seed
```

#### Добавить новое достижение в каталог:
```bash
python3 scripts/add_achievements.py add \
  --code "first_grade" \
  --title "Первая оценка" \
  --description "Получена первая оценка в системе" \
  --image-url "/static/achievements/first_grade.png"
```

#### Выдать достижение пользователю:
```bash
python3 scripts/add_achievements.py award \
  --user-id 1 \
  --code "first_grade"
```

### 2. Через API endpoints

#### Создать достижение:
```bash
POST /api/v1/achievements/create
{
  "code": "first_grade",
  "title": "Первая оценка",
  "description": "Получена первая оценка в системе",
  "image_url": "/static/achievements/first_grade.png"
}
```

#### Выдать достижение пользователю:
```bash
POST /api/v1/achievements/award-to-user
{
  "user_id": 1,
  "achievement_code": "first_grade"
}
```

### 3. Через существующий endpoint (из main.py)

#### Синхронизация из папки:
```bash
POST /api/v1/achievements/sync
```
Сканирует папку `/achievements` на сервере и добавляет файлы в каталог.

#### Выдача достижения (старый способ):
```bash
POST /api/v1/achievements/award
{
  "user_id": 1,
  "achievement_id": "first_grade"  # или ID числом
}
```

### 3. Загрузка фото достижения

#### Через API:
```bash
POST /api/v1/achievements/upload-photo
Content-Type: multipart/form-data

file: [файл изображения]
achievement_code: "first_grade"  # опционально, если не указан - используется имя файла
```

#### Пример с curl:
```bash
curl -X POST "http://localhost:8001/api/v1/achievements/upload-photo" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@/path/to/achievement.png" \
  -F "achievement_code=first_grade"
```

**Важно:**
- Фото сохраняются в папку `achievements/` на сервере
- Доступны по URL: `/static/achievements/{filename}`
- Поддерживаемые форматы: jpg, jpeg, png, webp, gif
- Если код достижения не указан, используется имя файла без расширения

## Примеры использования

### Добавить достижение с фото:

**Вариант 1: Сначала загрузить фото, потом создать достижение**
```bash
# 1. Загрузить фото
curl -X POST "http://localhost:8001/api/v1/achievements/upload-photo" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@first_grade.png" \
  -F "achievement_code=first_grade"

# 2. Создать достижение (фото автоматически найдется по коду)
curl -X POST "http://localhost:8001/api/v1/achievements/create" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "first_grade",
    "title": "Первая оценка",
    "description": "Получена первая оценка в системе"
  }'
```

**Вариант 2: Положить фото в папку achievements/, затем создать достижение**
```bash
# 1. Скопировать фото в папку achievements/
cp first_grade.png grade_book_server/achievements/

# 2. Создать достижение (фото автоматически найдется)
curl -X POST "http://localhost:8001/api/v1/achievements/create" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "first_grade",
    "title": "Первая оценка",
    "description": "Получена первая оценка в системе"
  }'
```

### Добавить несколько достижений:
```bash
# 1. Заполнить каталог стандартными достижениями
python3 scripts/add_achievements.py seed

# 2. Выдать достижения пользователю с ID 1
python3 scripts/add_achievements.py award --user-id 1 --code "first_grade"
python3 scripts/add_achievements.py award --user-id 1 --code "excellent_student"
```

### Через Python напрямую:
```python
import asyncio
from scripts.add_achievements import add_achievement_to_catalog, award_achievement_to_user

# Добавить достижение
asyncio.run(add_achievement_to_catalog(
    code="first_grade",
    title="Первая оценка",
    description="Получена первая оценка в системе",
    image_url="/static/achievements/first_grade.png"
))

# Выдать пользователю
asyncio.run(award_achievement_to_user(
    user_id=1,
    achievement_code="first_grade"
))
```
