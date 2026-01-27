# Интеграция с 1С для дополнительных занятий

## Обзор

Система предоставляет API endpoints для экспорта данных о дополнительных занятиях в форматах, совместимых с 1С. Это позволяет синхронизировать данные о занятиях, записях студентов и финансовых операциях с учетной системой 1С.

## Доступные endpoints

### 1. Экспорт занятий в JSON формате

**GET** `/api/v1/lessons/export/1c/json`

**Параметры запроса:**
- `date_from` (опционально) - Дата начала периода (YYYY-MM-DD)
- `date_to` (опционально) - Дата окончания периода (YYYY-MM-DD)
- `school_id` (опционально) - ID школы для фильтрации

**Пример запроса:**
```
GET /api/v1/lessons/export/1c/json?date_from=2024-01-01&date_to=2024-12-31
Authorization: Bearer <token>
```

**Ответ:**
```json
{
  "export_date": "2024-12-15T10:30:00",
  "total_lessons": 25,
  "lessons": [
    {
      "id": 1,
      "title": "Математика для 9 класса",
      "subject": "Математика",
      "tutor_name": "Иванов Иван Иванович",
      "tutor_id": 123,
      "price": 1500.00,
      "duration_minutes": 60,
      "is_online": false,
      "location": "ул. Ленина, 10",
      "max_students": 15,
      "enrolled_count": 12,
      "created_at": "2024-01-15T09:00:00",
      "updated_at": "2024-12-10T14:30:00"
    }
  ]
}
```

### 2. Экспорт занятий в XML формате

**GET** `/api/v1/lessons/export/1c/xml`

**Параметры:** аналогично JSON экспорту

**Пример запроса:**
```
GET /api/v1/lessons/export/1c/xml?date_from=2024-01-01
Authorization: Bearer <token>
```

**Ответ:** XML документ с структурой:
```xml
<lessons_export export_date="2024-12-15T10:30:00" total_lessons="25">
  <lesson>
    <id>1</id>
    <title>Математика для 9 класса</title>
    <subject>Математика</subject>
    <tutor_name>Иванов Иван Иванович</tutor_name>
    <tutor_id>123</tutor_id>
    <price>1500.00</price>
    <duration_minutes>60</duration_minutes>
    <is_online>false</is_online>
    <location>ул. Ленина, 10</location>
    <max_students>15</max_students>
    <enrolled_count>12</enrolled_count>
    <created_at>2024-01-15T09:00:00</created_at>
    <updated_at>2024-12-10T14:30:00</updated_at>
  </lesson>
</lessons_export>
```

### 3. Экспорт записей на занятия

**GET** `/api/v1/lessons/enrollments/export/1c/json`

**Параметры запроса:**
- `date_from` (опционально) - Дата начала периода (YYYY-MM-DD)
- `date_to` (опционально) - Дата окончания периода (YYYY-MM-DD)
- `lesson_id` (опционально) - ID занятия для фильтрации
- `school_id` (опционально) - ID школы для фильтрации

**Ответ:**
```json
{
  "export_date": "2024-12-15T10:30:00",
  "total_enrollments": 150,
  "enrollments": [
    {
      "enrollment_id": 1,
      "lesson_id": 1,
      "lesson_title": "Математика для 9 класса",
      "student_id": 456,
      "student_name": "Петров Петр Петрович",
      "enrollment_date": "2024-01-20T10:00:00",
      "status": "enrolled",
      "price": 1500.00,
      "payment_status": "paid"
    }
  ]
}
```

### 4. Экспорт финансовых данных

**GET** `/api/v1/lessons/financial/export/1c/json`

**Параметры запроса:**
- `date_from` (опционально) - Дата начала периода (YYYY-MM-DD)
- `date_to` (опционально) - Дата окончания периода (YYYY-MM-DD)
- `school_id` (опционально) - ID школы для фильтрации

**Ответ:**
```json
{
  "export_date": "2024-12-15T10:30:00",
  "period": {
    "from": "2024-01-01",
    "to": "2024-12-31"
  },
  "summary": {
    "total_lessons": 25,
    "total_enrollments": 150,
    "total_revenue": 225000.00,
    "paid_revenue": 200000.00,
    "pending_revenue": 25000.00
  }
}
```

## Использование в 1С

### Вариант 1: HTTP-соединение

1С может периодически обращаться к API endpoints через HTTP-запросы и получать данные в JSON/XML формате.

**Пример кода 1С для получения данных:**
```
HTTPСоединение = Новый HTTPСоединение("api.example.com", 443, , , , 30);
Запрос = Новый HTTPЗапрос("/api/v1/lessons/export/1c/json?date_from=2024-01-01");
Запрос.Заголовки.Вставить("Authorization", "Bearer <token>");
Ответ = HTTPСоединение.Получить(Запрос);
Данные = ПрочитатьJSON(Ответ.ПолучитьТелоКакСтроку());
```

### Вариант 2: Файловый обмен

1. Экспортируйте данные через API в JSON/XML файл
2. Сохраните файл на общий сетевой диск
3. Настройте 1С на периодическое чтение файлов из этой папки

### Вариант 3: Web-сервис

Настройте внешний обработчик 1С, который будет вызывать API endpoints и импортировать данные в регистры и справочники 1С.

## Структура данных

### Занятие (Lesson)
- `id` - Уникальный идентификатор занятия
- `title` - Название занятия
- `subject` - Предмет
- `tutor_name` - ФИО преподавателя
- `tutor_id` - ID преподавателя
- `price` - Цена занятия (может быть NULL)
- `duration_minutes` - Длительность в минутах
- `is_online` - Онлайн или оффлайн
- `location` - Адрес проведения (для оффлайн)
- `max_students` - Максимальное количество студентов
- `enrolled_count` - Количество записавшихся
- `created_at` - Дата создания
- `updated_at` - Дата обновления

### Запись на занятие (Enrollment)
- `enrollment_id` - ID записи
- `lesson_id` - ID занятия
- `lesson_title` - Название занятия
- `student_id` - ID студента
- `student_name` - ФИО студента
- `enrollment_date` - Дата записи
- `status` - Статус: "enrolled", "completed", "cancelled"
- `price` - Цена занятия
- `payment_status` - Статус оплаты: "pending", "paid", "refunded"

## Безопасность

- Все endpoints требуют аутентификации (Bearer token)
- Доступ имеют только администраторы (`admin`, `owner`, `superadmin`)
- Рекомендуется использовать HTTPS для передачи данных

## Примеры использования

### Получение всех занятий за месяц
```
GET /api/v1/lessons/export/1c/json?date_from=2024-12-01&date_to=2024-12-31
```

### Получение записей на конкретное занятие
```
GET /api/v1/lessons/enrollments/export/1c/json?lesson_id=123
```

### Получение финансовой статистики за период
```
GET /api/v1/lessons/financial/export/1c/json?date_from=2024-01-01&date_to=2024-12-31
```
