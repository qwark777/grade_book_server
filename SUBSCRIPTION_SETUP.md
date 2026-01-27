# Быстрый старт системы подписок

## 1. Установка схемы БД

Схема создается автоматически при запуске приложения:

```bash
cd grade_book_server
python -m app.main
```

## 2. Заполнение тестовых данных

```bash
python seed_subscriptions.py
```

Это создаст:
- 5 планов подписок (Free, Pro, Enterprise, Student Plus, Teacher Pro)
- Права доступа для каждого плана
- Trial подписки для всех существующих школ

## 3. Проверка работы

```bash
python test_subscriptions.py
```

## 4. Тестирование API

### Получить планы подписок
```bash
curl http://localhost:8001/api/v1/subscriptions/plans
```

### Проверить права школы
```bash
curl http://localhost:8001/api/v1/entitlements/features?school_id=1
```

### Проверить лимиты
```bash
curl http://localhost:8001/api/v1/entitlements/limits?school_id=1
```

## 5. Интеграция в приложение

### Проверка аналитики
Аналитические endpoints теперь автоматически проверяют права доступа:
- `/grades/attendance-stats/{student_id}`
- `/grades/attendance-heatmap/{student_id}`
- `/grades/grade-distribution/{student_id}`
- `/grades/performance-trends/{student_id}`
- `/grades/subject-performance/{student_id}/{subject_id}`

### Ошибки доступа
При отсутствии прав возвращается HTTP 402 (Payment Required) с деталями:
```json
{
  "detail": {
    "error": "Entitlement required",
    "entitlement": "analytics.full",
    "reason": "Entitlement not included in plan",
    "current_usage": null,
    "limit": null
  }
}
```

## 6. Управление подписками

### Создать подписку для школы
```bash
curl -X POST http://localhost:8001/api/v1/subscriptions/schools/1/subscription \
  -H "Content-Type: application/json" \
  -d '{"plan_id": 2}' \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Записать использование
```bash
curl -X POST http://localhost:8001/api/v1/subscriptions/schools/1/usage \
  -H "Content-Type: application/json" \
  -d '{"event_key": "ai.quota", "event_value": 1}' \
  -H "Authorization: Bearer YOUR_TOKEN"
```

## 7. Мониторинг

### Статистика использования
```bash
curl http://localhost:8001/api/v1/subscriptions/schools/1/usage
```

### Инвойсы школы
```bash
curl http://localhost:8001/api/v1/subscriptions/schools/1/invoices
```

## Планы подписок

| План | Цена/мес | Ученики | AI запросы | Аналитика | CSV экспорт |
|------|----------|---------|------------|-----------|-------------|
| Free | $0 | 100 | 200 | Базовая | 2/мес |
| Pro | $50 | 500 | 10,000 | Полная | Безлимит |
| Enterprise | $200 | Безлимит | Безлимит | Полная | Безлимит |

## Следующие шаги

1. **Интеграция с платежной системой** (Stripe, ЮKassa)
2. **Автоматическое создание инвойсов**
3. **Webhooks для обновления статуса подписок**
4. **Уведомления о превышении лимитов**
5. **Дашборд для управления подписками**
