# Logging Service Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Копируем общие файлы проекта
COPY requirements.txt .
COPY settings.py .
COPY logging_conf.py .

# Копируем файлы Logging сервиса
COPY services/logging/ ./services/logging/

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Создаем директории для логов
RUN mkdir -p /app/logs

# Экспонируем порт
EXPOSE 8005

# Запускаем приложение
CMD ["python", "services/logging/main.py"]
