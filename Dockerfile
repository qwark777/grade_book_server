FROM python:3.11-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Копируем файл зависимостей
COPY requirements.txt .

# Устанавливаем Python зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код приложения
COPY . .

# Создаем директории для файлов
RUN mkdir -p profile_photos achievements homework_submissions homework_attachments message_attachments

# Открываем порт
EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; request=urllib.request.Request('http://127.0.0.1:8001/api/v1/health', headers={'Host':'api.gradebook.su'}); urllib.request.urlopen(request, timeout=3).read()"

# Команда запуска
CMD ["python", "-m", "app.main"]
