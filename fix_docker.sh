#!/bin/bash
# Скрипт для исправления проблем с Docker

echo "🔧 Исправление проблем с Docker..."

# Остановить и удалить все контейнеры
echo "1. Остановка контейнеров..."
docker-compose down 2>/dev/null || true

# Удалить старые контейнеры
echo "2. Удаление старых контейнеров..."
docker rm -f gradebook_server gradebook_mysql 2>/dev/null || true

# Очистить неиспользуемые volumes (опционально)
read -p "Удалить volumes? Это удалит данные БД! (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "3. Удаление volumes..."
    docker-compose down -v 2>/dev/null || true
    docker volume rm grade_book_server_mysql_data 2>/dev/null || true
fi

# Пересобрать образы
echo "4. Пересборка образов..."
docker-compose build --no-cache

# Запустить заново
echo "5. Запуск контейнеров..."
docker-compose up -d

echo "✅ Готово! Проверьте логи: docker-compose logs -f"
