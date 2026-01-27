#!/bin/bash
# Скрипт для проверки доступности сервера

set -e

SERVER_IP="${1:-85.198.86.132}"
PORT="${2:-8001}"

echo "🔍 Проверка доступности Grade Book Server"
echo "   IP: $SERVER_IP"
echo "   Port: $PORT"
echo ""

# Проверка 1: Docker контейнер
echo "1️⃣  Проверка Docker контейнера..."
if docker ps | grep -q gradebook_server; then
    echo "   ✅ Контейнер запущен"
    docker ps | grep gradebook_server
else
    echo "   ❌ Контейнер не запущен!"
    exit 1
fi

echo ""

# Проверка 2: Проброс порта
echo "2️⃣  Проверка проброса порта..."
if docker port gradebook_server 2>/dev/null | grep -q "$PORT"; then
    echo "   ✅ Порт проброшен:"
    docker port gradebook_server
else
    echo "   ⚠️  Порт не проброшен или контейнер не запущен"
fi

echo ""

# Проверка 3: Локальный доступ
echo "3️⃣  Проверка локального доступа..."
if curl -s -o /dev/null -w "%{http_code}" http://localhost:$PORT/docs | grep -q "200"; then
    echo "   ✅ Локальный доступ работает"
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:$PORT/docs)
    echo "   HTTP код: $HTTP_CODE"
else
    echo "   ❌ Локальный доступ не работает"
    echo "   Проверь логи: docker logs gradebook_server"
fi

echo ""

# Проверка 4: Доступ из интернета
echo "4️⃣  Проверка доступа из интернета..."
if curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://$SERVER_IP:$PORT/docs 2>/dev/null | grep -q "200"; then
    echo "   ✅ Доступ из интернета работает!"
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://$SERVER_IP:$PORT/docs)
    echo "   HTTP код: $HTTP_CODE"
    echo ""
    echo "   🌐 API доступен по адресу:"
    echo "      http://$SERVER_IP:$PORT"
    echo "      http://$SERVER_IP:$PORT/docs"
else
    echo "   ❌ Доступ из интернета не работает"
    echo ""
    echo "   🔧 Возможные причины:"
    echo "      1. Файрвол блокирует порт $PORT"
    echo "      2. Провайдер блокирует входящие соединения"
    echo "      3. Приложение не слушает на 0.0.0.0"
    echo ""
    echo "   💡 Решения:"
    echo "      - Запусти: ./setup_firewall.sh"
    echo "      - Проверь файрвол: sudo ufw status"
    echo "      - Проверь логи: docker logs gradebook_server"
fi

echo ""

# Проверка 5: Файрвол
echo "5️⃣  Проверка файрвола..."
if command -v ufw &> /dev/null; then
    if sudo ufw status | grep -q "$PORT"; then
        echo "   ✅ Порт $PORT открыт в UFW"
    else
        echo "   ⚠️  Порт $PORT не найден в правилах UFW"
        echo "   Запусти: sudo ufw allow $PORT/tcp"
    fi
elif command -v firewall-cmd &> /dev/null; then
    if sudo firewall-cmd --list-ports | grep -q "$PORT"; then
        echo "   ✅ Порт $PORT открыт в firewalld"
    else
        echo "   ⚠️  Порт $PORT не найден в правилах firewalld"
        echo "   Запусти: sudo firewall-cmd --permanent --add-port=$PORT/tcp && sudo firewall-cmd --reload"
    fi
else
    echo "   ⚠️  Файрвол не обнаружен (UFW/firewalld)"
fi

echo ""
echo "✅ Проверка завершена!"
