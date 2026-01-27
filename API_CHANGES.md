# Изменения в API после рефакторинга сервера

## Обновления в Android приложении

### 1. Базовый URL
- **Было**: `http://10.0.2.2:8001/`
- **Стало**: `http://10.0.2.2:8001/api/v1/`

### 2. Доступные эндпоинты

#### Аутентификация
- `POST /api/v1/register` - Регистрация пользователя
- `POST /api/v1/token` - Получение токена
- `GET /api/v1/protected` - Защищенный эндпоинт
- `GET /api/v1/verify-token` - Проверка токена

#### Профили
- `GET /api/v1/profile/photo` - Получение фото профиля
- `GET /api/v1/profile/info` - Информация профиля
- `POST /api/v1/profile/full-update` - Обновление профиля

#### Оценки и классы
- `GET /api/v1/classes` - Список классов
- `GET /api/v1/student-scores-full` - Все оценки студентов
- `GET /api/v1/subject-scores/{subject_name}` - Оценки по предмету

#### Пользователи
- `GET /api/v1/users/all` - Список пользователей
- `GET /api/v1/users/{user_id}` - Информация о пользователе

#### Сообщения
- `POST /api/v1/messages/send` - Отправка сообщения
- `GET /api/v1/messages/{user_id}` - Получение сообщений
- `GET /api/v1/conversations` - Список бесед

### 3. Удаленные эндпоинты
Следующие эндпоинты больше не поддерживаются в новом API:
- `GET /grades/{studentId}`
- `POST /grades`
- `GET /homeworks/{classId}`
- `POST /homeworks`
- `GET /students/{classId}`
- `GET /subjects`

### 4. Обновленные модели данных

#### Class
- **Было**: `data class Class(val id: Int, val name: String)`
- **Стало**: `data class Class(val name: String, val student_count: Int)`

### 5. WebSocket
- URL остается прежним: `ws://10.0.2.2:8001/ws?token={jwt_token}`

## Запуск обновленного сервера

```bash
# Установить зависимости
pip install -r requirements.txt

# Запустить новый сервер
python main_new.py
```

## Тестирование

1. Убедитесь, что новый сервер запущен на порту 8001
2. Запустите Android приложение
3. Проверьте основные функции:
   - Регистрация/логин
   - Просмотр профиля
   - Просмотр оценок
   - Отправка сообщений


