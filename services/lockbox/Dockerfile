# Используем официальный минималистичный образ Python
FROM python:3.11-slim-bookworm

# Устанавливаем рабочую директорию
WORKDIR /app

# Создаем непривилегированного пользователя для безопасности
RUN groupadd -r lockbox && useradd -r -g lockbox lockbox

# Копируем requirements.txt и устанавливаем зависимости
COPY services/lockbox/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код сервиса
COPY services/lockbox/main.py ./main.py

# Копируем общие модули (для опционального JWT->IAM)
COPY settings.py ./settings.py
COPY yandex_jwt_auth.py ./yandex_jwt_auth.py

# Устанавливаем владельца файлов
RUN chown -R lockbox:lockbox /app

# Переключаемся на непривилегированного пользователя
USER lockbox

# Открываем порт сервиса
EXPOSE 8006

# Запускаем приложение
CMD ["python", "main.py"]
