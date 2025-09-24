#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yandex API Service - сервис для взаимодействия с API Яндекса
"""

import os
import logging
import traceback
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import uvicorn

# Импорты из оригинального проекта
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from yandex_api import yandex_completion, yandex_text_embedding, yandex_batch_embeddings
from moderation_yandex import extract_text_from_yandex_completion  # добавлено

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI приложение
app = FastAPI(
    title="Yandex API Service",
    description="Сервис для взаимодействия с API Яндекса (GPT, embeddings)",
    version="1.0.0"
)

# ========================
# Pydantic модели
# ========================

class CompletionRequest(BaseModel):
    """Модель запроса генерации текста"""
    prompt: str = Field(..., description="Промпт для генерации", min_length=1)
    model_uri: Optional[str] = Field(None, description="URI модели")
    max_tokens: Optional[int] = Field(2000, description="Максимальное количество токенов", ge=1, le=8000)
    temperature: Optional[float] = Field(0.3, description="Температура генерации", ge=0.0, le=1.0)

class CompletionResponse(BaseModel):
    """Модель ответа генерации текста"""
    text: str = Field(..., description="Сгенерированный текст")
    model_uri: Optional[str] = Field(None, description="Использованная модель")
    tokens_used: Optional[int] = Field(None, description="Количество использованных токенов")

class EmbeddingRequest(BaseModel):
    """Модель запроса эмбеддинга"""
    text: str = Field(..., description="Текст для получения эмбеддинга", min_length=1)
    model_uri: Optional[str] = Field(None, description="URI модели эмбеддинга")

class EmbeddingResponse(BaseModel):
    """Модель ответа эмбеддинга"""
    embedding: List[float] = Field(..., description="Векторное представление текста")
    dimension: int = Field(..., description="Размерность вектора")
    model_uri: Optional[str] = Field(None, description="Использованная модель")

class BatchEmbeddingRequest(BaseModel):
    """Модель запроса пакетного эмбеддинга"""
    texts: List[str] = Field(..., description="Список текстов для эмбеддинга")
    model_uri: Optional[str] = Field(None, description="URI модели эмбеддинга")

class BatchEmbeddingResponse(BaseModel):
    """Модель ответа пакетного эмбеддинга"""
    embeddings: List[List[float]] = Field(..., description="Список векторных представлений")
    dimension: int = Field(..., description="Размерность векторов")
    count: int = Field(..., description="Количество обработанных текстов")
    model_uri: Optional[str] = Field(None, description="Использованная модель")

# ========================
# API эндпоинты
# ========================

@app.get("/")
async def root():
    """Корневой эндпоинт"""
    return {
        "service": "Yandex API Service",
        "version": "1.0.0",
        "status": "active",
        "available_endpoints": [
            "/completion",
            "/embedding",
            "/batch_embedding"
        ]
    }

@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    try:
        # Проверяем доступность API простым запросом
        test_embedding = yandex_text_embedding("test")

        return {
            "status": "healthy",
            "yandex_api": "available" if test_embedding else "unavailable",
            "embedding_dimension": len(test_embedding) if test_embedding else None
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e)
        }

@app.post("/completion", response_model=CompletionResponse)
async def generate_completion(request: CompletionRequest):
    """Генерация текста через Yandex GPT"""
    try:
        logger.info(f"Генерация текста, длина промпта: {len(request.prompt)}")

        response = yandex_completion(
            prompt=request.prompt,
            model_uri=request.model_uri,
            max_tokens=request.max_tokens,
            temperature=request.temperature
        )

        if not response or isinstance(response, dict) and response.get("error"):
            raise HTTPException(status_code=500, detail="Не удалось получить ответ от Yandex GPT")

        # Извлекаем текст из совместимого формата ответа
        text = extract_text_from_yandex_completion(response) if isinstance(response, dict) else str(response)
        if not text:
            raise HTTPException(status_code=500, detail="Пустой ответ модели")

        return CompletionResponse(
            text=text,
            model_uri=request.model_uri,
            tokens_used=None  # Yandex API не возвращает информацию о токенах
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка генерации текста: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка генерации: {str(e)}")

@app.post("/embedding", response_model=EmbeddingResponse)
async def create_embedding(request: EmbeddingRequest):
    """Создание эмбеддинга для текста"""
    try:
        logger.info(f"Создание эмбеддинга, длина текста: {len(request.text)}")

        embedding = yandex_text_embedding(request.text, request.model_uri)

        if not embedding:
            raise HTTPException(status_code=500, detail="Не удалось получить эмбеддинг")

        return EmbeddingResponse(
            embedding=embedding,
            dimension=len(embedding),
            model_uri=request.model_uri
        )

    except Exception as e:
        logger.error(f"Ошибка создания эмбеддинга: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка создания эмбеддинга: {str(e)}")

@app.post("/batch_embedding", response_model=BatchEmbeddingResponse)
async def create_batch_embeddings(request: BatchEmbeddingRequest):
    """Создание эмбеддингов для множества текстов"""
    try:
        logger.info(f"Создание пакетных эмбеддингов для {len(request.texts)} текстов")

        embeddings = yandex_batch_embeddings(request.texts, request.model_uri)

        if not embeddings or len(embeddings) != len(request.texts):
            raise HTTPException(status_code=500, detail="Не удалось получить все эмбеддинги")

        dimension = len(embeddings[0]) if embeddings else 0

        return BatchEmbeddingResponse(
            embeddings=embeddings,
            dimension=dimension,
            count=len(embeddings),
            model_uri=request.model_uri
        )

    except Exception as e:
        logger.error(f"Ошибка создания пакетных эмбеддингов: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка создания эмбеддингов: {str(e)}")

@app.get("/models")
async def get_available_models():
    """Получение списка доступных моделей"""
    # Базовая информация о моделях Yandex
    return {
        "completion_models": [
            {
                "name": "yandexgpt",
                "uri": "gpt://b1gvmob95yysaplct532/yandexgpt/latest",
                "description": "Базовая модель YandexGPT"
            },
            {
                "name": "yandexgpt-lite",
                "uri": "gpt://b1gvmob95yysaplct532/yandexgpt-lite/latest",
                "description": "Облегченная версия YandexGPT"
            }
        ],
        "embedding_models": [
            {
                "name": "text-search-query",
                "uri": "emb://b1gvmob95yysaplct532/text-search-query/latest",
                "description": "Модель для эмбеддинга поисковых запросов"
            },
            {
                "name": "text-search-doc",
                "uri": "emb://b1gvmob95yysaplct532/text-search-doc/latest",
                "description": "Модель для эмбеддинга документов"
            }
        ]
    }

# ========================
# Статистика и мониторинг
# ========================

# Простая статистика запросов
request_stats = {
    "completion_requests": 0,
    "embedding_requests": 0,
    "batch_embedding_requests": 0,
    "total_tokens_processed": 0,
    "total_texts_embedded": 0
}

@app.get("/stats")
async def get_stats():
    """Получение статистики использования сервиса"""
    return {
        "stats": request_stats,
        "uptime": "N/A"  # Можно добавить подсчет uptime
    }

# Middleware для подсчета статистики
@app.middleware("http")
async def stats_middleware(request, call_next):
    response = await call_next(request)

    # Обновляем статистику в зависимости от эндпоинта
    path = request.url.path
    if path == "/completion" and response.status_code == 200:
        request_stats["completion_requests"] += 1
    elif path == "/embedding" and response.status_code == 200:
        request_stats["embedding_requests"] += 1
        request_stats["total_texts_embedded"] += 1
    elif path == "/batch_embedding" and response.status_code == 200:
        request_stats["batch_embedding_requests"] += 1
        # Количество текстов нужно получить из запроса, пока просто увеличиваем на 1
        request_stats["total_texts_embedded"] += 1

    return response

# ========================
# Запуск приложения
# ========================

if __name__ == "__main__":
    host = os.getenv("YANDEX_SERVICE_HOST", "0.0.0.0")
    port = int(os.getenv("YANDEX_SERVICE_PORT", "8004"))

    logger.info(f"Запуск Yandex API Service на {host}:{port}")

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info"
    )
