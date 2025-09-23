#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot Service - обработка сообщений Telegram
"""

import os
import logging
import asyncio
from typing import Optional

import httpx
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import uvicorn

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация
# Загружаем токен через settings, где выполняется подгрузка секретов из Lockbox
try:
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from settings import TELEGRAM_TOKEN
except Exception:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://gateway:8000")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN не установлен")

# FastAPI приложение для управления ботом
app = FastAPI(
    title="Telegram Bot Service",
    description="Сервис для обработки сообщений Telegram бота",
    version="1.0.0"
)

# ========================
# Pydantic модели
# ========================

class TelegramMessage(BaseModel):
    """Модель сообщения от Telegram"""
    user_id: int
    chat_id: int
    text: str
    username: Optional[str] = None

class BotResponse(BaseModel):
    """Модель ответа бота"""
    success: bool
    message: Optional[str] = None
    error: Optional[str] = None

# ========================
# HTTP клиент для Gateway
# ========================

class GatewayClient:
    def __init__(self):
        self.client = httpx.AsyncClient()
        self.gateway_url = GATEWAY_URL

    async def ask_bartender(self, query: str, user_id: str) -> dict:
        """Отправка запроса к барменскому ИИ через Gateway"""
        try:
            response = await self.client.post(
                f"{self.gateway_url}/bartender/ask",
                json={
                    "query": query,
                    "user_id": user_id,
                    "k": 3,
                    "with_moderation": True
                },
                timeout=60.0
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Ошибка обращения к Gateway: {e}")
            raise

gateway_client = GatewayClient()

# ========================
# Telegram Bot логика
# ========================

# Состояние пользователей
user_states = {}

def escape_markdown_v2(text: str) -> str:
    """Экранирует специальные символы для MarkdownV2"""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    for char in escape_chars:
        text = text.replace(char, f'\\{char}')
    return text

def format_bartender_response(text: str) -> str:
    """Специальное форматирование для ответов бармена"""
    text = escape_markdown_v2(text)

    # Применяем базовое форматирование
    lines = text.split('\n')
    formatted_lines = []

    for line in lines:
        line = line.strip()
        if not line:
            formatted_lines.append('')
            continue

        # Обработка списков ингредиентов
        if line.startswith('\\-'):
            formatted_lines.append(f"🍸 {line[2:].strip()}")
        elif '\\:' in line and len(line) < 100:
            # Короткие строки с двоеточием - возможно заголовки
            formatted_lines.append(f"*{line}*")
        else:
            formatted_lines.append(line)

    return '\n'.join(formatted_lines)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start"""
    user = update.effective_user
    welcome_text = f"""
Привет, {user.first_name}\\! 🍸

Я *ИИ Бармен* \\- твой персональный помощник в мире коктейлей\\!

*Что я умею:*
🍹 Подбирать коктейли по ингредиентам
🥃 Рассказывать о напитках и их истории  
📖 Давать рецепты классических и авторских коктейлей
🎯 Советовать напитки под настроение

*Примеры запросов:*
• "Коктейль с водкой и лаймом"
• "Что можно сделать из виски?"
• "Рецепт Мохито"
• "Коктейль для романтического вечера"

Просто напиши мне, что тебя интересует\\! 🚀
"""

    keyboard = [
        ["🍸 Популярные коктейли", "🥃 По ингредиентам"],
        ["📚 Классика", "🎲 Случайный коктейль"],
        ["ℹ️ Помощь"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=False, resize_keyboard=True)

    await update.message.reply_text(
        welcome_text,
        parse_mode='MarkdownV2',
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /help"""
    help_text = """
*Как пользоваться ботом:*

🔸 *Просто напишите запрос* \\- я найду подходящие коктейли
🔸 *Укажите ингредиенты* \\- получите рецепты с ними
🔸 *Спросите про конкретный коктейль* \\- узнаете рецепт и историю

*Примеры:*
• "Коктейли с джином"
• "Рецепт Маргариты"  
• "Что приготовить на вечеринку?"
• "Безалкогольные коктейли"

*Используйте кнопки* для быстрого доступа к популярным категориям\\!
"""

    await update.message.reply_text(help_text, parse_mode='MarkdownV2')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений"""
    user = update.effective_user
    message = update.message
    user_text = message.text

    logger.info(f"Получено сообщение от {user.id} ({user.username}): {user_text}")

    # Обработка кнопок
    if user_text == "🍸 Популярные коктейли":
        user_text = "Покажи популярные коктейли"
    elif user_text == "🥃 По ингредиентам":
        await message.reply_text(
            "Напишите, какие ингредиенты у вас есть, например:\n\"Коктейли с водкой и лимоном\""
        )
        return
    elif user_text == "📚 Классика":
        user_text = "Классические коктейли и их рецепты"
    elif user_text == "🎲 Случайный коктейль":
        user_text = "Предложи случайный коктейль"
    elif user_text == "ℹ️ Помощь":
        await help_command(update, context)
        return

    # Отправляем индикатор печати
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        # Получаем ответ от Gateway
        response = await gateway_client.ask_bartender(user_text, str(user.id))

        if response.get("blocked", False):
            await message.reply_text(
                f"❌ {response.get('reason', 'Запрос заблокирован модерацией')}"
            )
            return

        answer = response.get("answer", "")
        if not answer:
            await message.reply_text("🤔 Не смог найти подходящую информацию. Попробуйте переформулировать запрос.")
            return

        # Форматируем и отправляем ответ
        formatted_answer = format_bartender_response(answer)

        # Разбиваем длинные сообщения
        if len(formatted_answer) > 4000:
            parts = []
            current_part = ""

            for line in formatted_answer.split('\n'):
                if len(current_part + line + '\n') > 4000:
                    if current_part:
                        parts.append(current_part.rstrip())
                        current_part = line + '\n'
                    else:
                        parts.append(line)
                else:
                    current_part += line + '\n'

            if current_part:
                parts.append(current_part.rstrip())

            for i, part in enumerate(parts):
                if i == 0:
                    await message.reply_text(part, parse_mode='MarkdownV2')
                else:
                    await message.reply_text(part, parse_mode='MarkdownV2')

                # Небольшая пауза между частями
                await asyncio.sleep(0.5)
        else:
            await message.reply_text(formatted_answer, parse_mode='MarkdownV2')

        # Добавляем информацию о времени обработки (опционально)
        processing_time = response.get("processing_time", 0)
        if processing_time > 5:  # Показываем только если обработка была долгой
            await message.reply_text(f"⏱ Время обработки: {processing_time:.1f}с")

    except Exception as e:
        logger.error(f"Ошибка при обработке сообщения: {e}")
        await message.reply_text(
            "😔 Произошла ошибка при обработке запроса. Попробуйте еще раз через несколько секунд."
        )

# ========================
# Telegram Bot Application
# ========================

telegram_app = None

async def setup_bot():
    """Настройка и запуск Telegram бота"""
    global telegram_app

    telegram_app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Добавляем обработчики
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("help", help_command))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запускаем бота
    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.updater.start_polling()

    logger.info("Telegram бот запущен")

async def shutdown_bot():
    """Остановка Telegram бота"""
    global telegram_app
    if telegram_app:
        await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()
        logger.info("Telegram бот остановлен")

# ========================
# FastAPI эндпоинты
# ========================

@app.get("/")
async def root():
    """Корневой эндпоинт"""
    return {
        "service": "Telegram Bot Service",
        "version": "1.0.0",
        "status": "active"
    }

@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    global telegram_app
    return {
        "status": "healthy" if telegram_app else "starting",
        "bot_running": telegram_app is not None
    }

@app.post("/send_message")
async def send_message(chat_id: int, text: str, parse_mode: str = None):
    """Отправка сообщения в чат"""
    global telegram_app

    if not telegram_app:
        raise HTTPException(status_code=503, detail="Бот не запущен")

    try:
        await telegram_app.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode
        )
        return {"success": True}
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ========================
# События жизненного цикла
# ========================

@app.on_event("startup")
async def startup_event():
    """События при запуске"""
    logger.info("Запуск Telegram Bot Service")
    await setup_bot()

@app.on_event("shutdown")
async def shutdown_event():
    """События при остановке"""
    logger.info("Остановка Telegram Bot Service")
    await shutdown_bot()

# ========================
# Запуск приложения
# ========================

if __name__ == "__main__":
    host = os.getenv("TELEGRAM_SERVICE_HOST", "0.0.0.0")
    port = int(os.getenv("TELEGRAM_SERVICE_PORT", "8001"))

    logger.info(f"Запуск Telegram Bot Service на {host}:{port}")

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info"
    )
