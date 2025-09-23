#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from settings import TELEGRAM_TOKEN, ORCH_URL as ORCH_URL_RAW
from services.faiss.faiss import build_index, load_index, build_docs_from_s3
from services.rag.incremental_rag import update_rag_incremental, save_incremental_state, get_bucket_files
from services.orchestrator.orchestrator import query as orch_query_sync
from services.auth.auth import start_auth
import logging
import os
import asyncio
import re
import requests
from urllib.parse import urlparse
from datetime import datetime

logger = logging.getLogger(__name__)

# Normalize ORCH_URL (ensure scheme)
ORCH_URL = ORCH_URL_RAW
if ORCH_URL:
    parsed = urlparse(ORCH_URL)
    if not parsed.scheme:
        ORCH_URL = "http://" + ORCH_URL
        logger.info("Normalized ORCH_URL -> %s", ORCH_URL)
else:
    ORCH_URL = None
    logger.warning("ORCH_URL not configured; remote orchestrator calls disabled")

# Состояние пользователей (минимальное)
user_states = {}

def escape_markdown_v2(text: str) -> str:
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    for char in escape_chars:
        text = text.replace(char, f'\\{char}')
    return text

def format_bartender_response(text: str) -> str:
    if not text:
        return text or ""
    # Заголовки рецептов (делаем жирными)
    text = re.sub(r'^([А-ЯЁA-Z][^:\n]*):?\s*$', r'*\1*', text, flags=re.MULTILINE)
    # Ингредиенты
    text = re.sub(r'^- (.+)$', r'• _\1_', text, flags=re.MULTILINE)
    # Шаги приготовления
    text = re.sub(r'^(\d+)\.\s*(.+)$', r'*\1\.* \2', text, flags=re.MULTILINE)
    # Выделяем названия напитков жирным
    drinks = ['мохито', 'мартини', 'маргарита', 'пина колада', 'космополитен', 'дайкири', 'кайпиринья', 'негрони', 'апероль спритц', 'олд фэшн']
    for drink in drinks:
        pattern = r'\b(' + re.escape(drink) + r')\b'
        text = re.sub(pattern, r'*\1*', text, flags=re.IGNORECASE)
    # Время/температуры
    text = re.sub(r'\b(\d+\s*°C|\d+\s*градус|\d+\s*мин|\d+\s*сек)\b', r'`\1`', text)
    # Количества
    text = re.sub(r'\b(\d+\s*мл|\d+\s*г|\d+\s*ст\.?\s*л\.?|\d+\s*ч\.?\s*л\.?)\b', r'`\1`', text)
    return text

def get_user_info(update: Update) -> str:
    user = update.effective_user
    if not user:
        return "Unknown"
    parts = []
    if user.username: parts.append(f"@{user.username}")
    if user.first_name: parts.append(user.first_name)
    if user.last_name: parts.append(user.last_name)
    return f"{user.id} ({' '.join(parts)})" if parts else str(user.id)

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

# -----------------------
# Handlers
# -----------------------
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
    answer = None
    try:
        uid = update.effective_user.id
        text = (update.message.text or "").strip()
        query = text  # default
        user_info = get_user_info(update)
        logger.info("Пользователь %s написал: %s", uid, text)
        logger.debug("User info: %s", user_info)

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

        # Button shortcuts -> map to query
        if text == "🥤 Безалкогольные":
            query = "Предложи освежающий безалкогольный напиток или моктейль с рецептом"
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
        elif text == "📖 Рецепты":
            query = "Покажи рецепт популярного барного напитка с пошаговым приготовлением"
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
            keyboard = [["🥤 Безалкогольные"], ["🎭 Настроение", "📖 Рецепты"]]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            await update.message.reply_text("🍸 Главное меню бармена:", reply_markup=reply_markup)
            return

        # ------------- call local orchestrator (synchronous) in threadpool -------------
        loop = asyncio.get_running_loop()
        try:
            # pass args directly to avoid lambda closure issues
            resp = await loop.run_in_executor(None, orch_query_sync, uid, query)
        except Exception as e:
            logger.exception("Local orchestrator call failed: %s", e)
            resp = None

        # If local orchestrator did not return anything, try remote ORCH_URL as fallback (optional)
        if not resp and ORCH_URL:
            try:
                r = requests.post(ORCH_URL, json={"user_id": uid, "text": query, "k": 3}, timeout=30)
                if r.status_code == 200:
                    resp = r.json()
                else:
                    logger.warning("Remote orchestrator returned status %s %s", r.status_code, r.text)
            except Exception as e:
                logger.exception("Remote orchestrator call failed: %s", e)

        if not resp:
            # Nothing returned from orchestrator
            answer = "Извините, сейчас сервис недоступен. Попробуйте позже."
            formatted_answer = format_bartender_response(answer)
            await update.message.reply_text(formatted_answer)
            return

        # Handle moderation / blocked
        if resp.get("blocked"):
            reason = resp.get("reason", "Неизвестная причина")
            logger.info("Запрос %s заблокирован модерацией: %s", uid, reason)
            await update.message.reply_text(escape_markdown_v2(f"🚫 Запрос заблокирован модерацией: {reason}"))
            return

        # Extract answer from resp reliably
        # Possible structures handled: {'answer': 'text'} or {'result': {'answer': ...}} or {'result': {'answer': {...}}}
        answer_candidate = None
        if isinstance(resp, dict):
            # top-level 'answer'
            answer_candidate = resp.get("answer") or resp.get("text") or None
            # nested 'result' -> 'answer'
            if not answer_candidate and isinstance(resp.get("result"), dict):
                rres = resp["result"]
                answer_candidate = rres.get("answer") or rres.get("text") or None
        else:
            # fallback string
            answer_candidate = str(resp)

        # If answer is a structured model response (dict/list) try to extract first string leaf
        def find_first_str(obj):
            if obj is None:
                return None
            if isinstance(obj, str):
                return obj
            if isinstance(obj, dict):
                for v in obj.values():
                    s = find_first_str(v)
                    if s:
                        return s
            if isinstance(obj, list):
                for v in obj:
                    s = find_first_str(v)
                    if s:
                        return s
            return None

        if isinstance(answer_candidate, (dict, list)):
            answer_text = find_first_str(answer_candidate) or ""
        else:
            answer_text = str(answer_candidate) if answer_candidate is not None else ""

        if not answer_text:
            answer_text = "Извините, не удалось сгенерировать ответ. Попробуйте переформулировать запрос."

        # Format and send
        formatted_answer = format_bartender_response(answer_text)
        await update.message.reply_text(formatted_answer)

        logger.info("Ответ отправлен пользователю %s", uid)
        logger.debug("Answer preview: %s", answer_text[:300])

    except Exception as e:
        logger.exception("Ошибка в handle_message: %s", e)
        try:
            await update.message.reply_text("😅 Извините, что-то пошло не так. Попробуйте ещё раз.")
        except Exception:
            pass

# -----------------------
# Main
# -----------------------
def main():
    logger.info("🚀 Запуск бота...")

    # Initialize auth once at startup (fail early)
    try:
        start_auth()
    except Exception as e:
        logger.exception("Авторизация не удалась: %s", e)
        logger.error("Завершаю запуск — исправьте конфигурацию аутентификации и перезапустите.")
        return

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    force_rebuild = os.getenv("FORCE_REBUILD_INDEX", "").lower() in ["true", "1", "yes"]

    try:
        if not force_rebuild:
            logger.info("🔄 Проверяю наличие новых файлов для инкрементального обновления...")
            try:
                incremental_success = update_rag_incremental("vedroo")
                if incremental_success:
                    logger.info("✅ Инкрементальное обновление выполнено успешно")
                else:
                    logger.warning("⚠️ Инкрементальное обновление не удалось, выполняю полную перестройку")
                    # full rebuild
                    bucket_name = "vedroo"
                    docs = build_docs_from_s3(bucket_name, "")
                    if docs:
                        ok = build_index(docs)
                        if ok:
                            # --- Build mapping of processed source files -> last_modified (ISO) ---
                            try:
                                # get list of files currently in bucket (key -> info)
                                bucket_files = {f["key"]: f for f in get_bucket_files(bucket_name)}
                                processed = {}
                                for d in docs:
                                    src = d.get("meta", {}).get("source")
                                    if not src:
                                        continue
                                    if src in processed:
                                        continue
                                    info = bucket_files.get(src)
                                    processed[src] = {
                                        "hash": None,  # we don't compute hash here; last_modified is enough for incremental check
                                        "last_modified": info.get("last_modified") if info else None
                                    }

                                state = {"processed_files": processed, "last_update": datetime.now().isoformat()}
                                save_incremental_state(state)
                                logger.info("Incremental state updated with %d files after full rebuild", len(processed))
                            except Exception as e:
                                logger.exception("Failed to update incremental state after full rebuild: %s", e)
                        else:
                            logger.error("build_index failed during full rebuild")
            except Exception as e:
                logger.exception("Ошибка инкрементального обновления, делаю полную перестройку: %s", e)
                build_docs_from_s3("vedroo", "")

        else:
            logger.info("🔄 Принудительная перестройка индекса...")
            build_docs_from_s3("vedroo", "")

        # Попытка загрузить индекс (если он есть)
        try:
            index, vectors, docs = load_index()
            logger.info("📚 Векторный индекс загружен (%d документов)", len(docs))
        except Exception as e:
            logger.warning("FAISS индекс не найден/не удалось загрузить: %s", e)
            logger.info("Можно продолжить — поиск будет недоступен до сборки индекса.")
    except Exception as e:
        logger.exception("❌ Ошибка при работе с индексом: %s", e)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.run_polling()

if __name__ == "__main__":
    main()
