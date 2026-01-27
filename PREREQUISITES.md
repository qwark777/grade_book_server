# Требования перед установкой

## ✅ Что НЕ нужно устанавливать

**MySQL НЕ нужно устанавливать на хосте!** Docker Compose автоматически скачает и запустит MySQL в контейнере.

Также НЕ нужно устанавливать:
- Python
- pip
- Библиотеки Python
- MySQL клиент

Все это устанавливается автоматически в Docker контейнерах.

## ✅ Что нужно установить

### 1. Docker

**Ubuntu/Debian:**
```bash
# Обновляем пакеты
sudo apt update

# Устанавливаем зависимости
sudo apt install -y apt-transport-https ca-certificates curl software-properties-common

# Добавляем официальный GPG ключ Docker
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

# Добавляем репозиторий Docker
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Устанавливаем Docker
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Добавляем текущего пользователя в группу docker (чтобы не использовать sudo)
sudo usermod -aG docker $USER

# Перезагрузи компьютер или выполни:
newgrp docker
```

**CentOS/RHEL:**
```bash
# Устанавливаем зависимости
sudo yum install -y yum-utils

# Добавляем репозиторий Docker
sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo

# Устанавливаем Docker
sudo yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Запускаем Docker
sudo systemctl start docker
sudo systemctl enable docker

# Добавляем пользователя в группу docker
sudo usermod -aG docker $USER
newgrp docker
```

**Проверка установки:**
```bash
docker --version
# Должно показать: Docker version 20.10.x или выше
```

### 2. Docker Compose

Если Docker Compose не установился вместе с Docker:

**Ubuntu/Debian:**
```bash
sudo apt install -y docker-compose-plugin
```

**Или используй standalone версию:**
```bash
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
```

**Проверка:**
```bash
docker compose version
# или
docker-compose --version
```

## 🚀 Быстрая проверка готовности

Выполни эти команды, чтобы убедиться, что все готово:

```bash
# 1. Проверь Docker
docker --version

# 2. Проверь Docker Compose
docker compose version

# 3. Проверь, что Docker работает
docker ps

# 4. Проверь, что можешь скачивать образы
docker pull hello-world
docker run hello-world
```

Если все команды выполнились успешно, ты готов к развертыванию!

## 📦 Что происходит при запуске `docker-compose up`

1. **Docker скачивает образы:**
   - `mysql:8.0` - база данных MySQL (автоматически)
   - Собирает образ приложения из `Dockerfile`

2. **Docker создает контейнеры:**
   - `gradebook_mysql` - контейнер с MySQL
   - `gradebook_server` - контейнер с приложением

3. **Автоматически выполняется:**
   - Инициализация базы данных
   - Создание таблиц
   - Создание владельца (если указан `OWNER_PASSWORD`)

## ⚠️ Важные замечания

1. **Порт 3306:** Если на хосте уже установлен MySQL и он использует порт 3306, измени `MYSQL_PORT` в `.env` или `docker-compose.yml`

2. **Порт 8001:** Убедись, что порт 8001 свободен, или измени `APP_PORT` в `.env`

3. **Права доступа:** Если используешь `sudo` для Docker команд, добавь пользователя в группу `docker` (см. выше)

4. **Дисковое пространство:** Убедись, что есть минимум 2-3 GB свободного места для образов и данных

## 🔧 Если что-то не работает

### Docker не запускается:
```bash
# Проверь статус
sudo systemctl status docker

# Запусти Docker
sudo systemctl start docker
sudo systemctl enable docker
```

### Проблемы с правами:
```bash
# Добавь пользователя в группу docker
sudo usermod -aG docker $USER

# Выйди и войди заново, или выполни:
newgrp docker
```

### Порт занят:
```bash
# Проверь, что использует порт
sudo netstat -tlnp | grep 3306
sudo netstat -tlnp | grep 8001

# Или измени порты в docker-compose.yml
```

## 📚 Дополнительная информация

- [Официальная документация Docker](https://docs.docker.com/)
- [Docker Compose документация](https://docs.docker.com/compose/)
- [Инструкция по развертыванию](DOCKER_SETUP.md)
