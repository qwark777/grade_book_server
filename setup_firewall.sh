#!/bin/bash
# Скрипт для настройки файрвола и открытия портов

set -e

echo "🔥 Настройка файрвола для Grade Book Server"
echo ""

# Определяем систему
if command -v ufw &> /dev/null; then
    echo "📦 Обнаружен UFW (Ubuntu/Debian)"
    
    # Проверяем статус
    if sudo ufw status | grep -q "Status: active"; then
        echo "✅ UFW уже активен"
    else
        echo "⚠️  UFW не активен. Активируем..."
        echo "y" | sudo ufw enable
    fi
    
    # Открываем порты
    echo "🔓 Открываем порт 8001 для API..."
    sudo ufw allow 8001/tcp comment 'Grade Book API'
    
    echo "🔓 Открываем порт 80 для HTTP (если используешь Nginx)..."
    sudo ufw allow 80/tcp comment 'HTTP'
    
    echo "🔓 Открываем порт 443 для HTTPS (если используешь Nginx)..."
    sudo ufw allow 443/tcp comment 'HTTPS'
    
    echo ""
    echo "📊 Текущий статус файрвола:"
    sudo ufw status verbose
    
elif command -v firewall-cmd &> /dev/null; then
    echo "📦 Обнаружен firewalld (CentOS/RHEL)"
    
    # Проверяем статус
    if sudo systemctl is-active --quiet firewalld; then
        echo "✅ firewalld активен"
    else
        echo "⚠️  firewalld не активен. Запускаем..."
        sudo systemctl start firewalld
        sudo systemctl enable firewalld
    fi
    
    # Открываем порты
    echo "🔓 Открываем порт 8001 для API..."
    sudo firewall-cmd --permanent --add-port=8001/tcp
    sudo firewall-cmd --permanent --add-service=http
    sudo firewall-cmd --permanent --add-service=https
    
    echo "🔄 Перезагружаем firewalld..."
    sudo firewall-cmd --reload
    
    echo ""
    echo "📊 Открытые порты:"
    sudo firewall-cmd --list-ports
    
else
    echo "⚠️  Не обнаружен UFW или firewalld"
    echo "📝 Используй iptables вручную:"
    echo "   sudo iptables -A INPUT -p tcp --dport 8001 -j ACCEPT"
    echo "   sudo iptables-save"
fi

echo ""
echo "✅ Настройка файрвола завершена!"
echo ""
echo "🌐 Теперь API должен быть доступен по:"
echo "   http://85.198.86.132:8001"
echo "   http://85.198.86.132:8001/docs"
echo ""
echo "🔍 Проверь доступность:"
echo "   curl http://85.198.86.132:8001/api/v1/health"
