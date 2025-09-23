# Gateway Service Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Копируем общие файлы проекта
COPY requirements.txt .
COPY settings.py .
COPY logging_conf.py .

# Копируем файлы Gateway сервиса
COPY services/gateway/ ./services/gateway/

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Создаем директории для логов
RUN mkdir -p /app/logs

# Экспонируем порт
EXPOSE 8000

# Запускаем приложение
CMD ["python", "services/gateway/main.py"]
