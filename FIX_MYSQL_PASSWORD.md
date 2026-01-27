# Исправление проблемы с паролем MySQL

## Проблема

Ошибка: `Access denied for user 'root'@'172.18.0.3' (using password: YES)`

## Причина

В MySQL Docker контейнере:
- Пароль для пользователя `root` устанавливается через переменную `MYSQL_ROOT_PASSWORD`
- Если `MYSQL_USER=root`, то используется пароль из `MYSQL_ROOT_PASSWORD`, а не из `MYSQL_PASSWORD`
- `MYSQL_PASSWORD` используется только для создания дополнительного пользователя (если `MYSQL_USER` не root)

## Решение

В `docker-compose.yml` для сервиса `app` нужно использовать `MYSQL_ROOT_PASSWORD` для подключения как root:

```yaml
environment:
  MYSQL_HOST: ${MYSQL_HOST:-db}
  MYSQL_USER: ${MYSQL_USER:-root}
  MYSQL_PASSWORD: ${MYSQL_ROOT_PASSWORD:-rootpassword}  # Используем root password
  MYSQL_DB: ${MYSQL_DB:-grade_book}
  MYSQL_PORT: 3306
```

## Альтернативное решение: Создать отдельного пользователя

Если хочешь использовать отдельного пользователя (не root):

1. В сервисе `db` установи:
```yaml
environment:
  MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD:-rootpassword}
  MYSQL_DATABASE: ${MYSQL_DB:-grade_book}
  MYSQL_USER: ${MYSQL_USER:-gradebook_user}  # Не root!
  MYSQL_PASSWORD: ${MYSQL_PASSWORD:-gradebook_password}
```

2. В сервисе `app` используй:
```yaml
environment:
  MYSQL_USER: ${MYSQL_USER:-gradebook_user}
  MYSQL_PASSWORD: ${MYSQL_PASSWORD:-gradebook_password}
```

## Текущая конфигурация

Сейчас используется подход с root пользователем, поэтому в `app` сервисе `MYSQL_PASSWORD` должен быть равен `MYSQL_ROOT_PASSWORD`.
