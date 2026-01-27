# Устранение проблем с Docker

## Ошибка 'ContainerConfig'

Эта ошибка обычно возникает из-за проблем с конфигурацией volumes или синтаксисом docker-compose.yml.

### Решение 1: Проверьте синтаксис

```bash
# Проверьте синтаксис docker-compose.yml
docker-compose config
```

### Решение 2: Убедитесь, что директории существуют

```bash
cd grade_book_server
mkdir -p profile_photos achievements
```

### Решение 3: Используйте именованные volumes вместо bind mounts

Если проблема с относительными путями, используйте именованные volumes:

```yaml
volumes:
  - profile_photos_data:/app/profile_photos
  - achievements_data:/app/achievements

# В секции volumes:
volumes:
  profile_photos_data:
  achievements_data:
```

### Решение 4: Обновите docker-compose

Старая версия docker-compose (1.29.2) может иметь проблемы. Обновите:

```bash
# Для Linux
sudo apt-get update
sudo apt-get install docker-compose-plugin

# Используйте docker compose (без дефиса) вместо docker-compose
docker compose up -d
```

### Решение 5: Очистите старые контейнеры и volumes

```bash
# Остановите и удалите контейнеры
docker-compose down

# Удалите старые volumes (⚠️ удалит данные!)
docker-compose down -v

# Очистите неиспользуемые ресурсы
docker system prune -a
```

### Решение 6: Проверьте права доступа

```bash
# Убедитесь, что у вас есть права на директории
chmod -R 755 profile_photos achievements
```

### Решение 7: Используйте абсолютные пути

Если относительные пути не работают:

```yaml
volumes:
  - /full/path/to/profile_photos:/app/profile_photos
  - /full/path/to/achievements:/app/achievements
```

## Альтернативный вариант: без bind mounts

Если проблемы продолжаются, используйте именованные volumes (данные будут храниться в Docker):

```yaml
volumes:
  - profile_photos_data:/app/profile_photos
  - achievements_data:/app/achievements
```

Но тогда файлы не будут доступны напрямую на хосте.
