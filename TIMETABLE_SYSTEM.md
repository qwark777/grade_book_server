# Система расписания

## Обзор

Полноценная система управления расписанием с поддержкой:
- Основного расписания (шаблоны)
- Замен и изменений
- Праздников и выходных
- Учебных недель (A/B)
- Кабинетов и комнат

## Структура базы данных

### Основные таблицы

1. **academic_periods** - Учебные периоды (семестры, четверти)
2. **timetable_templates** - Шаблоны основного расписания
3. **timetable_changes** - Замены и изменения
4. **holidays** - Праздники и выходные
5. **academic_weeks** - Календарь учебных недель
6. **rooms** - Кабинеты и комнаты

### Связи

- `timetable_templates` связана с `classes`, `subjects`, `users` (учителя), `rooms`, `academic_periods`
- `timetable_changes` связана с `classes`, `users` (учителя), `subjects`, `rooms`
- `academic_weeks` связана с `academic_periods`

## API Эндпоинты

### Расписание

- `GET /api/v1/timetable/week` - Расписание на конкретную неделю
- `GET /api/v1/timetable/current-week` - Расписание на текущую неделю
- `GET /api/v1/timetable/next-week` - Расписание на следующую неделю
- `GET /api/v1/timetable/previous-week` - Расписание на предыдущую неделю
- `GET /api/v1/timetable/week-info` - Информация о неделе

### Управление

- `GET /api/v1/rooms` - Список кабинетов
- `POST /api/v1/timetable/changes` - Создать замену
- `POST /api/v1/holidays` - Создать праздник

## Модели данных

### WeekSchedule
```json
{
  "week_start_date": "2024-01-15",
  "week_end_date": "2024-01-21",
  "week_number": 1,
  "week_type": "A",
  "days": [...]
}
```

### DaySchedule
```json
{
  "date": "2024-01-15",
  "day_of_week": "Понедельник",
  "is_holiday": false,
  "holiday_name": null,
  "holiday_reason": null,
  "lessons": [...]
}
```

### LessonItem
```json
{
  "lesson_number": 1,
  "start_time": "08:00",
  "end_time": "08:45",
  "subject": "Математика",
  "teacher": "Иванова И.И.",
  "room": "301",
  "change_type": "replace",
  "change_reason": "Замена по болезни"
}
```

## Логика работы

### 1. Получение расписания

1. Определяется тип недели (A/B) по дате
2. Загружаются шаблоны расписания для класса и типа недели
3. Применяются замены для конкретных дат
4. Проверяются праздники
5. Формируется итоговое расписание

### 2. Типы изменений

- **replace** - Замена учителя/предмета/кабинета
- **cancel** - Отмена урока
- **move** - Перенос урока
- **add** - Добавление урока

### 3. Праздники

- **holiday** - Государственный праздник
- **weekend** - Выходной день
- **vacation** - Каникулы

## Заполнение тестовыми данными

```bash
cd /Users/qwark/Documents/GitHub/grade_book_server
python3 app/db/seed_data.py
```

Это создаст:
- 5 кабинетов
- 10 предметов
- Класс 10А
- Расписание на 5 дней
- Несколько праздников
- 10 учебных недель
- Примеры замен

## Android интеграция

### Новые модели
- `WeekSchedule` - Расписание недели
- `DaySchedule` - Расписание дня
- `LessonItem` - Урок
- `Room` - Кабинет

### API методы
- `getTimetableWeek()` - Расписание на неделю
- `getCurrentWeekTimetable()` - Текущая неделя
- `getNextWeekTimetable()` - Следующая неделя
- `getPreviousWeekTimetable()` - Предыдущая неделя
- `getRooms()` - Список кабинетов

### TimetableFragment
Обновлен для работы с реальными данными из API с fallback на mock данные.

## Преимущества системы

- ✅ **Гибкость** - Поддержка различных типов изменений
- ✅ **Масштабируемость** - Легко добавлять новые функции
- ✅ **Надежность** - Fallback на mock данные при недоступности API
- ✅ **Полнота** - Учет всех аспектов учебного процесса
- ✅ **Производительность** - Оптимизированные запросы с индексами

