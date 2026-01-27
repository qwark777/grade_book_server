# Развертывание через Docker

Это руководство поможет вам развернуть сервер одной командой с помощью Docker и Docker Compose.

## Требования

- Docker (версия 20.10 или выше)
- Docker Compose (версия 2.0 или выше)

Проверьте установку:
```bash
docker --version
docker-compose --version
```

## Быстрый старт

### 1. Клонируйте репозиторий и перейдите в директорию сервера

```bash
cd grade_book_server
```

### 2. Настройте переменные окружения

Скопируйте пример файла окружения:
```bash
cp .env.example .env
```

Отредактируйте `.env` файл и измените пароли и ключи безопасности:
```bash
nano .env  # или используйте любой другой редактор
```

**⚠️ ВАЖНО:** Обязательно измените:
- `MYSQL_ROOT_PASSWORD` - пароль root для MySQL
- `MYSQL_PASSWORD` - пароль пользователя базы данных
- `SECRET_KEY` - секретный ключ для JWT (сгенерируйте новый!)
- `ENCRYPTION_KEY` - ключ шифрования (сгенерируйте новый!)
- `OWNER_PASSWORD` - пароль для первого владельца

### 3. Запустите сервер одной командой

```bash
docker-compose up -d
```

Эта команда:
- Создаст и запустит контейнер MySQL
- Создаст и запустит контейнер приложения
- Инициализирует базу данных
- Запустит сервер на порту 8001

### 4. Первый владелец создается автоматически!

Если вы указали `OWNER_PASSWORD` в `.env`, владелец будет создан автоматически при первом запуске.

Если владелец не был создан автоматически, создайте его вручную:

```bash
docker-compose exec app python scripts/create_owner.py \
  --username owner \
  --password YourSecurePassword123
```

Или используйте значения из `.env`:
```bash
docker-compose exec app python scripts/create_owner.py \
  --username ${OWNER_USERNAME} \
  --password ${OWNER_PASSWORD}
```

### 5. Проверьте статус

```bash
docker-compose ps
```

Вы должны увидеть два запущенных контейнера:
- `gradebook_mysql` - база данных
- `gradebook_server` - приложение

### 6. Проверьте логи

```bash
# Логи приложения
docker-compose logs app

# Логи базы данных
docker-compose logs db

# Все логи
docker-compose logs -f
```

## Управление

### Остановка сервера

```bash
docker-compose down
```

### Остановка с удалением данных

⚠️ **ВНИМАНИЕ:** Это удалит все данные из базы данных!

```bash
docker-compose down -v
```

### Перезапуск сервера

```bash
docker-compose restart
```

### Обновление кода

```bash
# Остановите контейнеры
docker-compose down

# Пересоберите образ
docker-compose build

# Запустите снова
docker-compose up -d
```

### Просмотр логов в реальном времени

```bash
docker-compose logs -f app
```

## Доступ к серверу

После запуска сервер будет доступен по адресу:
- **Локально:** http://localhost:8001
- **В сети:** http://your-server-ip:8001

API документация: http://localhost:8001/docs

## Работа с базой данных

### Подключение к MySQL

```bash
docker-compose exec db mysql -u root -p
```

Или с указанием базы данных:
```bash
docker-compose exec db mysql -u root -p grade_book
```

### Резервное копирование базы данных

```bash
docker-compose exec db mysqldump -u root -p grade_book > backup.sql
```

### Восстановление из резервной копии

```bash
docker-compose exec -T db mysql -u root -p grade_book < backup.sql
```

## Переменные окружения

Основные переменные в `.env`:

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `MYSQL_ROOT_PASSWORD` | Пароль root для MySQL | `rootpassword` |
| `MYSQL_USER` | Пользователь БД | `gradebook_user` |
| `MYSQL_PASSWORD` | Пароль пользователя БД | `gradebook_password` |
| `MYSQL_DB` | Имя базы данных | `grade_book` |
| `MYSQL_PORT` | Порт MySQL | `3306` |
| `APP_PORT` | Порт приложения | `8001` |
| `SECRET_KEY` | Секретный ключ JWT | (изменяйте!) |
| `ENCRYPTION_KEY` | Ключ шифрования | (изменяйте!) |
| `OWNER_USERNAME` | Username первого владельца | `owner` |
| `OWNER_PASSWORD` | Пароль первого владельца | (обязательно!) |
| `OWNER_FULL_NAME` | Полное имя владельца | (опционально) |

## Генерация безопасных ключей

Для генерации новых ключей используйте:

```python
# SECRET_KEY (64 символа hex)
import secrets
print(secrets.token_hex(32))

# ENCRYPTION_KEY (Fernet key)
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

Или через командную строку:

```bash
# SECRET_KEY
python -c "import secrets; print(secrets.token_hex(32))"

# ENCRYPTION_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Устранение проблем

### Контейнер не запускается

1. Проверьте логи:
   ```bash
   docker-compose logs app
   ```

2. Проверьте, что порты не заняты:
   ```bash
   # Linux/Mac
   lsof -i :8001
   lsof -i :3306
   
   # Windows
   netstat -ano | findstr :8001
   ```

3. Проверьте права доступа к файлам:
   ```bash
   chmod -R 755 profile_photos achievements
   ```

### База данных не подключается

1. Убедитесь, что MySQL контейнер запущен:
   ```bash
   docker-compose ps db
   ```

2. Проверьте логи MySQL:
   ```bash
   docker-compose logs db
   ```

3. Проверьте переменные окружения в `.env`

### Ошибка "Permission denied"

На Linux может потребоваться изменить права:
```bash
sudo chown -R $USER:$USER profile_photos achievements
```

### Очистка и перезапуск с нуля

```bash
# Остановить и удалить все
docker-compose down -v

# Удалить образы
docker-compose rm -f

# Пересобрать и запустить
docker-compose up -d --build
```

## Production настройки

Для production окружения:

1. **Используйте сильные пароли** в `.env`
2. **Измените SECRET_KEY и ENCRYPTION_KEY**
3. **Настройте reverse proxy** (nginx) для HTTPS
4. **Ограничьте доступ к портам** через firewall
5. **Настройте регулярные бэкапы** базы данных
6. **Используйте volumes** для персистентного хранения
7. **Настройте мониторинг** и логирование

### Пример nginx конфигурации

```nginx
server {
    listen 80;
    server_name your-domain.com;
    
    location / {
        proxy_pass http://localhost:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Дополнительные команды

### Выполнение команд в контейнере

```bash
# Python shell
docker-compose exec app python

# Bash shell
docker-compose exec app bash

# Выполнение скриптов
docker-compose exec app python scripts/create_owner.py --username admin --password pass
```

### Просмотр использования ресурсов

```bash
docker stats
```

### Очистка неиспользуемых ресурсов

```bash
docker system prune -a
```

## Поддержка

При возникновении проблем:
1. Проверьте логи: `docker-compose logs`
2. Проверьте статус: `docker-compose ps`
3. Убедитесь, что все переменные окружения настроены правильно
