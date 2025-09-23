#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Validation Service - сервис валидации и модерации контента
"""

import os
import logging
import traceback
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import uvicorn

# Импорты из оригинального проекта
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from moderation_yandex import pre_moderate_input, post_moderate_output

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI приложение
app = FastAPI(
    title="Validation Service",
    description="Сервис валидации и модерации контента",
    version="1.0.0"
)

# ========================
# Pydantic модели
# ========================

class ModerationRequest(BaseModel):
    """Модель запроса модерации"""
    text: str = Field(..., description="Текст для модерации", min_length=1)
    is_input: bool = Field(True, description="True для входящего текста, False для исходящего")

class ModerationResponse(BaseModel):
    """Модель ответа модерации"""
    is_safe: bool = Field(..., description="Безопасен ли текст")
    reason: Optional[str] = Field(None, description="Причина блокировки")
    confidence: Optional[float] = Field(None, description="Уверенность в решении")

class ValidationRequest(BaseModel):
    """Модель запроса валидации"""
    text: str = Field(..., description="Текст для валидации")
    validation_type: str = Field("basic", description="Тип валидации")

class ValidationResponse(BaseModel):
    """Модель ответа валидации"""
    is_valid: bool = Field(..., description="Валиден ли текст")
    errors: list = Field(default_factory=list, description="Список ошибок")
    suggestions: list = Field(default_factory=list, description="Предложения по исправлению")

# ========================
# Валидационные функции
# ========================

def validate_text_basic(text: str) -> tuple[bool, list, list]:
    """Базовая валидация текста"""
    errors = []
    suggestions = []

    # Проверка длины
    if len(text.strip()) < 3:
        errors.append("Текст слишком короткий")
        suggestions.append("Добавьте больше деталей к вашему запросу")

    if len(text) > 1000:
        errors.append("Текст слишком длинный")
        suggestions.append("Сократите запрос до 1000 символов")

    # Проверка на спам (повторяющиеся символы)
    if any(char * 5 in text for char in "abcdefghijklmnopqrstuvwxyzабвгдежзийклмнопрстуфхцчшщъыьэюя"):
        errors.append("Обнаружены повторяющиеся символы")
        suggestions.append("Удалите повторяющиеся символы")

    # Проверка на капс
    if text.isupper() and len(text) > 20:
        errors.append("Слишком много заглавных букв")
        suggestions.append("Используйте обычный регистр")

    return len(errors) == 0, errors, suggestions

def validate_cocktail_query(text: str) -> tuple[bool, list, list]:
    """Валидация запросов о коктейлях"""
    errors = []
    suggestions = []

    # Базовая валидация
    is_valid, base_errors, base_suggestions = validate_text_basic(text)
    errors.extend(base_errors)
    suggestions.extend(base_suggestions)

    # Специфичная валидация для коктейльных запросов
    text_lower = text.lower()

    # Проверка на релевантность (должно быть что-то связанное с напитками)
    drink_keywords = [
        'коктейль', 'напиток', 'алкоголь', 'бар', 'рецепт', 'ингредиент',
        'водка', 'виски', 'джин', 'ром', 'текила', 'вино', 'пиво',
        'лимон', 'лайм', 'сок', 'сироп', 'лед', 'мята',
        'мартини', 'мохито', 'маргарита', 'дайкири', 'космополитан'
    ]

    # Если запрос длинный, но не содержит ключевых слов о напитках
    if len(text) > 50 and not any(keyword in text_lower for keyword in drink_keywords):
        errors.append("Запрос не связан с напитками или коктейлями")
        suggestions.append("Уточните запрос, добавив информацию о коктейлях или напитках")

    return len(errors) == 0, errors, suggestions

# ========================
# API эндпоинты
# ========================

@app.get("/")
async def root():
    """Корневой эндпоинт"""
    return {
        "service": "Validation Service",
        "version": "1.0.0",
        "status": "active"
    }

@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    return {
        "status": "healthy",
        "moderation": "available",
        "validation": "available"
    }

@app.post("/moderate", response_model=ModerationResponse)
async def moderate_text(request: ModerationRequest):
    """Модерация текста"""
    try:
        logger.info(f"Модерация текста: {'входящий' if request.is_input else 'исходящий'}")

        if request.is_input:
            is_safe, reason = pre_moderate_input(request.text)
        else:
            is_safe, reason = post_moderate_output(request.text)

        return ModerationResponse(
            is_safe=is_safe,
            reason=reason if not is_safe else None
        )

    except Exception as e:
        logger.error(f"Ошибка модерации: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка модерации: {str(e)}")

@app.post("/validate", response_model=ValidationResponse)
async def validate_text(request: ValidationRequest):
    """Валидация текста"""
    try:
        logger.info(f"Валидация текста типа: {request.validation_type}")

        if request.validation_type == "basic":
            is_valid, errors, suggestions = validate_text_basic(request.text)
        elif request.validation_type == "cocktail":
            is_valid, errors, suggestions = validate_cocktail_query(request.text)
        else:
            raise HTTPException(status_code=400, detail=f"Неизвестный тип валидации: {request.validation_type}")

        return ValidationResponse(
            is_valid=is_valid,
            errors=errors,
            suggestions=suggestions
        )

    except Exception as e:
        logger.error(f"Ошибка валидации: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Ошибка валидации: {str(e)}")

@app.post("/validate_and_moderate")
async def validate_and_moderate(text: str, is_input: bool = True, validation_type: str = "basic"):
    """Комбинированная валидация и модерация"""
    try:
        # Сначала валидация
        validation_result = await validate_text(ValidationRequest(
            text=text,
            validation_type=validation_type
        ))

        # Если валидация не прошла, возвращаем ошибку
        if not validation_result.is_valid:
            return {
                "valid": False,
                "safe": False,
                "validation_errors": validation_result.errors,
                "suggestions": validation_result.suggestions,
                "reason": "Валидация не пройдена"
            }

        # Затем модерация
        moderation_result = await moderate_text(ModerationRequest(
            text=text,
            is_input=is_input
        ))

        return {
            "valid": True,
            "safe": moderation_result.is_safe,
            "validation_errors": [],
            "suggestions": [],
            "reason": moderation_result.reason
        }

    except Exception as e:
        logger.error(f"Ошибка комбинированной проверки: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ========================
# Запуск приложения
# ========================

if __name__ == "__main__":
    host = os.getenv("VALIDATION_SERVICE_HOST", "0.0.0.0")
    port = int(os.getenv("VALIDATION_SERVICE_PORT", "8003"))

    logger.info(f"Запуск Validation Service на {host}:{port}")

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info"
    )
