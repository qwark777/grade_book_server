# Устранение проблем с созданием владельца/админа

## Проблема: Владелец не создался при развертывании

### Причины:

1. **OWNER_PASSWORD не установлен в .env файле**
   - Скрипт пропускает создание, если пароль не указан
   - Проверь файл `.env` на сервере

2. **Ошибка подключения к базе данных**
   - База данных еще не готова при запуске скрипта
   - Проверь логи контейнера

3. **Владелец уже существует**
   - Скрипт не создает нового, если уже есть владелец

## Решения:

### 1. Проверь логи контейнера

```bash
docker logs gradebook_server
```

Ищи строки с:
- `OWNER_PASSWORD не установлен`
- `Владелец успешно создан`
- `Ошибка подключения к базе данных`

### 2. Проверь переменные окружения

```bash
# Проверь, что .env файл существует
cat .env | grep OWNER

# Должно быть:
# OWNER_USERNAME=owner
# OWNER_PASSWORD=твой_пароль
# OWNER_FULL_NAME=System Owner
```

### 3. Создай владельца вручную

```bash
# Войди в контейнер
docker-compose exec app bash

# Создай владельца
python scripts/create_owner.py --username owner --password твой_пароль

# Или с полным именем
python scripts/create_owner.py --username owner --password твой_пароль --full-name "System Owner"
```

Или из хоста:

```bash
docker-compose exec app python scripts/create_owner.py --username owner --password твой_пароль
```

### 4. Проверь, существует ли владелец

```bash
# Войди в MySQL контейнер
docker-compose exec db mysql -u root -p${MYSQL_ROOT_PASSWORD:-rootpassword} grade_book

# Выполни запрос
SELECT id, username, role FROM users WHERE role = 'owner';
```

### 5. Пересоздай владельца (если нужно)

```bash
# Удали существующего владельца
docker-compose exec db mysql -u root -p${MYSQL_ROOT_PASSWORD:-rootpassword} grade_book -e "DELETE FROM users WHERE role = 'owner';"

# Перезапусти контейнер или создай вручную
docker-compose restart app
```

## Создание администратора

После создания владельца, войди в приложение и создай администратора через UI, или используй скрипт:

```bash
# Сначала нужно создать школу (через API или вручную в БД)
# Затем создай админа:
docker-compose exec app python scripts/create_admin.py --username admin --password твой_пароль --school-id 1
```

## Автоматическое создание при следующем запуске

1. Убедись, что в `.env` файле установлены:
   ```env
   OWNER_USERNAME=owner
   OWNER_PASSWORD=твой_безопасный_пароль
   OWNER_FULL_NAME=System Owner
   ```

2. Удали существующего владельца (если нужно):
   ```bash
   docker-compose exec db mysql -u root -p${MYSQL_ROOT_PASSWORD:-rootpassword} grade_book -e "DELETE FROM users WHERE role = 'owner';"
   ```

3. Перезапусти контейнер:
   ```bash
   docker-compose restart app
   ```

## Быстрая проверка

```bash
# Запусти скрипт проверки
chmod +x scripts/check_and_create_owner.sh
./scripts/check_and_create_owner.sh
```
