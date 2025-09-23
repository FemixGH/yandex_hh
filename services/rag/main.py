#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAG Service - сервис для векторного поиска и обработки документов
"""

import os
import logging
import traceback
from typing import List, Dict, Optional, Any
from datetime import datetime

import numpy as np
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
import uvicorn

# Импорты из оригинального проекта
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from rag_yandex_nofaiss import load_vectorstore, build_index_from_bucket
from faiss_index_yandex import semantic_search, build_index, load_index
from bartender_file_handler import build_bartender_index_from_bucket
from incremental_rag import update_rag_incremental
from settings import VECTORSTORE_DIR, S3_BUCKET, S3_PREFIX

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI приложение
app = FastAPI(
    title="RAG Service",
    description="Сервис для векторного поиска и RAG операций",
    version="1.0.0"
)

# ========================
# Pydantic модели
# ========================

class QueryRequest(BaseModel):
    """Модель запроса к RAG"""
    query: str = Field(..., description="Поисковый запрос")
    user_id: Optional[str] = Field(None, description="ID пользователя")
    k: Optional[int] = Field(3, description="Количество результатов", ge=1, le=10)

class QueryResponse(BaseModel):
    """Модель ответа RAG"""
    answer: str = Field(..., description="Сгенерированный ответ")
    retrieved_count: int = Field(0, description="Количество найденных документов")
    sources: List[str] = Field(default_factory=list, description="Источники")
    processing_time: float = Field(0.0, description="Время обработки")

class SearchRequest(BaseModel):
    """Модель запроса поиска"""
    query: str = Field(..., description="Поисковый запрос")
    k: Optional[int] = Field(5, description="Количество результатов", ge=1, le=20)
    threshold: Optional[float] = Field(0.5, description="Порог релевантности", ge=0.0, le=1.0)

class SearchResult(BaseModel):
    """Модель результата поиска"""
    text: str
    score: float
    metadata: Optional[Dict[str, Any]] = None

class SearchResponse(BaseModel):
    """Модель ответа поиска"""
    results: List[SearchResult]
    total_found: int
    processing_time: float

class IndexStatus(BaseModel):
    """Статус индекса"""
    exists: bool
    documents_count: int = 0
    last_updated: Optional[datetime] = None
    size_mb: Optional[float] = None

class IndexRebuildRequest(BaseModel):
    """Запрос перестройки индекса"""
    bucket: str = Field(S3_BUCKET, description="S3 бакет")
    prefix: str = Field(S3_PREFIX, description="Префикс файлов")
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
        current_time = datetime.now()
        if (_vectorstore_cache is None or
            _last_index_load is None or
            (current_time - _last_index_load).total_seconds() > 3600):

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
# API эндпоинты
# ========================

@app.get("/")
async def root():
    """Корне��ой эндпоинт"""
    return {
        "service": "RAG Service",
        "version": "1.0.0",
        "status": "active"
    }

@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    try:
        vectorstore = await get_vectorstore()
        matrix, docs = vectorstore

        return {
            "status": "healthy",
            "vectorstore": {
                "loaded": True,
                "documents": len(docs) if docs else 0
            }
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e)
        }

@app.post("/answer", response_model=QueryResponse)
async def generate_answer(request: QueryRequest):
    """Генерация ответа с использованием RAG"""
    start_time = datetime.now()

    try:
        logger.info(f"Получен запрос: {request.query}")

        # Импорт функции для ответа (избегаем циклических импортов)
        from rag_yandex_nofaiss import async_answer_user_query

        # Вызываем функцию ответа
        answer, meta = await async_answer_user_query(
            user_text=request.query,
            user_id=int(request.user_id or 0),
            k=request.k
        )

        processing_time = (datetime.now() - start_time).total_seconds()

        response = QueryResponse(
            answer=answer,
            retrieved_count=meta.get("retrieved_count", 0),
            sources=meta.get("sources", []),
            processing_time=processing_time
        )

        logger.info(f"Ответ сформирован за {processing_time:.2f}s")
        return response

    except Exception as e:
        logger.error(f"Ошибка генерации ответа: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка генерации ответа: {str(e)}")

@app.post("/search", response_model=SearchResponse)
async def semantic_search_endpoint(request: SearchRequest):
    """Семантический поиск в векторной базе"""
    start_time = datetime.now()

    try:
        # Импорт функций для эмбеддингов
        from yandex_api import yandex_text_embedding

        # Получаем эмбеддинг запроса
        query_embedding = yandex_text_embedding(request.query)
        if not query_embedding:
            raise HTTPException(status_code=500, detail="Не удалось получить эмбеддинг запроса")

        # Получаем векторное хранилище
        vectorstore = await get_vectorstore()
        matrix, docs = vectorstore

        # Выполняем поиск
        results = semantic_search(
            query_embedding,
            matrix,
            docs,
            k=request.k,
            threshold=request.threshold
        )

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
                invalidate_vectorstore_cache()
                logger.info("Перестройка индекса завершена")
            except Exception as e:
                logger.error(f"Ошибка при перестройке индекса: {e}")

        background_tasks.add_task(rebuild_task)

        return {
            "status": "started",
            "message": "Перестройка индекса запущена в фоновом режиме",
            "bucket": request.bucket,
            "prefix": request.prefix
        }

    except Exception as e:
        logger.error(f"Ошибка запуск�� перестройки индекса: {e}")
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
                    invalidate_vectorstore_cache()
                    logger.info("Инкрементальное обновление завершено успешно")
                else:
                    logger.warning("��нкрементальное обновление не удалось")
            except Exception as e:
                logger.error(f"Ошибка при инкрементальном обновлении: {e}")

        background_tasks.add_task(update_task)

        return {
            "status": "started",
            "message": "Инкрементальное обновление запущено в фоновом режиме"
        }

    except Exception as e:
        logger.error(f"Ошибка запуска инкрементального обновления: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка запуска обновления: {str(e)}")

# ========================
# Запуск приложения
# ========================

if __name__ == "__main__":
    host = os.getenv("RAG_SERVICE_HOST", "0.0.0.0")
    port = int(os.getenv("RAG_SERVICE_PORT", "8002"))

    logger.info(f"Запуск RAG Service на {host}:{port}")

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info"
    )
