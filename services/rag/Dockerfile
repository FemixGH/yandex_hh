# RAG Service Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Копируем общие файлы проекта
COPY requirements.txt .
COPY settings.py .
COPY logging_conf.py .
COPY rag_yandex_nofaiss.py .
COPY faiss_index_yandex.py .
COPY bartender_file_handler.py .
COPY incremental_rag.py .
COPY yandex_api.py .
COPY yandex_jwt_auth.py .

# Копируем файлы RAG сервиса
COPY services/rag/ ./services/rag/

# Устанавливаем системные зависимости для PyMuPDF
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Создаем директории
RUN mkdir -p /app/vectorstore /app/faiss_index_yandex

# Экспонируем порт
EXPOSE 8002

# Запускаем приложение
CMD ["python", "services/rag/main.py"]
