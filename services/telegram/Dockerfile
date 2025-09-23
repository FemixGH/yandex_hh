# Telegram Service Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Копируем общие файлы проекта
COPY requirements.txt .
COPY settings.py .
COPY logging_conf.py .

# Копируем файлы Telegram сервиса
COPY services/telegram/ ./services/telegram/

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Экспонируем порт
EXPOSE 8001

# Запускаем приложение
CMD ["python", "services/telegram/main.py"]
