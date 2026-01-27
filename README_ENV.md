# Настройка переменных окружения

## Быстрый старт

Приложение использует значения по умолчанию из `config.py` и `docker-compose.yml`, которые совпадают с `.env.test`.

**Для Docker развертывания:**
- Можно запустить без `.env` файла - все значения будут взяты из значений по умолчанию
- Или скопируй `.env.test` в `.env` и измени только нужные значения

**Для локальной разработки:**
- Скопируй `.env.test` в `.env`
- Или установи `MYSQL_HOST=localhost` в `.env`

## Значения по умолчанию

Все значения по умолчанию совпадают с `.env.test`:

```env
MYSQL_HOST=db              # Для Docker (в config.py)
MYSQL_HOST=localhost       # Для локальной разработки
MYSQL_USER=root
MYSQL_PASSWORD=12345678
MYSQL_DB=grade_book
MYSQL_PORT=3306
MYSQL_ROOT_PASSWORD=rootpassword

APP_PORT=8001

SECRET_KEY=304f1388f317fe2e917a1df468144def7f60586ba96dc80b07d26c68cae00fab
ENCRYPTION_KEY=ICkoftk-wbOx89vzo2nuGkPatHZCQ1IKBVpFdRJ1F4k=

OWNER_USERNAME=owner
OWNER_PASSWORD=test_password_123
OWNER_FULL_NAME=Test Owner
```

## Создание .env файла

### Вариант 1: Копирование из .env.test

```bash
cp .env.test .env
# Затем отредактируй .env и измени нужные значения
```

### Вариант 2: Использование .env.example

```bash
cp .env.example .env
# Затем отредактируй .env и измени нужные значения
```

### Вариант 3: Без .env файла

Можно запустить без `.env` файла - все значения будут взяты из значений по умолчанию в `config.py` и `docker-compose.yml`.

**Для Docker:**
```bash
docker-compose up -d
```

**Для локальной разработки:**
```bash
export MYSQL_HOST=localhost
python main_new.py
```

## Важные замечания

1. **MYSQL_HOST**: 
   - В Docker используй `db` (значение по умолчанию)
   - Для локальной разработки используй `localhost`

2. **Пароли**: 
   - В продакшене обязательно измени все пароли!
   - Особенно `OWNER_PASSWORD`, `MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD`

3. **Безопасность**:
   - Не коммить `.env` файл в Git
   - Используй разные ключи для продакшена
