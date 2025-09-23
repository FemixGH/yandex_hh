#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yandex Lockbox Service - сервис для работы с Yandex Lockbox
Получение и управление секретами из Yandex Cloud Lockbox
"""

import os
import json
import urllib.request
import urllib.error
import logging
import traceback
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Depends, status
from pydantic import BaseModel, Field
import uvicorn

# Импортируем при возможности обмен JWT->IAM из общего модуля проекта
try:
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from yandex_jwt_auth import get_iam_token as jwt_to_iam
except Exception:
    jwt_to_iam = None

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI приложение
app = FastAPI(
    title="Yandex Lockbox Service",
    description="Сервис для получения секретов из Yandex Cloud Lockbox",
    version="1.0.0"
)

# ========================
# Pydantic модели
# ========================

class SecretRequest(BaseModel):
    """Модель запроса получения секрета"""
    secret_id: str = Field(..., description="ID секрета в Lockbox", min_length=1)
    version_id: Optional[str] = Field(None, description="ID версии секрета (по умолчанию - текущая)")

class SecretEntry(BaseModel):
    """Модель записи секрета"""
    key: str = Field(..., description="Ключ секрета")
    text_value: Optional[str] = Field(None, description="Текстовое значение")
    binary_value: Optional[bytes] = Field(None, description="Бинарное значение")

class SecretResponse(BaseModel):
    """Модель ответа с секретом"""
    secret_id: str = Field(..., description="ID секрета")
    version_id: str = Field(..., description="ID версии")
    entries: List[SecretEntry] = Field(..., description="Записи секрета")
    retrieved_at: str = Field(..., description="Время получения секрета")

class SecretKeyValue(BaseModel):
    """Модель ключ-значение для удобного доступа"""
    secrets: Dict[str, str] = Field(..., description="Словарь секретов ключ -> значение")

class HealthResponse(BaseModel):
    """Модель ответа проверки здоровья"""
    status: str = Field(..., description="Статус сервиса")
    lockbox_api: str = Field(..., description="Доступность Lockbox API")
    iam_token_valid: bool = Field(..., description="Валидность IAM токена")

# ========================
# Утилиты для работы с IAM
# ========================

class IAMTokenManager:
    """Менеджер для работы с IAM токенами"""

    def __init__(self):
        self._token = None
        self._token_expires_at = None

    def get_iam_token(self) -> str:
        """Получает действующий IAM токен"""
        # Сначала проверяем переменные окружения
        token_from_env = os.environ.get('IAM_TOKEN')
        if token_from_env:
            logger.info("Используется IAM токен из переменной окружения")
            return token_from_env

        # Проверяем метаданные сервиса (для Compute/Serverless)
        token_from_metadata = self._get_token_from_metadata()
        if token_from_metadata:
            return token_from_metadata

        # Резервно: пробуем обменять JWT на IAM через общий модуль
        if jwt_to_iam is not None:
            try:
                logger.info("Получение IAM токена через yandex_jwt_auth")
                return jwt_to_iam()
            except Exception as e:
                logger.warning(f"Не удалось получить IAM токен через yandex_jwt_auth: {e}")

        # Если нет токена - ошибка
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="IAM токен не найден. Установите IAM_TOKEN, назначьте сервисный аккаунт или настройте JWT-обмен"
        )

    def _get_token_from_metadata(self) -> Optional[str]:
        """Получает IAM токен из метаданных окружения (VM/Serverless)"""
        # Исторически в YC используется GCE-совместимый метадата-сервис
        # Если он недоступен локально, вернем None
        for url in [
            "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token",
            "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/identity"
        ]:
            try:
                req = urllib.request.Request(url)
                req.add_header("Metadata-Flavor", "Google")
                with urllib.request.urlopen(req, timeout=3) as response:
                    token_data = json.load(response)
                    access_token = token_data.get('access_token') or token_data.get('token')
                    expires_in = token_data.get('expires_in', 3600)
                    if access_token:
                        self._token = access_token
                        self._token_expires_at = datetime.now() + timedelta(seconds=int(expires_in) - 300)
                        logger.info("IAM токен получен из метаданных окружения")
                        return access_token
            except Exception:
                continue
        return None

# Глобальный экземпляр менеджера токенов
iam_manager = IAMTokenManager()

# ========================
# Основные функции для работы с Lockbox
# ========================

def get_secret_payload(secret_id: str, iam_token: str, version_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Получает секрет из Yandex Lockbox по его ID

    Args:
        secret_id: ID секрета в Lockbox
        iam_token: IAM токен для авторизации
        version_id: ID версии секрета (опционально)

    Returns:
        Словарь с данными секрета
    """
    # Формируем URL
    url = f'https://payload.lockbox.api.cloud.yandex.net/lockbox/v1/secrets/{secret_id}/payload'
    if version_id:
        url += f'?versionId={version_id}'

    # Заголовки запроса
    headers = {
        'Authorization': f'Bearer {iam_token}',
        'Content-Type': 'application/json'
    }

    # Выполняем запрос
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode('utf-8')
        except Exception:
            pass

        if e.code == 403:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Нет доступа к секрету {secret_id}. Проверьте права сервисного аккаунта"
            )
        elif e.code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Секрет {secret_id} не найден"
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Ошибка Lockbox API: {e.code} {e.reason}. {error_body}"
            )
    except urllib.error.URLError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка сети при запросе к Lockbox: {e}"
        )
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка парсинга JSON от Lockbox: {e}"
        )

def parse_secret_payload(payload: Dict[str, Any]) -> Dict[str, str]:
    """
    Преобразует payload секрета в удобный словарь ключ -> значение
    """
    secrets = {}
    entries = payload.get('entries', [])

    for entry in entries:
        key = entry.get('key') or entry.get('Key')
        # поддерживаем оба варианта ключа для textValue/text_value
        text_value = entry.get('textValue')
        if text_value is None:
            text_value = entry.get('text_value')

        if key and text_value is not None:
            secrets[key] = text_value
        elif key:
            binary_value = entry.get('binaryValue') or entry.get('binary_value')
            if binary_value:
                secrets[key] = f"<binary_data_{len(binary_value)}_bytes>"

    return secrets

# ========================
# Dependency injection
# ========================

async def get_iam_token() -> str:
    """Dependency для получения IAM токена"""
    return iam_manager.get_iam_token()

# ========================
# API эндпоинты
# ========================

@app.get("/")
async def root():
    """Корневой эндпоинт"""
    return {
        "service": "Yandex Lockbox Service",
        "version": "1.0.0",
        "status": "active",
        "available_endpoints": [
            "/secret",
            "/secret/kv",
            "/secret/{secret_id}/kv",
            "/health"
        ]
    }

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Проверка здоровья сервиса"""
    try:
        iam_token = None
        iam_token_valid = False
        try:
            iam_token = iam_manager.get_iam_token()
            iam_token_valid = bool(iam_token)
        except Exception:
            iam_token_valid = False

        lockbox_api_status = "unknown"
        if iam_token:
            try:
                test_url = 'https://payload.lockbox.api.cloud.yandex.net/lockbox/v1/secrets/test-health-check/payload'
                req = urllib.request.Request(test_url, headers={'Authorization': f'Bearer {iam_token}'})
                try:
                    urllib.request.urlopen(req, timeout=5)
                    lockbox_api_status = "available"
                except urllib.error.HTTPError as e:
                    if e.code in [404, 403]:
                        lockbox_api_status = "available"
                    else:
                        lockbox_api_status = f"error_{e.code}"
                except urllib.error.URLError:
                    lockbox_api_status = "unavailable"
            except Exception as e:
                logger.error(f"Ошибка проверки Lockbox API: {e}")
                lockbox_api_status = "error"
        else:
            lockbox_api_status = "unknown"

        return HealthResponse(
            status="healthy" if iam_token_valid and lockbox_api_status == "available" else "unhealthy",
            lockbox_api=lockbox_api_status,
            iam_token_valid=iam_token_valid
        )

    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return HealthResponse(
            status="unhealthy",
            lockbox_api="error",
            iam_token_valid=False
        )

@app.post("/secret", response_model=SecretResponse)
async def get_secret(request: SecretRequest, iam_token: str = Depends(get_iam_token)):
    """Получение секрета из Lockbox (полная структура)"""
    try:
        logger.info(f"Запрос секрета: {request.secret_id}")
        payload = get_secret_payload(request.secret_id, iam_token, request.version_id)
        entries = []
        for entry in payload.get('entries', []):
            entries.append(SecretEntry(
                key=entry.get('key', ''),
                text_value=entry.get('textValue') or entry.get('text_value'),
                binary_value=entry.get('binaryValue') or entry.get('binary_value')
            ))
        return SecretResponse(
            secret_id=request.secret_id,
            version_id=payload.get('versionId', payload.get('version_id', 'current')),
            entries=entries,
            retrieved_at=datetime.now().isoformat()
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка получения секрета {request.secret_id}: {e}\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Внутренняя ошибка при получении секрета: {str(e)}"
        )

@app.post("/secret/kv", response_model=SecretKeyValue)
async def get_secret_key_value(request: SecretRequest, iam_token: str = Depends(get_iam_token)):
    """Получение секрета в формате ключ-значение (удобно для использования)"""
    try:
        logger.info(f"Запрос секрета (kv): {request.secret_id}")
        payload = get_secret_payload(request.secret_id, iam_token, request.version_id)
        secrets = parse_secret_payload(payload)
        return SecretKeyValue(secrets=secrets)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка получения секрета (kv) {request.secret_id}: {e}\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Внутренняя ошибка при получении секрета: {str(e)}"
        )

@app.get("/secret/{secret_id}/kv")
async def get_secret_by_id(secret_id: str, iam_token: str = Depends(get_iam_token)):
    """Получение секрета по ID через GET запрос (удобно для быстрого доступа)"""
    request = SecretRequest(secret_id=secret_id, version_id=None)
    result = await get_secret_key_value(request, iam_token)
    return result.secrets

@app.get("/secret/default/kv")
async def get_default_secret_kv(iam_token: str = Depends(get_iam_token)):
    """Получение секрета из переменной окружения SECRET_ID (для быстрого теста)"""
    secret_id = os.getenv("SECRET_ID")
    if not secret_id:
        raise HTTPException(status_code=400, detail="SECRET_ID не задан в переменных окружения")
    request = SecretRequest(secret_id=secret_id, version_id=None)
    result = await get_secret_key_value(request, iam_token)
    return result.secrets

# ========================
# Запуск сервиса
# ========================

if __name__ == "__main__":
    # Поддерживаем как HOST/PORT, так и LOCKBOX_SERVICE_HOST/PORT
    host = os.environ.get("LOCKBOX_SERVICE_HOST") or os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("LOCKBOX_SERVICE_PORT") or os.environ.get("PORT", 8006))

    logger.info(f"Запуск Yandex Lockbox Service на {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
