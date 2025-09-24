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
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler
from fastapi import FastAPI, HTTPException, Request, Header
from pydantic import BaseModel, Field
import uvicorn

# Redis (async)
try:
    from redis import asyncio as aioredis  # redis>=4.2
except Exception:  # fallback имя
    import redis.asyncio as aioredis  # type: ignore

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
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# Режим работы: polling (локально) или webhook (в облаке). По умолчанию — polling
USE_WEBHOOK = os.getenv("USE_WEBHOOK", "false").lower() in {"1", "true", "yes"}
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Публичный URL API Gateway, например: https://<id>.apigw.yandexcloud.net/telegram/webhook
WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN")  # Совпадает с тем, что задан в setWebhook

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN не установлен")

# FastAPI приложение для управления ботом
app = FastAPI(
    title="Telegram Bot Service",
    description="Сервис для обработки сообщений Telegram бота",
    version="1.0.0"
)

# ========================
# Redis client (lazy init)
# ========================
redis_client: Optional[aioredis.Redis] = None

async def get_redis() -> Optional[aioredis.Redis]:
    global redis_client
    if redis_client is None and REDIS_URL:
        try:
            redis_client = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
            # Быстрый ping для валидации соединения (не валим старт при ошибке)
            try:
                await redis_client.ping()
                logger.info("Redis подключен: %s", REDIS_URL)
            except Exception as e:
                logger.warning("Redis недоступен (%s) — используем in-memory fallback", e)
        except Exception as e:
            logger.warning("Не удалось инициализировать Redis: %s", e)
            redis_client = None
    return redis_client

TERMS_KEY_PREFIX = "tg:terms:accepted:"

async def set_terms_accepted(user_id: int, accepted: bool) -> None:
    r = await get_redis()
    key = f"{TERMS_KEY_PREFIX}{user_id}"
    if r is None:
        # fallback in-memory
        user_states[user_id] = {"accepted_terms": accepted}
        return
    try:
        await r.set(key, "1" if accepted else "0")
        # на всякий случай обновим in-memory
        user_states[user_id] = {"accepted_terms": accepted}
    except Exception as e:
        logger.warning("Redis set failed: %s — fallback to memory", e)
        user_states[user_id] = {"accepted_terms": accepted}

async def get_terms_accepted(user_id: int) -> bool:
    # сначала попробуем Redis
    r = await get_redis()
    key = f"{TERMS_KEY_PREFIX}{user_id}"
    if r is not None:
        try:
            val = await r.get(key)
            if val is not None:
                return val == "1"
        except Exception as e:
            logger.warning("Redis get failed: %s — fallback to memory", e)
    # затем in-memory
    return bool(user_states.get(user_id, {}).get("accepted_terms", False))

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
        # Нормализуем базовый URL, чтобы избежать двойных слэшей при конкатенации
        base = GATEWAY_URL or ""
        self.gateway_url = base.rstrip("/")

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

# Состояние пользователей (fallback при отсутствии Redis)
user_states = {}

# ====== Новый блок: текст дисклеймера и клавиатура согласия ======
DISCLAIMER_TEXT = (
    "Внимание: бот может предоставлять информацию об алкогольных напитках.\n"
    "— Контент 18+\n"
    "— Пейте ответственно и умеренно\n"
    "— Не нарушайте законы вашей страны\n\n"
    "Нажимая ‘Принимаю условия’, вы подтверждаете, что вам есть 18 лет и вы согласны с условиями использования."
)

def get_terms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(text="Принимаю условия", callback_data="terms_accept"),
            InlineKeyboardButton(text="Не принимаю", callback_data="terms_reject"),
        ]
    ])

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
    """Обработчик команды /start — показываем дисклеймер и просим подтвердить условия."""
    user = update.effective_user
    # Сбрасываем согласие в Redis (необязательно, но полезно):
    await set_terms_accepted(user.id, False)

    await update.message.reply_text(
        DISCLAIMER_TEXT,
        reply_markup=get_terms_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /help"""
    help_text = (
        "*Как пользоваться ботом:*\n\n"
        "🔸 *Сначала примите условия использования* командой /start\n"
        "🔸 *Потом просто напишите запрос* — я найду подходящие коктейли\n"
        "🔸 *Укажите ингредиенты* — получите рецепты с ними\n"
        "🔸 *Спросите про конкретный коктейль* — узнаете рецепт и историю\n\n"
        "*Примеры:*\n• \"Коктейли с джином\"\n• \"Рецепт Маргариты\"  \n• \"Что приготовить на вечеринку?\"\n• \"Безалкогольные коктейли\"\n"
    )
    await update.message.reply_text(help_text, parse_mode='MarkdownV2')

# Новый обработчик inline-кнопок согласия с условиями
async def on_terms_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = query.from_user
    data = query.data or ""

    if data == "terms_accept":
        await set_terms_accepted(user.id, True)
        await query.answer("Условия приняты")
        # Покажем основную клавиатуру категорий и приветствие
        keyboard = [
            ["🍸 Популярные коктейли", "🥃 По ингредиентам"],
            ["📚 Классика", "🎲 Случайный коктейль"],
            ["ℹ️ Помощь"],
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=False, resize_keyboard=True)
        welcome_text = (
            "Привет! Я ИИ Бармен — помогу с рецептами и идеями напитков.\n"
            "Напишите, что вас интересует, или выберите кнопку ниже."
        )
        try:
            await query.edit_message_text("Спасибо! Условия приняты.")
        except Exception:
            pass
        await context.bot.send_message(chat_id=query.message.chat_id, text=welcome_text, reply_markup=reply_markup)

    elif data == "terms_reject":
        await set_terms_accepted(user.id, False)
        await query.answer("Условия не приняты")
        try:
            await query.edit_message_text(
                "Вы отказались от условий. Бот не будет предоставлять информацию об алкогольных напитках.\n"
                "Если передумаете — отправьте /start."
            )
        except Exception:
            pass
    else:
        await query.answer()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений"""
    user = update.effective_user
    message = update.message

    # Требуем принятия условий перед использованием (читаем из Redis)
    accepted = await get_terms_accepted(user.id)
    if not accepted:
        await message.reply_text(
            "Пожалуйста, сначала примите условия использования: /start",
            reply_markup=get_terms_keyboard()
        )
        return

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
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

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
                await message.reply_text(part, parse_mode='MarkdownV2')
                await asyncio.sleep(0.5)
        else:
            await message.reply_text(formatted_answer, parse_mode='MarkdownV2')

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
    telegram_app.add_handler(CallbackQueryHandler(on_terms_decision, pattern=r"^terms_(accept|reject)$"))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Инициализация
    await telegram_app.initialize()
    await telegram_app.start()

    if USE_WEBHOOK and WEBHOOK_URL:
        # В облаке используем webhook
        await telegram_app.bot.set_webhook(url=WEBHOOK_URL, secret_token=WEBHOOK_SECRET_TOKEN)
        logger.info(f"Webhook установлен: {WEBHOOK_URL}")
    else:
        # Локально используем polling
        try:
            await telegram_app.bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            pass
        if getattr(telegram_app, 'updater', None) is not None:
            await telegram_app.updater.start_polling()
            logger.info("Polling запущен")
        else:
            logger.warning("Updater недоступен. Проверьте версию python-telegram-bot.")

    logger.info("Telegram бот запущен")

async def shutdown_bot():
    """Остановка Telegram бота"""
    global telegram_app
    if telegram_app:
        if USE_WEBHOOK and WEBHOOK_URL:
            try:
                await telegram_app.bot.delete_webhook()
            except Exception:
                pass
        else:
            if getattr(telegram_app, 'updater', None) is not None:
                try:
                    await telegram_app.updater.stop()
                except Exception:
                    pass
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
        "bot_running": telegram_app is not None,
        "mode": "webhook" if USE_WEBHOOK and WEBHOOK_URL else "polling"
    }

@app.get("/webhook/info")
async def webhook_info():
    """Диагностика текущего состояния вебхука на стороне Telegram."""
    global telegram_app
    if not telegram_app:
        raise HTTPException(status_code=503, detail="Бот не запущен")
    try:
        info = await telegram_app.bot.get_webhook_info()
        return {
            "use_webhook": USE_WEBHOOK,
            "configured_webhook_url": WEBHOOK_URL,
            "secret_set": bool(WEBHOOK_SECRET_TOKEN),
            "telegram": {
                "url": info.url,
                "has_custom_certificate": info.has_custom_certificate,
                "pending_update_count": info.pending_update_count,
                "ip_address": info.ip_address,
                "last_error_date": info.last_error_date,
                "last_error_message": info.last_error_message,
                "max_connections": info.max_connections,
                "allowed_updates": info.allowed_updates,
            }
        }
    except Exception as e:
        logger.error(f"Ошибка получения webhook info: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/webhook/sync")
async def webhook_sync():
    """Переустанавливает вебхук на текущий WEBHOOK_URL/секрет. Полезно, если бот молчит."""
    global telegram_app
    if not telegram_app:
        raise HTTPException(status_code=503, detail="Бот не запущен")
    if not (USE_WEBHOOK and WEBHOOK_URL):
        raise HTTPException(status_code=400, detail="Webhook режим не активирован")
    try:
        await telegram_app.bot.set_webhook(url=WEBHOOK_URL, secret_token=WEBHOOK_SECRET_TOKEN)
        info = await telegram_app.bot.get_webhook_info()
        logger.info("Webhook переустановлен: %s", WEBHOOK_URL)
        return {"success": True, "url": WEBHOOK_URL, "telegram_url": info.url}
    except Exception as e:
        logger.error(f"Ошибка установки вебхука: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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

# Вебхук для Telegram (для работы через API Gateway/Serverless Containers)
@app.post("/telegram/webhook")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: Optional[str] = Header(default=None)):
    """Эндпоинт для приёма вебхуков от Telegram. Возвращаем ACK быстро, обработку запускаем в фоне."""
    global telegram_app

    if not (USE_WEBHOOK and WEBHOOK_URL):
        raise HTTPException(status_code=400, detail="Webhook режим не активирован")

    if WEBHOOK_SECRET_TOKEN:
        if not x_telegram_bot_api_secret_token or x_telegram_bot_api_secret_token != WEBHOOK_SECRET_TOKEN:
            raise HTTPException(status_code=403, detail="Неверный секретный токен вебхука")

    if not telegram_app:
        raise HTTPException(status_code=503, detail="Бот не запущен")

    try:
        data = await request.json()
        # Логируем факт получения апдейта (без чувствительных данных)
        try:
            upd_type = data.get("message", {}).get("text") or data.get("callback_query", {}).get("data")
            logger.info("Получен вебхук от Telegram (сокр.): %s", str(upd_type)[:120])
        except Exception:
            logger.info("Получен вебхук от Telegram (без парсинга тела)")

        update = Update.de_json(data, bot=telegram_app.bot)
        # Обрабатываем в фоне, чтобы быстро вернуть 200 OK
        asyncio.create_task(telegram_app.process_update(update))
    except Exception as e:
        logger.error(f"Ошибка обработки вебхука: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"ok": True}

# Алиас короткого пути, чтобы проксирование через gateway по /telegram/webhook попадало сюда как /webhook
@app.post("/webhook")
async def telegram_webhook_alias(request: Request, x_telegram_bot_api_secret_token: Optional[str] = Header(default=None)):
    return await telegram_webhook(request, x_telegram_bot_api_secret_token)

# ========================
# События жизненного цикла
# ========================

@app.on_event("startup")
async def startup_event():
    """События при запуске"""
    logger.info("Запуск Telegram Bot Service")
    # Инициализируем Redis заранее (не критично, но полезно)
    await get_redis()
    await setup_bot()

@app.on_event("shutdown")
async def shutdown_event():
    """События при остановке"""
    logger.info("Остановка Telegram Bot Service")
    await shutdown_bot()
    try:
        await gateway_client.client.aclose()
    except Exception:
        pass

# ========================
# Запуск приложения
# ========================

if __name__ == "__main__":
    # Для Yandex Serverless Containers часто используется переменная PORT (обычно 8080)
    host = os.getenv("TELEGRAM_SERVICE_HOST", "0.0.0.0")
    port = int(os.getenv("PORT", os.getenv("TELEGRAM_SERVICE_PORT", "8001")))

    logger.info(f"Запуск Telegram Bot Service на {host}:{port} (mode={'webhook' if USE_WEBHOOK and WEBHOOK_URL else 'polling'})")

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info"
    )
