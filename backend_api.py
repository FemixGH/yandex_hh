#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FastAPI Backend для ИИ Бармен проекта
Основные функции:
- Обработка запросов к барменскому ИИ
- Управление векторным индексом
- Модерация контента
- Работа с файлами в S3
- Инкрементальное обновление данных
"""

import asyncio
import logging
import os
from typing import List, Dict, Optional, Any
from datetime import datetime
import traceback

from fastapi import FastAPI, HTTPException, UploadFile, File, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import uvicorn

# Импорты модулей проекта
from rag_yandex_nofaiss import async_answer_user_query, load_vectorstore, build_index_from_bucket
from moderation_yandex import pre_moderate_input, post_moderate_output
from bartender_file_handler import build_bartender_index_from_bucket
from incremental_rag import update_rag_incremental
from yandex_api import yandex_completion, yandex_text_embedding, yandex_batch_embeddings
from faiss_index_yandex import semantic_search, build_index, load_index
from settings import VECTORSTORE_DIR, S3_BUCKET, S3_PREFIX, FOLDER_ID
from logging_conf import setup_logging

# Настройка логирования
setup_logging()
logger = logging.getLogger(__name__)

# Создание FastAPI приложения
app = FastAPI(
    title="ИИ Бармен API",
    description="API для взаимодействия с ИИ барменом и управления векторной базой знаний",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшене ограничить доменами
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========================
# Pydantic модели для API
# ========================

class BartenderQuery(BaseModel):
    """Модель запроса к барменскому ИИ"""
    query: str = Field(..., description="Запрос пользователя к бармену", min_length=1, max_length=1000)
    user_id: Optional[str] = Field(None, description="ID пользователя для персонализации")
    k: Optional[int] = Field(3, description="Количество релевантных документов для поиска", ge=1, le=10)
    with_moderation: Optional[bool] = Field(True, description="Включить модерацию запроса и ответа")

class BartenderResponse(BaseModel):
    """Модель ответа барменского ИИ"""
    answer: str = Field(..., description="Ответ бармена")
    blocked: bool = Field(False, description="Заблокирован ли ответ модерацией")
    reason: Optional[str] = Field(None, description="Причина блокировки")
    retrieved_count: int = Field(0, description="Количество найденных релевантных документов")
    processing_time: float = Field(0.0, description="Время обработки запроса в секундах")
    sources: Optional[List[str]] = Field(None, description="Источники информации")

class ModerationRequest(BaseModel):
    """Модель запроса модерации"""
    text: str = Field(..., description="Текст для модерации", min_length=1)
    is_input: bool = Field(True, description="True для входящего текста, False для исходяще��о")

class ModerationResponse(BaseModel):
    """Модель ответа модерации"""
    is_safe: bool = Field(..., description="Безопасен ли текст")
    reason: Optional[str] = Field(None, description="Причина блокировки")
    confidence: Optional[float] = Field(None, description="Уверенность в решении")

class EmbeddingRequest(BaseModel):
    """Модель запроса эмбеддинга"""
    text: str = Field(..., description="Текст для получения эмбеддинга", min_length=1)
    model_uri: Optional[str] = Field(None, description="URI модели эмбеддинга")

class EmbeddingResponse(BaseModel):
    """Модель ответа эмбеддинга"""
    embedding: List[float] = Field(..., description="Векторное представление текста")
    dimension: int = Field(..., description="Размерность вектора")

class SearchRequest(BaseModel):
    """Модель запроса поиска в векторной базе"""
    query: str = Field(..., description="Поисковый запрос", min_length=1)
    k: Optional[int] = Field(5, description="Количество результатов", ge=1, le=20)
    threshold: Optional[float] = Field(0.5, description="Порог релевантности", ge=0.0, le=1.0)

class SearchResult(BaseModel):
    """Модель результата поиска"""
    text: str = Field(..., description="Найденный текст")
    score: float = Field(..., description="Оценка релевантности")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Метаданные документа")

class SearchResponse(BaseModel):
    """Модель ответа поиска"""
    results: List[SearchResult] = Field(..., description="Результаты поиска")
    total_found: int = Field(..., description="Общее количество найденных результатов")
    processing_time: float = Field(..., description="Время обработки запроса")

class IndexStatus(BaseModel):
    """Модель статуса индекса"""
    exists: bool = Field(..., description="Существует ли индекс")
    documents_count: int = Field(0, description="Количество документов в индексе")
    last_updated: Optional[datetime] = Field(None, description="Время последнего обновления")
    size_mb: Optional[float] = Field(None, description="Размер индекса в МБ")

class IndexRebuildRequest(BaseModel):
    """Модель запроса перестройки индекса"""
    bucket: str = Field(S3_BUCKET, description="S3 бакет с файлами")
    prefix: str = Field(S3_PREFIX, description="Префикс файлов в бакете")
    force: bool = Field(False, description="Принудительная перестройка")

# ========================
# Глобальные переменные
# ========================
_vectorstore_cache = None
_last_index_load = None

# ========================
# Вспомогательные функции
# ========================

async def get_vectorstore():
    """Получает векторное хранилище с кешированием"""
    global _vectorstore_cache, _last_index_load

    try:
        # Проверяем, нужно ли перезагрузить индекс
        current_time = datetime.now()
        if (_vectorstore_cache is None or
            _last_index_load is None or
            (current_time - _last_index_load).total_seconds() > 3600):  # Обновляем каждый час

            logger.info("Загрузка векторного хранилища...")
            _vectorstore_cache = load_vectorstore()
            _last_index_load = current_time
            logger.info("Векторное хранилище загружено")

        return _vectorstore_cache
    except Exception as e:
        logger.error(f"Ошибка загрузки векторного хранилища: {e}")
        raise HTTPException(status_code=500, detail="Ошибка загрузки индекса")

def invalidate_vectorstore_cache():
    """Инвалидирует кеш векторного хранилища"""
    global _vectorstore_cache, _last_index_load
    _vectorstore_cache = None
    _last_index_load = None

# ========================
# API Эндпоинты
# ========================

@app.get("/")
async def root():
    """Корневой эндпоинт"""
    return {
        "service": "ИИ Бармен API",
        "version": "1.0.0",
        "status": "active",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    try:
        # Проверяем доступность векторного хранилища
        vectorstore = await get_vectorstore()
        matrix, docs = vectorstore

        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "vectorstore": {
                "loaded": True,
                "documents": len(docs) if docs else 0
            }
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
        )

@app.post("/bartender/ask", response_model=BartenderResponse)
async def ask_bartender(request: BartenderQuery):
    """Основной эндпоинт для общения с ИИ барменом"""
    start_time = datetime.now()

    try:
        logger.info(f"Получен запрос от пользователя {request.user_id}: {request.query}")

        # Вызываем асинхронную функцию ответа с правильными параметрами
        answer, meta = await async_answer_user_query(
            user_text=request.query,  # Исправлено: user_text вместо query
            user_id=int(request.user_id or 0),  # Исправлено: преобразуем в int
            k=request.k
        )

        processing_time = (datetime.now() - start_time).total_seconds()

        response = BartenderResponse(
            answer=answer,
            blocked=meta.get("blocked", False),
            reason=meta.get("reason"),
            retrieved_count=meta.get("retrieved_count", 0),
            processing_time=processing_time,
            sources=meta.get("sources", [])
        )

        logger.info(f"Ответ сформирован за {processing_time:.2f}s для пользователя {request.user_id}")
        return response

    except Exception as e:
        logger.error(f"Ошибка при обработке запроса: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка обработки запроса: {str(e)}")

@app.post("/moderation/check", response_model=ModerationResponse)
async def check_moderation(request: ModerationRequest):
    """Проверка текста на соответствие правилам модерации"""
    try:
        if request.is_input:
            is_safe, reason = pre_moderate_input(request.text)
        else:
            is_safe, reason = post_moderate_output(request.text)

        return ModerationResponse(
            is_safe=is_safe,
            reason=reason if not is_safe else None
        )

    except Exception as e:
        logger.error(f"Ошибка модерации: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка модерации: {str(e)}")

@app.post("/embeddings/create", response_model=EmbeddingResponse)
async def create_embedding(request: EmbeddingRequest):
    """Создание эмбеддинга для текста"""
    try:
        embedding = yandex_text_embedding(request.text, request.model_uri)

        if not embedding:
            raise HTTPException(status_code=500, detail="Не удалось получить эмбеддинг")

        return EmbeddingResponse(
            embedding=embedding,
            dimension=len(embedding)
        )

    except Exception as e:
        logger.error(f"Ошибка создания эмбеддинга: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка создания эмбеддинга: {str(e)}")

@app.post("/search/semantic", response_model=SearchResponse)
async def semantic_search_endpoint(request: SearchRequest):
    """Семантический поиск в векторной базе"""
    start_time = datetime.now()

    try:
        # Получаем эмбеддинг запроса
        query_embedding = yandex_text_embedding(request.query)
        if not query_embedding:
            raise HTTPException(status_code=500, detail="Не удалось получить эмбеддинг запроса")

        # Получаем векторное хранилище
        vectorstore = await get_vectorstore()
        matrix, docs = vectorstore

        # Выполняем поиск
        results = semantic_search(query_embedding, matrix, docs, k=request.k, threshold=request.threshold)

        search_results = []
        for doc, score in results:
            search_results.append(SearchResult(
                text=doc.get("content", ""),
                score=float(score),
                metadata=doc.get("metadata", {})
            ))

        processing_time = (datetime.now() - start_time).total_seconds()

        return SearchResponse(
            results=search_results,
            total_found=len(search_results),
            processing_time=processing_time
        )

    except Exception as e:
        logger.error(f"Ошибка семантического поиска: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка поиска: {str(e)}")

@app.get("/index/status", response_model=IndexStatus)
async def get_index_status():
    """Получение статуса векторного индекса"""
    try:
        # Проверяем существование файлов индекса
        vectors_file = os.path.join(VECTORSTORE_DIR, "vectors.npy")
        meta_file = os.path.join(VECTORSTORE_DIR, "meta.pkl")

        exists = os.path.exists(vectors_file) and os.path.exists(meta_file)

        documents_count = 0
        last_updated = None
        size_mb = None

        if exists:
            try:
                vectorstore = await get_vectorstore()
                matrix, docs = vectorstore
                documents_count = len(docs) if docs else 0

                # Получаем время последнего изменения
                if os.path.exists(vectors_file):
                    last_updated = datetime.fromtimestamp(os.path.getmtime(vectors_file))
                    size_mb = os.path.getsize(vectors_file) / (1024 * 1024)

            except Exception as e:
                logger.warning(f"Не удалось загрузить детали индекса: {e}")

        return IndexStatus(
            exists=exists,
            documents_count=documents_count,
            last_updated=last_updated,
            size_mb=size_mb
        )

    except Exception as e:
        logger.error(f"Ошибка получения статуса индекса: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка получения статуса: {str(e)}")

@app.post("/index/rebuild")
async def rebuild_index(request: IndexRebuildRequest, background_tasks: BackgroundTasks):
    """Перестройка векторного индекса из S3"""
    try:
        def rebuild_task():
            try:
                logger.info(f"Начинаем перестройку индекса из бакета {request.bucket}")
                build_bartender_index_from_bucket(request.bucket, request.prefix)
                invalidate_vectorstore_cache()  # Инвалидируем кеш
                logger.info("Перестройка индекса завершена")
            except Exception as e:
                logger.error(f"Ошибка при перестройке индекса: {e}")

        # Запускаем перестройку в фоне
        background_tasks.add_task(rebuild_task)

        return {
            "status": "started",
            "message": "Перестройка индекса запущена в фоновом режиме",
            "bucket": request.bucket,
            "prefix": request.prefix
        }

    except Exception as e:
        logger.error(f"Ошибка запуска перестройки индекса: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка запуска перестройки: {str(e)}")

@app.post("/index/update")
async def update_index_incremental(background_tasks: BackgroundTasks):
    """Инкрементальное обновление векторного индекса"""
    try:
        def update_task():
            try:
                logger.info("Начинаем инкрементальное обновление индекса")
                success = update_rag_incremental(S3_BUCKET)
                if success:
                    invalidate_vectorstore_cache()  # Инвалидируем кеш
                    logger.info("Инкрементальное обновление завершено успешно")
                else:
                    logger.warning("Инкрементальное обновление не удалось")
            except Exception as e:
                logger.error(f"Ошибка при инкрементальном обновлении: {e}")

        # Запускаем обновление в фоне
        background_tasks.add_task(update_task)

        return {
            "status": "started",
            "message": "Инкрементальное обновление запущено в фоновом режиме"
        }

    except Exception as e:
        logger.error(f"Ошибка запуска инкрементального обновления: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка запуска обновления: {str(e)}")

@app.post("/completion/generate")
async def generate_completion(
    prompt: str,
    model_uri: Optional[str] = None,
    max_tokens: Optional[int] = 2000,
    temperature: Optional[float] = 0.3
):
    """Генерация текста через Yandex GPT"""
    try:
        response = yandex_completion(
            prompt=prompt,
            model_uri=model_uri,
            max_tokens=max_tokens,
            temperature=temperature
        )

        return {
            "response": response,
            "model_uri": model_uri,
            "prompt_length": len(prompt)
        }

    except Exception as e:
        logger.error(f"Ошибка генерации текста: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка генерации: {str(e)}")

# ========================
# Обработчики ошибок
# ========================

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Обработчик HTTP исключений"""
    logger.error(f"HTTP {exc.status_code}: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "status_code": exc.status_code,
            "timestamp": datetime.now().isoformat()
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """Общий обработчик исключений"""
    logger.error(f"Необработанная ошибка: {exc}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={
            "error": "Внутренняя ошибка сервера",
            "timestamp": datetime.now().isoformat()
        }
    )

# ========================
# Запуск приложения
# ========================

if __name__ == "__main__":
    # Настройки для запуска
    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8000"))
    debug = os.getenv("API_DEBUG", "false").lower() == "true"

    logger.info(f"Запуск FastAPI сервера на {host}:{port}")

    uvicorn.run(
        "backend_api:app",
        host=host,
        port=port,
        reload=debug,
        log_level="info"
    )
