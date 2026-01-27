#!/bin/bash
# Скрипт для создания необходимых директорий

echo "📁 Создание директорий..."

cd "$(dirname "$0")"

# Создаем директории, если их нет
mkdir -p profile_photos
mkdir -p achievements

# Устанавливаем права доступа
chmod -R 755 profile_photos achievements

echo "✅ Директории созданы:"
ls -la | grep -E "profile_photos|achievements"

echo ""
echo "Теперь можно запускать: docker-compose up -d"
