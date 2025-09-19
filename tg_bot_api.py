#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Телеграм бот для ИИ Бармена, работающий через FastAPI API
"""

import asyncio
import aiohttp
import logging
from typing import Dict, Any, Optional
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from settings import TELEGRAM_TOKEN
from logging_conf import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

# Настройки API
API_BASE_URL = "http://localhost:8000"  # URL вашего FastAPI сервера
API_TIMEOUT = 30

# Состояние пользователей
user_states = {}

class BartenderAPIClient:
    """Клиент для работы с FastAPI бэкендом"""

    def __init__(self, base_url: str = API_BASE_URL):
        self.base_url = base_url.rstrip('/')

    async def ask_bartender(self, query: str, user_id: str) -> Dict[str, Any]:
        """Отправка запроса к барменскому ИИ через API"""
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)) as session:
            try:
                async with session.post(
                    f"{self.base_url}/bartender/ask",
                    json={
                        "query": query,
                        "user_id": user_id,
                        "k": 3,
                        "with_moderation": True
                    }
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        error_text = await response.text()
                        logger.error(f"API error {response.status}: {error_text}")
                        return {
                            "answer": "😔 Извините, сейчас у меня технические трудности. Попробуйте позже.",
                            "blocked": False,
                            "error": True
                        }
            except asyncio.TimeoutError:
                logger.error("API timeout for user %s", user_id)
                return {
                    "answer": "⏰ Запрос занял слишком много времени. Попробуйте упростить вопрос.",
                    "blocked": False,
                    "error": True
                }
            except Exception as e:
                logger.error(f"API client error: {e}")
                return {
                    "answer": "😅 Что-то пошло не так. Попробуйте ещё раз.",
                    "blocked": False,
                    "error": True
                }

    async def health_check(self) -> bool:
        """Проверка здоровья API"""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get(f"{self.base_url}/health") as response:
                    return response.status == 200
        except Exception:
            return False

# Глобальный клиент API
api_client = BartenderAPIClient()

def escape_markdown_v2(text: str) -> str:
    """Экранирует специальные символы для MarkdownV2"""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    for char in escape_chars:
        text = text.replace(char, f'\\{char}')
    return text

def format_bartender_response(text: str) -> str:
    """Форматирование ответов бармена для Telegram"""
    import re

    # Сначала экранируем все специальные символы MarkdownV2
    text = escape_markdown_v2(text)

    # Применяем форматирование
    # Ингредиенты (ищем уже экранированные дефисы)
    text = re.sub(r'^\\- (.+)$', r'• _\1_', text, flags=re.MULTILINE)

    # Шаги приготовления (нумерованные списки)
    text = re.sub(r'^(\d+)\\\.\s*(.+)$', r'\1\\. \2', text, flags=re.MULTILINE)

    # Выделяем названия популярных напитков
    drinks = ['мохито', 'мартини', 'маргарита', 'пина колада', 'космополитен',
              'дайкири', 'кайпиринья', 'негрони', 'апероль спритц', 'олд фэшн']
    for drink in drinks:
        escaped_drink = escape_markdown_v2(drink)
        pattern = r'\b(' + re.escape(escaped_drink) + r')\b'
        text = re.sub(pattern, r'*\1*', text, flags=re.IGNORECASE)

    # Выделяем температуры и время
    text = re.sub(r'\b(\d+\s*°C|\d+\s*градус|\d+\s*мин|\d+\s*сек)\b', r'`\1`', text)

    # Выделяем количества ингредиентов
    text = re.sub(r'\b(\d+\s*мл|\d+\s*г|\d+\s*ст\\\.?\s*л\\\.?|\d+\s*ч\\\.?\s*л\\\.?)\b', r'`\1`', text)

    return text

def get_user_info(update: Update) -> str:
    """Получает информацию о пользователе для логирования"""
    user = update.effective_user
    if not user:
        return "Unknown"

    info_parts = []
    if user.username:
        info_parts.append(f"@{user.username}")
    if user.first_name:
        info_parts.append(user.first_name)
    if user.last_name:
        info_parts.append(user.last_name)

    if info_parts:
        return f"{user.id} ({' '.join(info_parts)})"
    else:
        return str(user.id)

# Дисклеймер
DISCLAIMER = """
⚠️ ВАЖНОЕ ПРЕДУПРЕЖДЕНИЕ ⚠️

🚫 Чрезмерное употребление алкоголя вредит вашему здоровью
🚫 Алкоголь противопоказан лицам до 18 лет
🚫 Беременным и кормящим женщинам
🚫 Лицам с заболеваниями, при которых противопоказан алкоголь

⚡ Этот бот создан исключительно в развлекательных и образовательных целях
⚡ Мы не призываем к употреблению алкоголя
⚡ Пожалуйста, употребляйте алкоголь ответственно

Если вы согласны с условиями, нажмите "✅ Продолжить" 👇
"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    try:
        uid = update.effective_user.id
        user_states[uid] = {"disclaimer_shown": True, "accepted_disclaimer": False}

        keyboard = [["✅ Продолжить", "❌ Отказаться"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

        formatted_disclaimer = escape_markdown_v2(DISCLAIMER)
        await update.message.reply_text(
            f"🍸 Привет\\! Я ИИ Бармен\\!\n\n{formatted_disclaimer}",
            reply_markup=reply_markup,
            parse_mode='MarkdownV2'
        )
        logger.info("User %s started bot", uid)
    except Exception as e:
        logger.exception("Ошибка в start: %s", e)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка сообщений пользователей"""
    try:
        uid = update.effective_user.id
        text = (update.message.text or "").strip()
        user_info = get_user_info(update)
        logger.info("Пользователь %s написал: %s", user_info, text)

        # Инициализация состояния пользователя
        if uid not in user_states:
            user_states[uid] = {"disclaimer_shown": False, "accepted_disclaimer": False}

        # Обработка дисклеймера
        if not user_states[uid].get("accepted_disclaimer", False):
            if text == "✅ Продолжить":
                user_states[uid]["accepted_disclaimer"] = True
                keyboard = [["🥤 Безалкогольные"], ["🎭 Настроение", "📖 Рецепты"]]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                await update.message.reply_text(
                    "🎉 Отлично\\! Теперь я ваш персональный бармен\\!\n\n"
                    "💬 Просто напишите мне, что хотите, или используйте кнопки ниже\\.\n"
                    "🍸 Например: 'Рецепт мохито', 'Коктейль с джином', 'Безалкогольный напиток для вечеринки'\n"
                    "🔍 Я знаю множество рецептов коктейлей и безалкогольных напитков\\!",
                    reply_markup=reply_markup,
                    parse_mode='MarkdownV2'
                )
                return
            elif text == "❌ Отказаться":
                await update.message.reply_text(
                    "😔 Жаль — если передумаете, введите /start",
                    parse_mode='MarkdownV2'
                )
                return
            else:
                keyboard = [["✅ Продолжить", "❌ Отказаться"]]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
                await update.message.reply_text(
                    "⚠️ Пожалуйста, сначала примите или отклоните условия\\.",
                    reply_markup=reply_markup,
                    parse_mode='MarkdownV2'
                )
                return

        # Обработка кнопок и формирование запроса
        query = await process_button_or_text(text, update)
        if query is None:  # Уже обработано в process_button_or_text
            return

        # Показываем индикатор набора текста
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')

        # Отправляем запрос к API
        response = await api_client.ask_bartender(query, str(uid))

        # Обрабатываем ответ
        answer = response.get("answer", "Извините, не могу ответить на этот вопрос.")
        blocked = response.get("blocked", False)
        error = response.get("error", False)

        if blocked:
            logger.info("Запрос от %s заблокирован модерацией: %s", user_info, response.get("reason"))
            await update.message.reply_text(
                escape_markdown_v2(answer),
                parse_mode='MarkdownV2'
            )
            return

        if error:
            logger.warning("Ошибка API для пользователя %s", user_info)
            await update.message.reply_text(
                escape_markdown_v2(answer),
                parse_mode='MarkdownV2'
            )
            return

        # Отправляем отформатированный ответ
        formatted_answer = format_bartender_response(answer)
        await update.message.reply_text(
            formatted_answer,
            parse_mode='MarkdownV2'
        )

        # Если это был ответ на запрос по настроению, показываем меню
        if text.startswith(("😊", "😌", "🔥", "💭", "😎", "🌊")):
            keyboard = [["🥤 Безалкогольные"], ["🎭 Настроение", "📖 Рецепты"]]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            await update.message.reply_text(
                "🍸 Хотите попробовать что\\-то еще\\?",
                reply_markup=reply_markup,
                parse_mode='MarkdownV2'
            )

        # Логирование
        processing_time = response.get("processing_time", 0)
        retrieved_count = response.get("retrieved_count", 0)
        logger.info("Ответ отправлен пользователю %s (время: %.2fs, документов: %d)",
                   user_info, processing_time, retrieved_count)

    except Exception as e:
        logger.exception("Ошибка в handle_message: %s", e)
        try:
            await update.message.reply_text(
                "😅 Извините, что\\-то пошло не так\\. Попробуйте ещё раз\\.",
                parse_mode='MarkdownV2'
            )
        except Exception:
            pass

async def process_button_or_text(text: str, update: Update) -> Optional[str]:
    """Обработка кнопок и преобразование в запросы"""
    # Обработка основных кнопок
    if text == "🥤 Безалкогольные":
        return "Предложи освежающий безалкогольный напиток или мокктейль с рецептом"
    elif text == "📖 Рецепты":
        return "Покажи рецепт популярного барного напитка с пошаговым приготовлением"
    elif text == "🎭 Настроение":
        # Показываем меню настроений
        keyboard = [
            ["😊 Веселое", "😌 Спокойное"],
            ["🔥 Энергичное", "💭 Романтичное"],
            ["😎 Уверенное", "🌊 Расслабленное"],
            ["🔙 Назад к меню"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text(
            "🎭 Выберите ваше настроение, и я подберу идеальный коктейль:",
            reply_markup=reply_markup,
            parse_mode='MarkdownV2'
        )
        return None
    elif text == "🔙 Назад к меню":
        # Возвращаемся к основному меню
        keyboard = [["🥤 Безалкогольные"], ["🎭 Настроение", "📖 Рецепты"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text(
            "🍸 Главное меню бармена:",
            reply_markup=reply_markup,
            parse_mode='MarkdownV2'
        )
        return None

    # Обработка настроений
    mood_queries = {
        "😊 Веселое": "Предложи яркий, освежающий напиток для хорошего настроения и праздника",
        "😌 Спокойное": "Предложи мягкий, успокаивающий напиток для спокойного вечера",
        "🔥 Энергичное": "Предложи бодрящий напиток для энергичного настроения",
        "💭 Романтичное": "Предложи элегантный, изысканный напиток для романтического настроения",
        "😎 Уверенное": "Предложи стильный, классический барный напиток для уверенного настроения",
        "🌊 Расслабленное": "Предложи легкий, освежающий напиток для расслабленного настроения"
    }

    if text in mood_queries:
        return mood_queries[text]

    # Специальные обработки текста
    text_lower = text.lower()
    if any(word in text_lower for word in ["расслаб", "отдохн", "релакс", "устал", "стресс"]):
        return "Предложи расслабляющий напиток для снятия стресса и отдыха"
    elif any(word in text_lower for word in ["дешев", "бюджет", "недорог", "простой", "экономн"]):
        return f"Предложи простой и бюджетный коктейль: {text}"
    elif any(word in text_lower for word in ["редбул", "red bull", "энергетик", "энергия", "бодрящ"]):
        return f"Предложи энергичный коктейль или напиток: {text}"

    # Обычный текстовый запрос
    return text

async def check_api_health():
    """Проверка доступности API при запуске"""
    logger.info("🔍 Проверяю доступность FastAPI сервера...")

    for attempt in range(5):
        if await api_client.health_check():
            logger.info("✅ FastAPI сервер доступен")
            return True
        else:
            logger.warning(f"⚠️ FastAPI сервер недоступен (попытка {attempt + 1}/5)")
            if attempt < 4:
                await asyncio.sleep(2)

    logger.error("❌ FastAPI сервер недоступен. Убедитесь, что сервер запущен на %s", API_BASE_URL)
    return False

def main():
    """Запуск бота"""
    logger.info("🚀 Запуск телеграм бота ИИ Бармена...")

    if not TELEGRAM_TOKEN:
        logger.error("❌ TELEGRAM_TOKEN не установлен в переменных окружения")
        return

    # Создаем приложение
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🤖 Бот настроен, запускаю...")
    logger.info(f"🌐 API сервер: {API_BASE_URL}")

    # Запускаем бота
    try:
        application.run_polling(drop_pending_updates=True)
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"❌ Ошибка при запуске бота: {e}")

if __name__ == "__main__":
    main()
