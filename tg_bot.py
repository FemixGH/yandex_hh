#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from settings import TELEGRAM_TOKEN
from rag_yandex_nofaiss import async_answer_user_query
from bartender_file_handler import build_bartender_index_from_bucket
from logging_conf import setup_logging
setup_logging()

import logging
import os
import re
logger = logging.getLogger(__name__)


# Состояние пользователей (минимальное)
user_states = {}

def escape_markdown_v2(text: str) -> str:
    """
    Экранирует специальные символы для MarkdownV2
    """
    # Символы, которые нужно экранировать в MarkdownV2
    escape_chars = r'_*[]()~`>#+-=|{}.!'

    # Экранируем каждый символ
    for char in escape_chars:
        text = text.replace(char, f'\\{char}')

    return text

def format_markdown_message(text: str) -> str:
    """
    Форматирует сообщение с поддержкой Markdown, сохраняя эмодзи и основное форматирование
    """
    # Не экранируем эмодзи и базовые символы
    # Эта функция будет использоваться для системных сообщений бота
    return text

def format_bartender_response(text: str) -> str:
    """
    Специальное форматирование для ответов бармена с поддержкой Markdown
    """
    # Заменяем обычные символы на Markdown форматирование

    # Заголовки рецептов (делаем жирными)
    text = re.sub(r'^([А-ЯЁA-Z][^:\n]*):?\s*$', r'*\1*', text, flags=re.MULTILINE)

    # Ингредиенты (делаем курсивом строки с дефисами)
    text = re.sub(r'^- (.+)$', r'• _\1_', text, flags=re.MULTILINE)

    # Шаги приготовления (нумерованные списки)
    text = re.sub(r'^(\d+)\.\s*(.+)$', r'*\1\.* \2', text, flags=re.MULTILINE)

    # Выделяем названия напитков жирным
    drinks = ['мохито', 'мартини', 'маргарита', 'пина колада', 'космополитен', 'дайкири', 'кайпиринья', 'негрони', 'апероль спритц', 'олд фэшн']
    for drink in drinks:
        pattern = r'\b(' + re.escape(drink) + r')\b'
        text = re.sub(pattern, r'*\1*', text, flags=re.IGNORECASE)

    # Выделяем температуры и время
    text = re.sub(r'\b(\d+\s*°C|\d+\s*градус|\d+\s*мин|\d+\s*сек)\b', r'`\1`', text)

    # Выделяем количества ингредиентов
    text = re.sub(r'\b(\d+\s*мл|\d+\s*г|\d+\s*ст\.?\s*л\.?|\d+\s*ч\.?\s*л\.?)\b', r'`\1`', text)

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
    try:
        uid = update.effective_user.id
        text = (update.message.text or "").strip()
        user_info = get_user_info(update)
        logger.info("Пользователь %s написал: %s", uid, text)
        logger.info("Пользователь %s написал: %s", user_info, text)
        # Инициализация состояния, если вдруг нет
        if uid not in user_states:
            user_states[uid] = {"disclaimer_shown": False, "accepted_disclaimer": False}

        # Если дисклеймер не принят — обрабатываем отдельно
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

        # Если дисклеймер принят — обрабатываем запрос через RAG pipeline
        # Поддерживаем кнопки — преобразуем в понятный запрос
        if text == "🥤 Безалкогольные":
            query = "Предложи освежающий безалкогольный напиток или мокктейль с рецептом"
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
            return
        elif text == "📖 Рецепты":
            query = "Покажи рецепт популярного барного напитка с пошаговым приготовлением"

        # Обработка выбора настроения
        elif text == "😊 Веселое":
            query = "Предложи яркий, освежающий напиток для хорошего настроения и праздника"
        elif text == "😌 Спокойное":
            query = "Предложи мягкий, успокаивающий напиток для спокойного вечера"
        elif text == "🔥 Энергичное":
            query = "Предложи бодрящий напиток для энергичного настроения"
        elif text == "💭 Романтичное":
            query = "Предложи элегантный, изысканный напиток для романтического настроения"
        elif text == "😎 Уверенное":
            query = "Предложи стильный, классический барный напиток для уверенного настроения"
        elif text == "🌊 Расслабленное":
            query = "Предложи легкий, освежающий напиток для расслабленного настроения"
        elif text == "🔙 Назад к меню":
            # Возвращаемся к основному меню
            keyboard = [["🥤 Безалкогольные"], ["🎭 Настроение", "📖 Рецепты"]]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            await update.message.reply_text(
                "🍸 Главное меню бармена:",
                reply_markup=reply_markup,
                parse_mode='MarkdownV2'
            )
            return

        # Специальная обработка запросов о расслаблении
        elif any(word in text.lower() for word in ["расслаб", "отдохн", "релакс", "устал", "стресс"]):
            query = "Предложи расслабляющий напиток для снятия стресса и отдыха"

        # Специальная обработка запросов о бюджетных коктейлях
        elif any(word in text.lower() for word in ["дешев", "бюджет", "недорог", "простой", "экономн"]):
            query = f"Предложи простой и бюджетный коктейль: {text}"

        # Специальная обработка запросов с энергетиками
        elif any(word in text.lower() for word in ["редбул", "red bull", "энергетик", "энергия", "бодрящ"]):
            query = f"Предложи энергичный коктейль или напиток: {text}"
        else:
            query = text

        # Вызов RAG pipeline (async wrapper)
        # async_answer_user_query выполняет pre/post модерацию и находит ответы в vectorstore
        answer, meta = await async_answer_user_query(query, uid, k=3)

        # Если модерация заблокировала — meta содержит блокировку
        if meta.get("blocked"):
            logger.info("Запрос %s заблокирован модерацией: %s", uid, meta.get("reason"))
            logger.info("Запрос от %s заблокирован модерацией: %s", user_info, meta.get("reason"))
            await update.message.reply_text(
                escape_markdown_v2(answer),
                parse_mode='MarkdownV2'
            )
            return

        # Иначе отправляем ответ с Markdown форматированием
        formatted_answer = format_bartender_response(answer)
        await update.message.reply_text(
            formatted_answer,
            parse_mode='MarkdownV2'
        )

        # Если это был ответ на запрос по настроению, добавляем кнопку возврата
        if text.startswith(("😊", "😌", "🔥", "💭", "😎", "🌊")):
            keyboard = [["🥤 Безалкогольные"], ["🎭 Настроение", "📖 Рецепты"]]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            await update.message.reply_text(
                "🍸 Хотите попробовать что\\-то еще\\?",
                reply_markup=reply_markup,
                parse_mode='MarkdownV2'
            )

        # Логируем отправленный ответ
        logger.info("Ответ отправлен пользователю %s: %s", uid, answer[:200] + "..." if len(answer) > 200 else answer)
        logger.info("Ответ отправлен пользователю %s: %s", user_info, answer[:200] + "..." if len(answer) > 200 else answer)
        # Логируем метаданные для диагностики
        logger.info("Метаданные ответа для пользователя %s: retrieved=%s", uid, meta.get("retrieved_count"))
        logger.info("Метаданные ответа для пользователя %s: retrieved=%s", user_info, meta.get("retrieved_count"))
    except Exception as e:
        logger.exception("Ошибка в handle_message: %s", e)
        try:
            await update.message.reply_text(
                "😅 Извините, что\\-то пошло не так\\. Попробуйте ещё раз\\.",
                parse_mode='MarkdownV2'
            )
        except Exception:
            pass

def main():
    """Запуск бота"""
    logger.info("🚀 Запуск бота...")
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    logger.info("🔧 Создаю JWT и получаю IAM_TOKEN...")
    logger.info("🔑 JWT создан, IAM_TOKEN получен")

    # Проверяем, существует ли уже векторный индекс
    force_rebuild = os.getenv("FORCE_REBUILD_INDEX", "").lower() in ["true", "1", "yes"]

    try:
        if not force_rebuild:
            # Сначала пробуем инкрементальное обновление
            logger.info("🔄 Проверяю наличие новых файлов для инкрементального обновления...")
            from incremental_rag import update_rag_incremental

            try:
                incremental_success = update_rag_incremental("vedroo")
                if incremental_success:
                    logger.info("✅ Инкрементальное обновление выполнено успешно")
                else:
                    logger.warning("⚠️ Инкрементальное обновление не удалось, выполняю полную перестройку")
                    raise Exception("Incremental update failed")
            except Exception as e:
                logger.info("📚 Выполняю полную перестройку индекса...")
                build_bartender_index_from_bucket("vedroo", "")
                logger.info("📚 Новый индекс создан из бакета")

            # Загружаем итоговый индекс
            from rag_yandex_nofaiss import load_vectorstore
            mat, docs = load_vectorstore()
            logger.info("📚 Векторный индекс загружен (%d документов)", len(docs))
        else:
            logger.info("🔄 Принудительная перестройка индекса...")
            build_bartender_index_from_bucket("vedroo", "")
            logger.info("📚 Новый индекс создан из бакета")
    except Exception as e:
        logger.error("❌ Ошибка при работе с индексом: %s", e)
        logger.info("📚 Создаю новый векторный индекс из S3 бакета...")
        logger.info("🔍 Поиск файлов в бакете vedroo...")

        # Сначала проверим, что есть в бакете
        try:
            import boto3
            from settings import S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY

            s3 = boto3.client(
                "s3",
                endpoint_url=S3_ENDPOINT,
                aws_access_key_id=S3_ACCESS_KEY,
                aws_secret_access_key=S3_SECRET_KEY,
            )
            response = s3.list_objects_v2(Bucket="vedroo", Prefix="")
            contents = response.get("Contents") or []

            logger.info("📁 Найдено файлов в бакете: %d", len(contents))
            csv_count = sum(1 for obj in contents if obj.get("Key", "").lower().endswith('.csv'))
            pdf_count = sum(1 for obj in contents if obj.get("Key", "").lower().endswith('.pdf'))
            other_count = len(contents) - csv_count - pdf_count

            logger.info("📊 CSV файлов: %d", csv_count)
            logger.info("📄 PDF файлов: %d", pdf_count)
            logger.info("📋 Других файлов: %d", other_count)

            # Показываем детали файлов
            for obj in contents:
                key = obj.get("Key")
                size = obj.get("Size")
                if key:
                    logger.info("📎 Файл: %s (%d байт)", key, size)

        except Exception as bucket_error:
            logger.warning("⚠️ Не удалось проверить содержимое бакета: %s", bucket_error)

        # Строим индекс
        build_bartender_index_from_bucket("vedroo", "")
        logger.info("📚 Новый индекс создан из бакета")

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🍸 ИИ Бармен запущен (polling)...")
    application.run_polling()

if __name__ == "__main__":
    main()