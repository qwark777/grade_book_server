# Исправление проблемы подключения к базе данных

## Проблема

При запуске контейнера приложение пытается подключиться к `localhost:3306` вместо `db:3306`, что вызывает ошибку:
```
Can't connect to MySQL server on 'localhost'
```

## Причина

В файле `app/core/config.py` значение по умолчанию для `MYSQL_HOST` было установлено как `"localhost"` вместо `"db"`.

## Решение

1. **Исправлен `config.py`**: Значение по умолчанию изменено на `"db"` для Docker окружения.

2. **Увеличена задержка**: Время ожидания перед инициализацией БД увеличено до 15 секунд.

## Что делать на сервере

### 1. Убедись, что в `.env` файле НЕТ `MYSQL_HOST=localhost`

```bash
# Проверь .env файл
cat .env | grep MYSQL_HOST

# Если там MYSQL_HOST=localhost, удали эту строку или замени на:
# MYSQL_HOST=db
```

### 2. Пересобери и перезапусти контейнеры

```bash
cd ~/grade_book_server

# Останови контейнеры
docker-compose down

# Пересобери образ (чтобы применить изменения в config.py)
docker-compose build

# Запусти заново
docker-compose up -d

# Проверь логи
docker-compose logs -f app
```

### 3. Если проблема сохраняется

Проверь, что переменные окружения правильно передаются:

```bash
# Войди в контейнер
docker-compose exec app bash

# Проверь переменные окружения
env | grep MYSQL

# Должно быть:
# MYSQL_HOST=db
# MYSQL_USER=gradebook_user
# MYSQL_PASSWORD=...
# MYSQL_DB=grade_book
# MYSQL_PORT=3306
```

### 4. Проверь сеть Docker

```bash
# Убедись, что оба контейнера в одной сети
docker network inspect grade_book_server_gradebook_network

# Должны быть оба контейнера: gradebook_server и gradebook_mysql
```

### 5. Проверь доступность БД из контейнера приложения

```bash
# Войди в контейнер приложения
docker-compose exec app bash

# Попробуй подключиться к БД
python -c "
import asyncio
from app.db.connection import get_db_connection

async def test():
    conn = await get_db_connection()
    print('✅ Подключение успешно!')
    conn.close()

asyncio.run(test())
"
```

## Для локальной разработки

Если запускаешь приложение локально (не в Docker), установи в `.env`:
```env
MYSQL_HOST=localhost
```

Или экспортируй переменную:
```bash
export MYSQL_HOST=localhost
```
