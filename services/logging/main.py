#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Logging Service - централизованное логирование для всех микросервисов
"""

import os
import logging
import json
from datetime import datetime
from typing import List, Optional, Dict, Any
from enum import Enum

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import uvicorn

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI приложение
app = FastAPI(
    title="Logging Service",
    description="Централизованный сервис логирования для всех микросервисов",
    version="1.0.0"
)

# ========================
# Модели данных
# ========================

class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

class LogEntry(BaseModel):
    """Модель записи лога"""
    level: LogLevel = Field(..., description="Уровень логирования")
    message: str = Field(..., description="Сообщение лога")
    service: str = Field(..., description="Название сервиса")
    timestamp: Optional[datetime] = Field(None, description="Время создания записи")
    user_id: Optional[str] = Field(None, description="ID пользователя")
    request_id: Optional[str] = Field(None, description="ID запроса")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Дополнительные метаданные")

class LogResponse(BaseModel):
    """Ответ на запрос логирования"""
    success: bool
    log_id: str
    timestamp: datetime

class LogQuery(BaseModel):
    """Модель запроса логов"""
    service: Optional[str] = Field(None, description="Фильтр по сервису")
    level: Optional[LogLevel] = Field(None, description="Фильтр по уровню")
    start_time: Optional[datetime] = Field(None, description="Начальное время")
    end_time: Optional[datetime] = Field(None, description="Конечное время")
    user_id: Optional[str] = Field(None, description="Фильтр по пользователю")
    limit: int = Field(100, description="Лимит записей", ge=1, le=1000)

# ========================
# Хранилище логов в памяти
# ========================

class LogStorage:
    """Простое хранилище логов в памяти (для продакшена нужна БД)"""

    def __init__(self, max_entries: int = 10000):
        self.logs: List[Dict] = []
        self.max_entries = max_entries
        self._counter = 0

    def add_log(self, entry: LogEntry) -> str:
        """Добавление записи в лог"""
        self._counter += 1
        log_id = f"log_{self._counter}"

        log_dict = {
            "id": log_id,
            "level": entry.level.value,
            "message": entry.message,
            "service": entry.service,
            "timestamp": entry.timestamp or datetime.now(),
            "user_id": entry.user_id,
            "request_id": entry.request_id,
            "metadata": entry.metadata or {}
        }

        self.logs.append(log_dict)

        # Ограничиваем размер хранилища
        if len(self.logs) > self.max_entries:
            self.logs = self.logs[-self.max_entries:]

        # Логируем в стандартный logger
        python_logger = logging.getLogger(entry.service)
        log_level = getattr(logging, entry.level.value)
        python_logger.log(log_level, f"[{entry.service}] {entry.message}", extra={
            "user_id": entry.user_id,
            "request_id": entry.request_id,
            "metadata": entry.metadata
        })

        return log_id

    def query_logs(self, query: LogQuery) -> List[Dict]:
        """Поиск логов по критериям"""
        filtered_logs = self.logs.copy()

        # Фильтрация по сервису
        if query.service:
            filtered_logs = [log for log in filtered_logs if log["service"] == query.service]

        # Фильтрация по уровню
        if query.level:
            filtered_logs = [log for log in filtered_logs if log["level"] == query.level.value]

        # Фильтрация по времени
        if query.start_time:
            filtered_logs = [log for log in filtered_logs if log["timestamp"] >= query.start_time]

        if query.end_time:
            filtered_logs = [log for log in filtered_logs if log["timestamp"] <= query.end_time]

        # Фильтрация по пользователю
        if query.user_id:
            filtered_logs = [log for log in filtered_logs if log["user_id"] == query.user_id]

        # Сортировка по времени (новые сначала)
        filtered_logs.sort(key=lambda x: x["timestamp"], reverse=True)

        # Лимит
        return filtered_logs[:query.limit]

    def get_stats(self) -> Dict[str, Any]:
        """Получение статистики логов"""
        if not self.logs:
            return {
                "total_logs": 0,
                "by_service": {},
                "by_level": {},
                "last_log_time": None
            }

        stats = {
            "total_logs": len(self.logs),
            "by_service": {},
            "by_level": {},
            "last_log_time": max(log["timestamp"] for log in self.logs)
        }

        # Подсчет по сервисам
        for log in self.logs:
            service = log["service"]
            stats["by_service"][service] = stats["by_service"].get(service, 0) + 1

        # Подсчет по уровням
        for log in self.logs:
            level = log["level"]
            stats["by_level"][level] = stats["by_level"].get(level, 0) + 1

        return stats

# Глобальное хранилище
log_storage = LogStorage()

# ========================
# API эндпоинты
# ========================

@app.get("/")
async def root():
    """Корневой эндпоинт"""
    return {
        "service": "Logging Service",
        "version": "1.0.0",
        "status": "active",
        "total_logs": len(log_storage.logs)
    }

@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    return {
        "status": "healthy",
        "storage": "memory",
        "logs_count": len(log_storage.logs),
        "last_log": log_storage.logs[-1]["timestamp"] if log_storage.logs else None
    }

@app.post("/log", response_model=LogResponse)
async def add_log(entry: LogEntry):
    """Добавление записи в лог"""
    try:
        log_id = log_storage.add_log(entry)

        return LogResponse(
            success=True,
            log_id=log_id,
            timestamp=datetime.now()
        )

    except Exception as e:
        logger.error(f"Ошибка добавления лога: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка логирования: {str(e)}")

@app.post("/query")
async def query_logs(query: LogQuery):
    """Поиск логов по критериям"""
    try:
        logs = log_storage.query_logs(query)

        return {
            "logs": logs,
            "count": len(logs),
            "query": query.dict()
        }

    except Exception as e:
        logger.error(f"Ошибка поиска логов: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка поиска: {str(e)}")

@app.get("/stats")
async def get_stats():
    """Получение статистики логирования"""
    try:
        return log_storage.get_stats()
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/services")
async def get_services():
    """Получение списка сервисов, которые логируются"""
    services = set()
    for log in log_storage.logs:
        services.add(log["service"])

    return {
        "services": list(services),
        "count": len(services)
    }

@app.delete("/clear")
async def clear_logs(service: Optional[str] = None):
    """Очистка логов (всех или конкретного сервиса)"""
    try:
        if service:
            log_storage.logs = [log for log in log_storage.logs if log["service"] != service]
            message = f"Логи сервиса {service} очищены"
        else:
            log_storage.logs.clear()
            message = "Все логи очищены"

        return {
            "success": True,
            "message": message,
            "remaining_logs": len(log_storage.logs)
        }

    except Exception as e:
        logger.error(f"Ошибка очистки логов: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ========================
# Экспорт логов
# ========================

@app.get("/export")
async def export_logs(format: str = "json", service: Optional[str] = None):
    """Экспорт логов в различных форматах"""
    try:
        logs = log_storage.logs

        if service:
            logs = [log for log in logs if log["service"] == service]

        if format.lower() == "json":
            return {
                "format": "json",
                "logs": logs,
                "count": len(logs),
                "exported_at": datetime.now()
            }
        elif format.lower() == "csv":
            # Простой CSV формат
            csv_lines = ["timestamp,level,service,message,user_id"]
            for log in logs:
                line = f"{log['timestamp']},{log['level']},{log['service']},\"{log['message']}\",{log['user_id'] or ''}"
                csv_lines.append(line)

            return {
                "format": "csv",
                "data": "\n".join(csv_lines),
                "count": len(logs)
            }
        else:
            raise HTTPException(status_code=400, detail="Поддерживаемые форматы: json, csv")

    except Exception as e:
        logger.error(f"Ошибка экспорта логов: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ========================
# Запуск приложения
# ========================

if __name__ == "__main__":
    host = os.getenv("LOGGING_SERVICE_HOST", "0.0.0.0")
    port = int(os.getenv("LOGGING_SERVICE_PORT", "8005"))

    logger.info(f"Запуск Logging Service на {host}:{port}")

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info"
    )
