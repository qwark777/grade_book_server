#!/bin/bash
# Скрипт для проверки и создания владельца вручную

echo "🔍 Проверка статуса владельца в системе..."
echo ""

# Проверяем, запущен ли контейнер
if ! docker ps | grep -q gradebook_server; then
    echo "❌ Контейнер gradebook_server не запущен!"
    echo "   Запусти: docker-compose up -d"
    exit 1
fi

echo "✅ Контейнер запущен"
echo ""

# Проверяем логи создания владельца
echo "📋 Последние логи создания владельца:"
docker logs gradebook_server 2>&1 | grep -i -E "owner|владелец|OWNER" | tail -10

echo ""
echo "🔧 Для создания владельца вручную выполни:"
echo ""
echo "   docker-compose exec app python scripts/create_owner.py --username owner --password YOUR_PASSWORD"
echo ""
echo "   Или с полным именем:"
echo "   docker-compose exec app python scripts/create_owner.py --username owner --password YOUR_PASSWORD --full-name 'System Owner'"
echo ""
