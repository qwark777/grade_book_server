# Настройка доступа к серверу из интернета

## Быстрая настройка

### 1. Открыть порт в файрволе

**Для Ubuntu/Debian (ufw):**
```bash
sudo ufw allow 8001/tcp
sudo ufw status
```

**Для CentOS/RHEL (firewalld):**
```bash
sudo firewall-cmd --permanent --add-port=8001/tcp
sudo firewall-cmd --reload
sudo firewall-cmd --list-ports
```

**Для iptables:**
```bash
sudo iptables -A INPUT -p tcp --dport 8001 -j ACCEPT
sudo iptables-save
```

### 2. Проверить, что Docker пробрасывает порт

```bash
docker ps
# Должен показать: 0.0.0.0:8001->8001/tcp

# Или проверить напрямую:
docker port gradebook_server
```

### 3. Проверить доступность

**С сервера:**
```bash
curl http://localhost:8001/api/v1/health
# или
curl http://127.0.0.1:8001/api/v1/health
```

**Из интернета (с другого компьютера):**
```bash
curl http://85.198.86.132:8001/api/v1/health
```

**В браузере:**
```
http://85.198.86.132:8001/docs
```

## Настройка через Nginx (рекомендуется для продакшена)

### 1. Установить Nginx

```bash
sudo apt update
sudo apt install nginx -y
```

### 2. Создать конфигурацию

```bash
sudo nano /etc/nginx/sites-available/gradebook
```

Вставь:
```nginx
server {
    listen 80;
    server_name 85.198.86.132;

    client_max_body_size 10M;

    location / {
        proxy_pass http://localhost:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
    }

    # WebSocket support
    location /ws {
        proxy_pass http://localhost:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

### 3. Активировать конфигурацию

```bash
sudo ln -s /etc/nginx/sites-available/gradebook /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 4. Открыть порт 80

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp  # для HTTPS в будущем
```

Теперь доступно по: `http://85.198.86.132`

## Настройка HTTPS (Let's Encrypt)

### 1. Установить Certbot

```bash
sudo apt install certbot python3-certbot-nginx -y
```

### 2. Получить сертификат (если есть домен)

```bash
sudo certbot --nginx -d yourdomain.com
```

### 3. Автоматическое обновление

```bash
sudo certbot renew --dry-run
```

## Проверка безопасности

### 1. Закрыть прямой доступ к порту 8001 (если используешь Nginx)

```bash
# В docker-compose.yml изменить:
ports:
  - "127.0.0.1:8001:8001"  # только localhost
```

### 2. Настроить CORS (если нужно)

В `app/main.py` добавь:
```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # или конкретные домены
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

## Устранение проблем

### Порт не открыт

```bash
# Проверить, слушает ли приложение
sudo netstat -tlnp | grep 8001
# или
sudo ss -tlnp | grep 8001

# Проверить файрвол
sudo ufw status verbose
```

### Docker не пробрасывает порт

```bash
# Пересоздать контейнер
docker-compose down
docker-compose up -d

# Проверить логи
docker logs gradebook_server
```

### Nginx не проксирует

```bash
# Проверить конфигурацию
sudo nginx -t

# Проверить логи
sudo tail -f /var/log/nginx/error.log
sudo tail -f /var/log/nginx/access.log
```

## Быстрая проверка

```bash
# 1. Проверить, что контейнер запущен
docker ps | grep gradebook

# 2. Проверить порты
docker port gradebook_server

# 3. Проверить файрвол
sudo ufw status

# 4. Проверить доступность
curl -I http://85.198.86.132:8001/docs
```
