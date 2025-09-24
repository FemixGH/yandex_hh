# Yandex API Service Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Копируем общие файлы проекта
COPY requirements.txt .
COPY settings.py .
COPY logging_conf.py .
COPY yandex_api.py .
COPY yandex_jwt_auth.py .
COPY ../../moderation_yandex.py .

# Копируем файлы Yandex сервиса
COPY services/yandex/ ./services/yandex/

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Экспонируем порт
EXPOSE 8004

# Запускаем приложение
CMD ["python", "services/yandex/main.py"]
