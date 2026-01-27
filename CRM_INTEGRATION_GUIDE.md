# Руководство по интеграции CRM для школ

## Обзор

Система позволяет каждой школе настроить связь со своей CRM системой (1С, Битрикс24, AmoCRM и т.д.) и синхронизировать данные между приложением и CRM школы.

## Архитектура

```
┌─────────────────────┐
│  Flutter App        │
│  (Школа A)          │ ────┐
└─────────────────────┘     │
                            │
┌─────────────────────┐     │     ┌──────────────────┐
│  Flutter App        │ ────┼────>│   API Server     │
│  (Школа B)          │     │     │                  │
└─────────────────────┘     │     │  /crm-integrations│
                            │     └──────────────────┘
┌─────────────────────┐     │              │
│  Flutter App        │ ────┘              │
│  (Школа C)          │                    │
└─────────────────────┘                    ▼
                                ┌──────────────┐
                                │  MySQL DB   │
                                │ school_crm_│
                                │ integrations│
                                └────────────┘
                                        │
                                        ▼
                    ┌──────────────────────────────────┐
                    │  CRM Systems (School A, B, C)    │
                    │  - 1С                            │
                    │  - Битрикс24                     │
                    │  - AmoCRM                        │
                    └──────────────────────────────────┘
```

## Возможности

### Для каждой школы:
- ✅ Настройка своей CRM системы
- ✅ Хранение API ключей и токенов
- ✅ Настройка направления синхронизации (app → CRM, CRM → app, двусторонняя)
- ✅ Выбор частоты синхронизации (в реальном времени, ежечасно, ежедневно, вручную)
- ✅ Маппинг полей (соответствие полей между приложением и CRM)
- ✅ Тестирование подключения
- ✅ История синхронизаций
- ✅ Логи операций

## API Endpoints

### 1. Получить список интеграций школы

**GET** `/api/v1/crm-integrations/schools/{school_id}/integrations`

**Ответ:**
```json
[
  {
    "id": 1,
    "school_id": 5,
    "crm_type": "1c",
    "crm_name": "1С:Бухгалтерия школы",
    "api_url": "https://1c-school.example.com",
    "is_active": true,
    "sync_direction": "bidirectional",
    "sync_frequency": "daily",
    "last_sync_at": "2024-12-15T10:30:00",
    "next_sync_at": "2024-12-16T10:30:00",
    "created_at": "2024-01-15T09:00:00",
    "updated_at": "2024-12-15T10:30:00"
  }
]
```

### 2. Создать интеграцию

**POST** `/api/v1/crm-integrations/schools/{school_id}/integrations`

**Тело запроса:**
```json
{
  "school_id": 5,
  "crm_type": "1c",
  "crm_name": "1С:Бухгалтерия",
  "api_url": "https://1c-school.example.com/api/v1",
  "api_key": "your_api_key",
  "api_secret": "your_api_secret",
  "access_token": "bearer_token_here",
  "sync_direction": "bidirectional",
  "sync_frequency": "daily",
  "field_mapping": {
    "lesson.title": "Название",
    "lesson.price": "Цена",
    "student.full_name": "ФИО"
  },
  "notes": "Основная CRM школы для учета занятий"
}
```

### 3. Обновить интеграцию

**PUT** `/api/v1/crm-integrations/integrations/{integration_id}`

**Тело запроса:**
```json
{
  "is_active": true,
  "sync_frequency": "hourly",
  "api_key": "new_api_key"
}
```

### 4. Удалить интеграцию

**DELETE** `/api/v1/crm-integrations/integrations/{integration_id}`

### 5. Тестировать подключение

**POST** `/api/v1/crm-integrations/integrations/{integration_id}/test`

**Ответ:**
```json
{
  "status": "success",
  "message": "Connection test completed",
  "details": {
    "crm_type": "1c",
    "api_url": "https://1c-school.example.com"
  }
}
```

### 6. Запустить синхронизацию

**POST** `/api/v1/crm-integrations/integrations/{integration_id}/sync?sync_type=lessons`

**Параметры:**
- `sync_type`: `lessons`, `enrollments`, `financial`, `students`, `teachers`, `full`

## Типы CRM

### Поддерживаемые типы:
- **1c** - 1С:Бухгалтерия, 1С:Управление образовательной организацией
- **bitrix24** - Битрикс24 CRM
- **amocrm** - AmoCRM
- **custom** - Пользовательская интеграция (REST API)
- **other** - Другая CRM система

## Направления синхронизации

1. **app_to_crm** - Только из приложения в CRM (экспорт данных)
2. **crm_to_app** - Только из CRM в приложение (импорт данных)
3. **bidirectional** - Двусторонняя синхронизация

## Частота синхронизации

- **realtime** - В реальном времени (через webhooks)
- **hourly** - Каждый час
- **daily** - Ежедневно
- **weekly** - Еженедельно
- **manual** - Только по запросу

## Маппинг полей

Позволяет настроить соответствие полей между приложением и CRM:

```json
{
  "field_mapping": {
    "lesson.title": "НазваниеЗанятия",
    "lesson.subject": "Предмет",
    "lesson.price": "Цена",
    "student.full_name": "ФИО_Студента",
    "student.class": "Класс",
    "enrollment.date": "ДатаЗаписи",
    "enrollment.status": "Статус",
    "payment.amount": "Сумма",
    "payment.status": "СтатусОплаты"
  }
}
```

## Примеры использования

### Настройка интеграции с 1С

```bash
POST /api/v1/crm-integrations/schools/5/integrations
Content-Type: application/json
Authorization: Bearer <admin_token>

{
  "school_id": 5,
  "crm_type": "1c",
  "crm_name": "1С:Бухгалтерия школы №123",
  "api_url": "https://1c-school123.example.com/api/v1",
  "api_key": "school_api_key_12345",
  "sync_direction": "app_to_crm",
  "sync_frequency": "daily"
}
```

### Настройка интеграции с Битрикс24

```bash
POST /api/v1/crm-integrations/schools/5/integrations
{
  "school_id": 5,
  "crm_type": "bitrix24",
  "crm_name": "Битрикс24 CRM школы",
  "api_url": "https://school.bitrix24.ru/rest",
  "api_key": "bitrix_webhook_key",
  "sync_direction": "bidirectional",
  "sync_frequency": "realtime"
}
```

### Запуск синхронизации

```bash
POST /api/v1/crm-integrations/integrations/1/sync?sync_type=lessons
Authorization: Bearer <admin_token>
```

## Безопасность

- Все интеграции привязаны к конкретной школе
- API ключи и токены хранятся в зашифрованном виде
- Доступ к управлению интеграциями только у администраторов школы
- Журналирование всех операций синхронизации

## UI в админке

В приложении Flutter для каждой школы:
1. Раздел "Интеграции CRM" в админ-панели
2. Список настроенных интеграций
3. Форма добавления новой интеграции
4. Кнопка тестирования подключения
5. Кнопка ручной синхронизации
6. История синхронизаций

## Следующие шаги

1. Запустить миграцию: `python migrations/add_crm_integrations.py`
2. Добавить UI в Flutter приложение для настройки интеграций
3. Реализовать сервис синхронизации данных (background task)
4. Добавить вебхуки для получения данных из CRM
