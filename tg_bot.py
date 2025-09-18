#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# Наш RAG без FAISS (см. rag_yandex_nofaiss.py)
from rag_yandex_nofaiss import async_answer_user_query, build_index_from_bucket

# Настройка логирования
load_dotenv()

LOG_FILENAME = f"ai_bartender_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILENAME, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    logger.error("TELEGRAM_TOKEN не найден в переменных окружения. Установите в .env")
    raise SystemExit("TELEGRAM_TOKEN отсутствует")

# Состояние пользователей (минимальное)
user_states = {}

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

# Команды
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
        logger.info("Пользователь %s написал: %s", uid, text)

        # Инициализация состояния, если вдруг нет
        if uid not in user_states:
            user_states[uid] = {"disclaimer_shown": False, "accepted_disclaimer": False}

        # Если дисклеймер не принят — обрабатываем отдельно
        if not user_states[uid].get("accepted_disclaimer", False):
            if text == "✅ Продолжить":
                user_states[uid]["accepted_disclaimer"] = True
                keyboard = [["🍹 Коктейли", "🥤 Безалкогольные"], ["🎭 Настроение", "📖 Рецепты"]]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                await update.message.reply_text(
                    "🎉 Отлично! Теперь я ваш персональный бармен!\n\n"
                    "💬 Просто напишите мне, что хотите, или используйте кнопки ниже.",
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

        # Если дисклеймер принят — обрабатываем запрос через RAG pipeline
        # Поддерживаем кнопки — преобразуем в понятный запрос
        if text == "🍹 Коктейли":
            query = "подбери коктейль по моему настроению"
        elif text == "🥤 Безалкогольные":
            query = "безалкогольные напитки — предложи варианты"
        elif text == "🎭 Настроение":
            query = "подскажи напиток по настроению"
        elif text == "📖 Рецепты":
            query = "дай рецепт коктейля"
        else:
            query = text

        # Вызов RAG pipeline (async wrapper)
        # async_answer_user_query выполняет pre/post модерацию и находит ответы в vectorstore
        answer, meta = await async_answer_user_query(query, uid, k=3)

        # Если модерация заблокировала — meta содержит блокировку
        if meta.get("blocked"):
            logger.info("Запрос %s заблокирован модерацией: %s", uid, meta.get("reason"))
            # Отправляем пользователю безопасное сообщение (answer уже содержит текст ответа)
            await update.message.reply_text(answer)
            return

        # Иначе отправляем ответ
        await update.message.reply_text(answer)

        # Логируем метаданные для диагностики
        logger.info("Ответ отправлен пользователю %s. meta: retrieved=%s", uid, meta.get("retrieved_count"))

    except Exception as e:
        logger.exception("Ошибка в handle_message: %s", e)
        try:
            await update.message.reply_text("😅 Извините, что-то пошло не так. Попробуйте ещё раз.")
        except Exception:
            pass

def main():
    """Запуск бота"""
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    build_index_from_bucket("vedroo", "")
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🍸 ИИ Бармен запущен (polling)...")
    application.run_polling()

if __name__ == "__main__":
    main()
