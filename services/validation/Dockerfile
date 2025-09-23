# Validation Service Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Копируем общие файлы проекта
COPY requirements.txt .
COPY settings.py .
COPY logging_conf.py .
COPY moderation_yandex.py .
COPY yandex_api.py .
COPY yandex_jwt_auth.py .

# Копируем файлы Validation сервиса
COPY services/validation/ ./services/validation/

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Экспонируем порт
EXPOSE 8003

# Запускаем приложение
CMD ["python", "services/validation/main.py"]
