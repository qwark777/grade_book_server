# Grade Book Server

Серверная часть системы электронного журнала с мобильным приложением.

## Структура проекта

```
grade_book_server/
├── app/
│   ├── api/                    # API роуты
│   │   ├── auth.py            # Аутентификация
│   │   ├── profile.py         # Профили пользователей
│   │   ├── grades.py          # Оценки и классы
│   │   ├── users.py           # Управление пользователями
│   │   ├── messages.py        # Сообщения
│   │   └── main.py            # Главный роутер API
│   ├── core/                  # Основные компоненты
│   │   ├── config.py          # Конфигурация
│   │   └── security.py        # Безопасность и JWT
│   ├── db/                    # База данных
│   │   ├── connection.py      # Подключение к БД
│   │   └── user_operations.py # Операции с пользователями
│   ├── models/                # Модели данных
│   │   ├── user.py           # Модели пользователей
│   │   ├── grade.py          # Модели оценок
│   │   └── message.py        # Модели сообщений
│   ├── websocket/             # WebSocket
│   │   ├── manager.py        # Менеджер подключений
│   │   └── endpoints.py      # WebSocket эндпоинты
│   └── main.py               # Главное приложение
├── main_new.py               # Новая точка входа
├── requirements.txt          # Зависимости
└── README.md                # Документация
```

## Установка и запуск

### 🐳 Вариант 1: Docker (Рекомендуется для быстрого развертывания)

**Развертывание одной командой:**

```bash
# 1. Настройте переменные окружения
cp .env.example .env
# Отредактируйте .env и измените пароли и ключи!

# 2. Запустите сервер
docker-compose up -d

# 3. Создайте первого владельца
docker-compose exec app python scripts/create_owner.py \
  --username owner --password YourSecurePassword123
```

Подробная инструкция: [DOCKER_SETUP.md](DOCKER_SETUP.md)

### 📦 Вариант 2: Локальная установка

1. Установите зависимости:
```bash
pip install -r requirements.txt
```

2. Настройте базу данных MySQL:
   - Создайте базу данных `grade_book`
   - Обновите настройки в `app/core/config.py`

3. **Создайте первого владельца (owner):**
   При первом запуске необходимо создать учетную запись владельца:
   ```bash
   python scripts/create_owner.py --username owner --password your_secure_password
   ```
   
   Или с указанием полного имени:
   ```bash
   python scripts/create_owner.py --username owner --password your_secure_password --full-name "Имя Фамилия"
   ```
   
   ⚠️ **ВАЖНО:** Сохраните учетные данные в безопасном месте! Владелец имеет полный доступ ко всем функциям системы.

4. Запустите сервер:
```bash
python main_new.py
```

Или используйте uvicorn напрямую:
```bash
uvicorn app.main:app --host localhost --port 8001 --reload
```

5. Войдите в приложение используя созданные учетные данные владельца.

📖 Подробная инструкция по первому запуску: [FIRST_START.md](FIRST_START.md)

## API Endpoints

### Аутентификация
- `POST /api/v1/register` - Регистрация пользователя
- `POST /api/v1/token` - Получение токена
- `GET /api/v1/verify-token` - Проверка токена

### Профили
- `GET /api/v1/profile/info` - Получение информации профиля
- `POST /api/v1/profile/full-update` - Обновление профиля
- `POST /api/v1/profile/photo` - Загрузка фото
- `GET /api/v1/profile_photo/{user_id}` - Получение фото

### Пользователи
- `GET /api/v1/users/all` - Список всех пользователей
- `GET /api/v1/users/{user_id}` - Информация о пользователе

### Оценки и классы
- `GET /api/v1/classes` - Список классов
- `GET /api/v1/subject-scores/{subject_name}` - Оценки по предмету
- `GET /api/v1/student-scores-full` - Все оценки студентов

### Сообщения
- `POST /api/v1/messages/send` - Отправка сообщения
- `GET /api/v1/messages/{user_id}` - Получение сообщений
- `GET /api/v1/conversations` - Список бесед

### WebSocket
- `WS /ws?token={jwt_token}` - WebSocket подключение для чата

## Особенности

- JWT аутентификация
- Шифрование сообщений
- WebSocket для реального времени
- Поддержка ролей (студент/учитель)
- Загрузка и хранение фотографий профилей
- MySQL база данных


