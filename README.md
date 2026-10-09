# GradeBook — сервер

Сервер электронного дневника на Python/FastAPI. Предоставляет REST API и WebSocket для Flutter-приложения, хранит данные в MySQL и использует Redis для обмена событиями между процессами. Поддерживает пользователей и школы, расписание, оценки, домашние задания, сообщения, достижения, подписки, интеграции CRM и заявки с сайта.

## Связанные проекты

- `../app` — приложение Flutter/Dart.
- `../landing` — статический сайт, отправляющий заявки в `/api/v1/landing/leads`.

## Запуск через Docker

Нужны Docker и Docker Compose. Команды выполняются из этой папки:

```bash
cp .env.example .env
```

Заполните `.env`: `MYSQL_ROOT_PASSWORD`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DB`, `SECRET_KEY`, `ENCRYPTION_KEY`, `OWNER_USERNAME`, `OWNER_PASSWORD`, а также разрешённые адреса сайта в `BACKEND_CORS_ORIGINS` и API-хосты в `TRUSTED_HOSTS`.

Генерация ключей:

```bash
python3 -c 'import secrets; print(secrets.token_hex(32))'
# Следующая команда требует установленного cryptography:
python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

```bash
docker compose up -d --build
curl -fsS http://127.0.0.1:8001/api/v1/health
docker compose logs --tail=100 app
```

Обычный compose запускает MySQL, Redis и API; API по умолчанию доступен на порту 8001. Для публичного сервера используйте `docker-compose.prod.yml` и [руководство эксплуатации](docs/PRODUCTION_RUNBOOK.md), включая миграции и резервные копии.

## Локальный запуск

Dockerfile использует Python 3.11; `.python-version` указывает 3.12 для локального окружения. Установите зависимости в отдельное виртуальное окружение, подготовьте MySQL и настройте `.env` для локальных адресов сервисов:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Заполните .env; MYSQL_HOST должен указывать на вашу MySQL.
python -m alembic upgrade head
python -m app.main
```

Настройки загружаются из `.env`; переменные процесса имеют приоритет. Проверка конфигурации: `python scripts/doctor.py`. Создание владельца вручную: `python scripts/create_owner.py --help`.

## Структура

- `app/api`, `app/models`, `app/core` — маршруты, модели и настройки.
- `app/db`, `app/websocket` — работа с базой и событиями.
- `migrations`, `alembic.ini` — миграции схемы.
- `scripts`, `Makefile` — обслуживание, проверки и резервные копии.
- `deploy`, `docker-compose.prod.yml` — конфигурация production.
- `test_*.py`, `test_fixtures` — тесты и фикстуры.
- `docs` — руководства по эксплуатации и предметным подсистемам.

Файлы пользователей находятся в `profile_photos`, `homework_submissions`, `homework_attachments`, `message_attachments`; каталог `achievements` содержит серверные изображения наград. В Docker они сохраняются в отдельных volumes. Содержимое этих каталогов, резервные копии и обученные модели не включаются в Git или Docker-образ.

## Проверки

```bash
make doctor
make test
```

Для полной проверки перед выпуском: `bash scripts/predeploy_check.sh`. Дополнительные тесты могут требовать настроенной тестовой базы; не запускайте скрипты заполнения демо-данных на рабочей базе.

## Документация

- [Production и резервные копии](docs/PRODUCTION_RUNBOOK.md).
- [CRM](docs/CRM_INTEGRATION_GUIDE.md) и [1С](docs/INTEGRATION_1C_GUIDE.md).
- [Подписки](docs/SUBSCRIPTION_SYSTEM.md).
- [Коэффициенты предметов](docs/SUBJECT_COEFFICIENTS.md).
- [ML-модель](docs/ML_SETUP.md).
- [Управление достижениями](scripts/ACHIEVEMENTS_README.md).

Предметные руководства содержат исторические примеры: точные маршруты и схемы сверяйте с `app/api` и моделями. `.env` и приватные ключи остаются локальными; в Git хранятся только `.env.example` и `.env.production.example`. `.gitignore` не удаляет файлы, ранее добавленные в индекс.
