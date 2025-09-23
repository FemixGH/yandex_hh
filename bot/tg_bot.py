#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import logging
import asyncio
import aiohttp
import os
import re
from datetime import datetime

logger = logging.getLogger(__name__)

# Конфигурация
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8000")

# Состояние пользователей
user_states = {}

def escape_markdown_v2(text: str) -> str:
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    for char in escape_chars:
        text = text.replace(char, f'\\{char}')
    return text

def format_bartender_response(text: str) -> str:
    if not text:
        return text or ""
    # Форматирование как в оригинальном боте
    text = re.sub(r'^([А-ЯЁA-Z][^:\n]*):?\s*$', r'*\1*', text, flags=re.MULTILINE)
    text = re.sub(r'^- (.+)$', r'• _\1_', text, flags=re.MULTILINE)
    text = re.sub(r'^(\d+)\.\s*(.+)$', r'*\1\.* \2', text, flags=re.MULTILINE)
    
    drinks = ['мохито', 'мартини', 'маргарита', 'пина колада', 'космополитен', 'дайкири', 'кайпиринья', 'негрони', 'апероль спритц', 'олд фэшн']
    for drink in drinks:
        pattern = r'\b(' + re.escape(drink) + r')\b'
        text = re.sub(pattern, r'*\1*', text, flags=re.IGNORECASE)
        
    text = re.sub(r'\b(\d+\s*°C|\d+\s*градус|\d+\s*мин|\d+\s*сек)\b', r'`\1`', text)
    text = re.sub(r'\b(\d+\s*мл|\d+\s*г|\d+\s*ст\.?\s*л\.?|\d+\s*ч\.?\s*л\.?)\b', r'`\1`', text)
    return text

async def call_orchestrator(user_id: int, text: str) -> dict:
    """Асинхронный вызов оркестратора"""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                f"{ORCHESTRATOR_URL}/query",
                json={"user_id": user_id, "text": text, "k": 3},
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.error(f"Orchestrator error: {response.status}")
                    return None
        except Exception as e:
            logger.error(f"Orchestrator call failed: {e}")
            return None

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

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.effective_user.id
        user_states[uid] = {"disclaimer_shown": True, "accepted_disclaimer": False}
        keyboard = [["✅ Продолжить", "❌ Отказаться"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text(f"🍸 Привет! Я ИИ Бармен!\n\n{DISCLAIMER}", reply_markup=reply_markup)
        logger.info("User %s started bot", uid)
    except Exception as e:
        logger.exception("Ошибка в start: %s", e)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.effective_user.id
        text = (update.message.text or "").strip()
        
        if uid not in user_states:
            user_states[uid] = {"disclaimer_shown": False, "accepted_disclaimer": False}

        # Disclaimer flow
        if not user_states[uid].get("accepted_disclaimer", False):
            if text == "✅ Продолжить":
                user_states[uid]["accepted_disclaimer"] = True
                keyboard = [["🥤 Безалкогольные"], ["🎭 Настроение", "📖 Рецепты"]]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                await update.message.reply_text(
                    "🎉 Отлично! Теперь я ваш персональный бармен! Выберите опцию ниже.",
                    reply_markup=reply_markup
                )
                return
            elif text == "❌ Отказаться":
                await update.message.reply_text("😔 Жаль — если передумаете, введите /start")
                return
            else:
                keyboard = [["✅ Продолжить", "❌ Отказаться"]]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
                await update.message.reply_text("⚠️ Пожалуйста, сначала примите или отклоните условия.", reply_markup=reply_markup)
                return

        # Маппинг кнопок на запросы
        query_mapping = {
            "🥤 Безалкогольные": "Предложи освежающий безалкогольный напиток или моктейль с рецептом",
            "📖 Рецепты": "Покажи рецепт популярного барного напитка с пошаговым приготовлением",
            "😊 Веселое": "Предложи яркий, освежающий напиток для хорошего настроения и праздника",
            "😌 Спокойное": "Предложи мягкий, успокаивающий напиток для спокойного вечера",
            "🔥 Энергичное": "Предложи бодрящий напиток для энергичного настроения",
            "💭 Романтичное": "Предложи элегантный, изысканный напиток для романтического настроения",
            "😎 Уверенное": "Предложи стильный, классический барный напиток для уверенного настроения",
            "🌊 Расслабленное": "Предложи легкий, освежающий напиток для расслабленного настроения",
        }

        if text in query_mapping:
            query = query_mapping[text]
        elif text == "🎭 Настроение":
            keyboard = [
                ["😊 Веселое", "😌 Спокойное"],
                ["🔥 Энергичное", "💭 Романтичное"],
                ["😎 Уверенное", "🌊 Расслабленное"],
                ["🔙 Назад к меню"]
            ]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            await update.message.reply_text("🎭 Выберите настроение:", reply_markup=reply_markup)
            return
        elif text == "🔙 Назад к меню":
            keyboard = [["🥤 Безалкогольные"], ["🎭 Настроение", "📖 Рецепты"]]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            await update.message.reply_text("🍸 Главное меню бармена:", reply_markup=reply_markup)
            return
        else:
            query = text

        # Вызов оркестратора
        logger.info(f"User {uid} query: {query}")
        response = await call_orchestrator(uid, query)

        if not response:
            await update.message.reply_text("😅 Извините, сервис временно недоступен. Попробуйте позже.")
            return

        if response.get("blocked"):
            reason = response.get("reason", "Неизвестная причина")
            await update.message.reply_text(escape_markdown_v2(f"🚫 Запрос заблокирован: {reason}"))
            return

        answer = response.get("answer", "")
        if not answer:
            answer = "Извините, не удалось сгенерировать ответ."

        formatted_answer = format_bartender_response(answer)
        await update.message.reply_text(formatted_answer)

    except Exception as e:
        logger.exception("Ошибка в handle_message: %s", e)
        await update.message.reply_text("😅 Извините, что-то пошло не так. Попробуйте ещё раз.")

def main():
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN not set!")
        return

    logger.info("🚀 Starting Telegram bot...")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.run_polling()

if __name__ == "__main__":
    main()