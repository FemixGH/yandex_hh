# Telegram Service Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Копируем общие файлы проекта и зависимости
COPY requirements.txt ./
COPY settings.py ./
COPY lockbox_loader.py ./
COPY yandex_jwt_auth.py ./
COPY logging_conf.py ./

# Копируем исходники Telegram сервиса
COPY services/telegram/ ./services/telegram/

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Директория для логов
RUN mkdir -p /app/logs

# Порт сервиса (Serverless Containers используют 8080 через переменные окружения)
EXPOSE 8001

# Команда запуска (uvicorn стартует из main.py)
CMD ["python", "services/telegram/main.py"]
