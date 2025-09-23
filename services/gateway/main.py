#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gateway Service - единый входной бэкенд для всех микросервисов
Координирует взаимодействие между сервисами
"""

import asyncio
import logging
import os
import traceback
from typing import List, Dict, Optional
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import uvicorn

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Создание FastAPI приложения
app = FastAPI(
    title="ИИ Бармен Gateway API",
    description="Единый входной API для всех микросервисов ИИ Бармена",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========================
# Конфигурация сервисов
# ========================

SERVICES_CONFIG = {
    "telegram": {
        "url": os.getenv("TELEGRAM_SERVICE_URL", "http://telegram-service:8001"),
        "timeout": 30.0
    },
    "rag": {
        "url": os.getenv("RAG_SERVICE_URL", "http://rag-service:8002"),
        "timeout": 60.0
    },
    "validation": {
        "url": os.getenv("VALIDATION_SERVICE_URL", "http://validation-service:8003"),
        "timeout": 10.0
    },
    "yandex": {
        "url": os.getenv("YANDEX_SERVICE_URL", "http://yandex-service:8004"),
        "timeout": 30.0
    },
    "logging": {
        "url": os.getenv("LOGGING_SERVICE_URL", "http://logging-service:8005"),
        "timeout": 5.0
    }
}

# При необходимости включаем lockbox сервис в маршрутизацию и health-check
if os.getenv("EXPOSE_LOCKBOX_PROXY", "false").lower() == "true":
    SERVICES_CONFIG["lockbox"] = {
        "url": os.getenv("LOCKBOX_SERVICE_URL", "http://lockbox-service:8006"),
        "timeout": 15.0
    }

# ========================
# Pydantic модели
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

class ServiceHealthStatus(BaseModel):
    """Статус здоровья сервиса"""
    name: str
    status: str  # "healthy", "unhealthy", "unknown"
    response_time: Optional[float] = None
    error: Optional[str] = None

class SystemHealthResponse(BaseModel):
    """Общий статус системы"""
    status: str
    services: List[ServiceHealthStatus]
    timestamp: datetime

# ========================
# HTTP клиент для сервисов
# ========================

class ServiceClient:
    def __init__(self):
        self.client = httpx.AsyncClient()

    async def call_service(self, service_name: str, endpoint: str, method: str = "GET",
                          data: Optional[Dict] = None, params: Optional[Dict] = None,
                          headers: Optional[Dict[str, str]] = None) -> Dict:
        """Вызов микросервиса"""
        if service_name not in SERVICES_CONFIG:
            raise HTTPException(status_code=500, detail=f"Неизвестный сервис: {service_name}")

        config = SERVICES_CONFIG[service_name]
        url = f"{config['url']}{endpoint}"

        try:
            if method.upper() == "GET":
                response = await self.client.get(url, params=params, headers=headers, timeout=config["timeout"])
            elif method.upper() == "POST":
                response = await self.client.post(url, json=data, params=params, headers=headers, timeout=config["timeout"])
            elif method.upper() == "PUT":
                response = await self.client.put(url, json=data, params=params, headers=headers, timeout=config["timeout"])
            elif method.upper() == "DELETE":
                response = await self.client.delete(url, params=params, headers=headers, timeout=config["timeout"])
            else:
                raise HTTPException(status_code=400, detail=f"Неподдерживаемый HTTP метод: {method}")

            response.raise_for_status()
            return response.json()

        except httpx.TimeoutException:
            logger.error(f"Таймаут при обращении к сервису {service_name}")
            raise HTTPException(status_code=504, detail=f"Таймаут сервиса {service_name}")
        except httpx.HTTPError as e:
            logger.error(f"HTTP ошибка при обращении к сервису {service_name}: {e}")
            raise HTTPException(status_code=503, detail=f"Сервис {service_name} недоступен")
        except Exception as e:
            logger.error(f"Ошибка при обращении к сервису {service_name}: {e}")
            raise HTTPException(status_code=500, detail=f"Ошибка вызова сервиса {service_name}")

    async def check_service_health(self, service_name: str) -> ServiceHealthStatus:
        """Проверка здоровья сервиса"""
        start_time = datetime.now()

        try:
            await self.call_service(service_name, "/health")
            response_time = (datetime.now() - start_time).total_seconds()

            return ServiceHealthStatus(
                name=service_name,
                status="healthy",
                response_time=response_time
            )
        except Exception as e:
            return ServiceHealthStatus(
                name=service_name,
                status="unhealthy",
                error=str(e)
            )

# Глобальный клиент
service_client = ServiceClient()

# Безопасное логирование: ошибки логов не должны ломать основной поток
async def safe_log(level: str, message: str, service: str = "gateway", **extra):
    try:
        await service_client.call_service(
            "logging", "/log", "POST",
            data={
                "level": level,
                "message": message,
                "service": service,
                **({"user_id": extra.get("user_id")} if extra.get("user_id") is not None else {})
            }
        )
    except Exception as e:
        logger.warning(f"Логирование недоступно: {e}")

# ========================
# API Эндпоинты
# ========================

@app.get("/")
async def root():
    """Корневой эндпоинт"""
    return {
        "service": "ИИ Бармен Gateway API",
        "version": "1.0.0",
        "status": "active",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/health", response_model=SystemHealthResponse)
async def health_check():
    """Проверка здоровья всей системы"""
    start_time = datetime.now()

    # Проверяем все сервисы параллельно
    health_tasks = []
    for service_name in SERVICES_CONFIG.keys():
        health_tasks.append(service_client.check_service_health(service_name))

    services_health = await asyncio.gather(*health_tasks, return_exceptions=True)

    # Обрабатываем результаты
    service_statuses: List[ServiceHealthStatus] = []

    for i, result in enumerate(services_health):
        service_name = list(SERVICES_CONFIG.keys())[i]

        if isinstance(result, Exception):
            service_statuses.append(ServiceHealthStatus(
                name=service_name,
                status="unknown",
                error=str(result)
            ))
        else:
            # Явно приводим к нужному типу (устранение предупреждений типизации)
            service_statuses.append(ServiceHealthStatus(**result.dict()))

    overall_status = "healthy"
    for s in service_statuses:
        if s.status != "healthy":
            overall_status = "degraded"
            break

    return SystemHealthResponse(
        status=overall_status,
        services=service_statuses,
        timestamp=datetime.now()
    )

@app.post("/bartender/ask", response_model=BartenderResponse)
async def ask_bartender(request: BartenderQuery):
    """Основной эндпоинт для общения с ИИ барменом"""
    start_time = datetime.now()

    try:
        # Не блокируем основной поток, если logging недоступен
        await safe_log("INFO", f"Получен запрос от пользователя {request.user_id}: {request.query}", user_id=request.user_id)

        # Модерация входящего запроса
        if request.with_moderation:
            moderation_result = await service_client.call_service(
                "validation", "/moderate", "POST",
                data={"text": request.query, "is_input": True}
            )

            if not moderation_result.get("is_safe", True):
                processing_time = (datetime.now() - start_time).total_seconds()
                return BartenderResponse(
                    answer="Извините, ваш запрос не прошел модерацию. Пожалуйста, перефразируйте вопрос.",
                    blocked=True,
                    reason=moderation_result.get("reason", "Нарушение правил"),
                    retrieved_count=0,
                    processing_time=processing_time,
                    sources=[]
                )

        # Получаем ответ от RAG сервиса
        rag_response = await service_client.call_service(
            "rag", "/answer", "POST",
            data={
                "query": request.query,
                "user_id": request.user_id,
                "k": request.k
            }
        )

        answer = rag_response.get("answer", "")

        # Модерация исходящего ответа
        if request.with_moderation and answer:
            moderation_result = await service_client.call_service(
                "validation", "/moderate", "POST",
                data={"text": answer, "is_input": False}
            )

            if not moderation_result.get("is_safe", True):
                processing_time = (datetime.now() - start_time).total_seconds()
                return BartenderResponse(
                    answer="Извините, сгенерированный ответ не прошел модерацию.",
                    blocked=True,
                    reason=moderation_result.get("reason", "Нарушение правил"),
                    retrieved_count=rag_response.get("retrieved_count", 0),
                    processing_time=processing_time,
                    sources=rag_response.get("sources", [])
                )

        processing_time = (datetime.now() - start_time).total_seconds()

        response = BartenderResponse(
            answer=answer,
            blocked=False,
            reason=None,
            retrieved_count=rag_response.get("retrieved_count", 0),
            processing_time=processing_time,
            sources=rag_response.get("sources", [])
        )

        # Безопасное логирование успешного ответа
        await safe_log("INFO", f"Ответ сформирован за {processing_time:.2f}s для пользователя {request.user_id}", user_id=request.user_id)
        return response

    except Exception as e:
        processing_time = (datetime.now() - start_time).total_seconds()
        # Пытаемся залогировать ошибку, но не ломаем ответ, если logging недоступен
        await safe_log("ERROR", f"Ошибка при обработке запроса: {str(e)}", user_id=request.user_id)
        logger.error(f"Ошибка при обработке запроса: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка обработки запроса: {str(e)}")

# Проксирование запросов к сервисам
@app.api_route("/telegram/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def telegram_proxy(path: str, request):
    """Проксирование запросов к Telegram сервису"""
    method = request.method
    params = dict(request.query_params)

    if method in ["POST", "PUT"]:
        try:
            data = await request.json()
        except:
            data = None
    else:
        data = None

    # Пробрасываем секрет вебхука, если есть
    fwd_headers = {}
    secret = request.headers.get("x-telegram-bot-api-secret-token") or request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret:
        fwd_headers["X-Telegram-Bot-Api-Secret-Token"] = secret

    return await service_client.call_service("telegram", f"/{path}", method, data, params, headers=fwd_headers or None)

@app.api_route("/rag/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def rag_proxy(path: str, request):
    """Проксирование запросов к RAG сервису"""
    method = request.method
    params = dict(request.query_params)

    if method in ["POST", "PUT"]:
        try:
            data = await request.json()
        except:
            data = None
    else:
        data = None

    return await service_client.call_service("rag", f"/{path}", method, data, params)

@app.api_route("/validation/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def validation_proxy(path: str, request):
    """Проксирование запросов к Validation сервису"""
    method = request.method
    params = dict(request.query_params)

    if method in ["POST", "PUT"]:
        try:
            data = await request.json()
        except:
            data = None
    else:
        data = None

    return await service_client.call_service("validation", f"/{path}", method, data, params)

@app.api_route("/yandex/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def yandex_proxy(path: str, request):
    """Проксирование запросов к Yandex сервису"""
    method = request.method
    params = dict(request.query_params)

    if method in ["POST", "PUT"]:
        try:
            data = await request.json()
        except:
            data = None
    else:
        data = None

    return await service_client.call_service("yandex", f"/{path}", method, data, params)

# Проксирование запросов к Lockbox сервису (по умолчанию отключено для безопасности)
if os.getenv("EXPOSE_LOCKBOX_PROXY", "false").lower() == "true":
    @app.api_route("/lockbox/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def lockbox_proxy(path: str, request):
        """Проксирование запросов к Lockbox сервису (только при явном включении)"""
        method = request.method
        params = dict(request.query_params)

        if method in ["POST", "PUT"]:
            try:
                data = await request.json()
            except:
                data = None
        else:
            data = None

        return await service_client.call_service("lockbox", f"/{path}", method, data, params)

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
    host = os.getenv("GATEWAY_HOST", "0.0.0.0")
    port = int(os.getenv("GATEWAY_PORT", "8000"))

    logger.info(f"Запуск Gateway сервера на {host}:{port}")

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info"
    )
