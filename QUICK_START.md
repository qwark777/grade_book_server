# 🚀 Быстрый старт с Docker

Развертывание сервера одной командой!

## Шаг 1: Настройка

```bash
cd grade_book_server
cp .env.example .env
```

Отредактируйте `.env` и обязательно измените:
- `MYSQL_ROOT_PASSWORD` - пароль root для MySQL
- `MYSQL_PASSWORD` - пароль пользователя БД  
- `OWNER_PASSWORD` - пароль первого владельца
- `SECRET_KEY` - сгенерируйте новый (см. ниже)
- `ENCRYPTION_KEY` - сгенерируйте новый (см. ниже)

## Шаг 2: Генерация ключей (опционально)

```bash
# SECRET_KEY
python -c "import secrets; print(secrets.token_hex(32))"

# ENCRYPTION_KEY  
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Шаг 3: Запуск

```bash
docker-compose up -d
```

Готово! Сервер запущен на http://localhost:8001

## Шаг 4: Проверка

```bash
# Проверить статус
docker-compose ps

# Посмотреть логи
docker-compose logs -f app

# Проверить API
curl http://localhost:8001/docs
```

## Вход в систему

Используйте учетные данные из `.env`:
- **Username:** значение `OWNER_USERNAME` (по умолчанию `owner`)
- **Password:** значение `OWNER_PASSWORD`

## Полезные команды

```bash
# Остановить
docker-compose down

# Перезапустить
docker-compose restart

# Обновить код
docker-compose down
docker-compose build
docker-compose up -d
```

📖 Подробная документация: [DOCKER_SETUP.md](DOCKER_SETUP.md)
