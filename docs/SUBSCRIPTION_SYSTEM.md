# Система подписок

## Обзор

Система подписок реализует многоуровневую модель доступа к функциям приложения с поддержкой:
- Планов подписок (Free, Pro, Enterprise, Student Plus, Teacher Pro)
- Системы прав доступа (entitlements)
- Отслеживания использования (usage tracking)
- Биллинга и инвойсов

## Структура базы данных

### Основные таблицы

1. **subscription_plans** - Планы подписок
   - id, name, description
   - price_monthly, price_yearly, currency
   - is_active, created_at

2. **plan_entitlements** - Права доступа для каждого плана
   - plan_id, entitlement_key, entitlement_value
   - Уникальный ключ: (plan_id, entitlement_key)

3. **school_subscriptions** - Подписки школ
   - school_id, plan_id, status
   - current_period_start, current_period_end
   - seats_students, seats_teachers

4. **user_subscriptions** - Индивидуальные подписки (Student Plus, Teacher Pro)
   - user_id, plan_id, status
   - current_period_start, current_period_end

5. **usage_events** - Отслеживание использования
   - school_id, user_id, event_key, event_value
   - event_date, created_at

6. **subscription_invoices** - Инвойсы
   - school_id, plan_id, amount, currency
   - status, period_start, period_end, due_date, paid_at

## Планы подписок

### Free (Базовый)
- До 10 классов, 100 учеников, 5 учителей
- 200 AI запросов/месяц
- 2 CSV экспорта/месяц
- Базовая аналитика
- 1GB хранилища
- 1000 сообщений/день

### Pro (Рекомендуемый)
- Безлимитные классы
- До 500 учеников, 25 учителей
- 10,000 AI запросов/месяц
- Безлимитные CSV экспорты
- Полная аналитика
- 100GB хранилища
- 10,000 сообщений/день
- Расширенные роли и права
- Отслеживание посещаемости
- Изменения расписания

### Enterprise (Для сетей школ)
- Все функции Pro
- Безлимитные пользователи
- Безлимитные AI запросы
- SSO/SCIM интеграция
- Аудиторские логи
- Webhooks
- API доступ
- Мультишкола
- Дашборд владельца

### Student Plus (Дополнительно для учеников)
- Персональные AI подсказки
- Отслеживание целей
- Родительские отчеты
- Аналитика обучения
- Дополнительные достижения

### Teacher Pro (Дополнительно для учителей)
- Отчеты по предметам
- Расширенные экспорты
- Шаблоны оценивания
- Аналитика по предмету
- Массовые операции

## Система прав доступа (Entitlements)

### Ключи прав доступа

#### Аналитика
- `analytics.basic` - Базовая аналитика (boolean)
- `analytics.full` - Полная аналитика (boolean)

#### AI функции
- `ai.quota` - Лимит AI запросов (number)
- `ai.personal` - Персональные AI подсказки (boolean)

#### Экспорты
- `exports.csv` - CSV экспорты (number или "unlimited")

#### Пользователи и классы
- `classes.max` - Максимум классов (number)
- `classes.unlimited` - Безлимитные классы (boolean)
- `students.max` - Максимум учеников (number)
- `students.unlimited` - Безлимитные ученики (boolean)
- `teachers.max` - Максимум учителей (number)
- `teachers.unlimited` - Безлимитные учителя (boolean)

#### Роли и права
- `roles.basic` - Базовые роли (boolean)
- `roles.advanced` - Расширенные роли (boolean)

#### Хранилище и сообщения
- `storage.mb` - Хранилище в MB (number)
- `storage.unlimited` - Безлимитное хранилище (boolean)
- `messages.daily` - Сообщения в день (number)
- `messages.unlimited` - Безлимитные сообщения (boolean)

#### Дополнительные функции
- `timetable.changes` - Изменения расписания (boolean)
- `attendance.tracking` - Отслеживание посещаемости (boolean)
- `reports.advanced` - Расширенные отчеты (boolean)
- `sso.enabled` - SSO интеграция (boolean)
- `scim.enabled` - SCIM интеграция (boolean)
- `audit.logs` - Аудиторские логи (boolean)
- `webhooks` - Webhooks (boolean)
- `api.access` - API доступ (boolean)
- `multi.school` - Мультишкола (boolean)
- `owner.dashboard` - Дашборд владельца (boolean)

## API Endpoints

### Подписки
- `GET /subscriptions/plans` - Список планов
- `GET /subscriptions/plans/{plan_id}` - Детали плана
- `GET /subscriptions/plans/{plan_id}/entitlements` - Права плана
- `GET /subscriptions/schools/{school_id}/subscription` - Подписка школы
- `POST /subscriptions/schools/{school_id}/subscription` - Создать подписку
- `GET /subscriptions/schools/{school_id}/usage` - Статистика использования
- `POST /subscriptions/schools/{school_id}/usage` - Записать использование
- `GET /subscriptions/schools/{school_id}/entitlements/check` - Проверить право
- `GET /subscriptions/schools/{school_id}/invoices` - Инвойсы школы

### Права доступа
- `GET /entitlements/check/{entitlement_key}` - Проверить одно право
- `GET /entitlements/check-multiple` - Проверить несколько прав
- `GET /entitlements/features` - Все доступные функции
- `GET /entitlements/limits` - Лимиты и использование

## Использование в коде

### Проверка прав доступа

```python
from app.core.entitlements import check_entitlement, get_school_id_for_user

# Проверить право доступа
school_id = await get_school_id_for_user(current_user)
entitlement_check = await check_entitlement(school_id, "analytics.full", current_user)

if not entitlement_check.has_access:
    raise HTTPException(status_code=402, detail="Analytics requires Pro plan")
```

### Запись использования

```python
from app.core.entitlements import record_usage_event

# Записать использование AI
await record_usage_event(school_id, "ai.quota", 1.0, user_id)
```

### Middleware для защиты endpoints

```python
from app.core.entitlements import require_entitlement

@router.get("/analytics/advanced")
@require_entitlement("analytics.full")
async def get_advanced_analytics():
    # Этот endpoint автоматически проверяет права доступа
    pass
```

## Установка и настройка

### 1. Создание схемы БД
```bash
# Схема создается автоматически при запуске приложения
python -m app.main
```

### 2. Заполнение seed данных
```bash
python seed_subscriptions.py
```

### 3. Проверка работы
```bash
# Проверить планы
curl http://localhost:8001/api/v1/subscriptions/plans

# Проверить права школы
curl http://localhost:8001/api/v1/entitlements/features?school_id=1
```

## Мониторинг и биллинг

### Отслеживание использования
- Все события использования записываются в `usage_events`
- Автоматический расчет лимитов по периодам подписки
- API для получения статистики использования

### Биллинг
- Автоматическое создание инвойсов
- Поддержка различных статусов платежей
- Интеграция с платежными системами (Stripe, ЮKassa)

### Уведомления
- Grace period для просроченных платежей
- Автоматическое отключение функций при превышении лимитов
- Уведомления о необходимости апгрейда

## Безопасность

- Все проверки прав доступа выполняются на сервере
- Клиентские ограничения только для UX
- Аудиторские логи для Enterprise планов
- Защита от превышения лимитов

## Расширение системы

### Добавление нового плана
1. Добавить план в `seed_subscriptions.py`
2. Определить права доступа в `plan_entitlements`
3. Обновить документацию

### Добавление нового права доступа
1. Добавить ключ в `plan_entitlements`
2. Обновить `check_entitlement` если нужно
3. Добавить проверки в соответствующие endpoints
4. Обновить документацию

### Добавление нового типа использования
1. Добавить ключ события в `usage_events`
2. Добавить запись использования в соответствующие места
3. Обновить лимиты в планах подписок
