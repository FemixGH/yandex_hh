import os
import requests
import threading
import logging
import asyncio
import json
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler

# Импорт модуля обработки резюме
import resume_handler

# Настройка логирования с правильной кодировкой
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(
            f"bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
            encoding='utf-8'
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Загружаем .env
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8000/callback")

# Хранилище состояний пользователей
user_states = {}

# FastAPI сервер
api = FastAPI()

# Глобальные переменные для синхронизации между потоками
application = None
bot_loop = None


def send_message_sync(chat_id: int, text: str, reply_markup=None):
    """Синхронная отправка сообщения через правильный event loop"""
    if bot_loop and application:
        asyncio.run_coroutine_threadsafe(
            _send_message_async(chat_id, text, reply_markup),
            bot_loop
        )


async def _send_message_async(chat_id: int, text: str, reply_markup=None):
    """Асинхронная отправка сообщения"""
    try:
        await application.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
        logger.info(f"Сообщение отправлено пользователю {chat_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения пользователю {chat_id}: {e}")


async def send_message(bot, chat_id: int, text: str, reply_markup=None, parse_mode='HTML'):
    """Асинхронная отправка сообщения для использования внутри бота"""
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
        logger.info(f"Сообщение отправлено пользователю {chat_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения пользователю {chat_id}: {e}")


def get_my_vacancies(access_token: str, employer_id: int, page: int = 0, per_page: int = 20):
    try:
        url = f"https://api.hh.ru/employers/{employer_id}/vacancies"
        params = {"page": page, "per_page": per_page}
        headers = {"Authorization": f"Bearer {access_token}"}
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code != 200:
            logger.error(f"Ошибка получения вакансий: {resp.status_code}")
            return None
        return resp.json()
    except Exception as e:
        logger.error(f"Исключение при получении вакансий: {e}")
        return None


def show_current_item_sync(user_id: int):
    """Синхронная версия для вызова из FastAPI"""
    if bot_loop:
        asyncio.run_coroutine_threadsafe(
            show_current_item(user_id),
            bot_loop
        )


async def show_current_item(user_id: int):
    try:
        st = user_states.get(user_id)
        if not st:
            logger.warning(f"Попытка показать элемент для несуществующего пользователя {user_id}")
            return

        items = st.get("items", [])
        page = st.get("page", 0)
        if not items:
            await send_message(application.bot, user_id, "Список пуст.")
            return

        role = st.get("role")
        item = items[page]

        if role == "seeker":
            # Для соискателя показываем резюме с управлением
            await resume_handler.show_resume_management(user_states, application, send_message, user_id, item)
        else:
            # Для работодателя показываем вакансию
            text = (
                f"🏢 Вакансия {page + 1}/{len(items)}:\n"
                f"Название: {item.get('name', '')}\n"
                f"Компания: {item.get('employer', {}).get('name', '')}\n"
                f"Город: {item.get('area', {}).get('name', '')}\n"
                f"Зарплата: {resume_handler.format_salary(item.get('salary'))}\n"
                f"Описание: {item.get('snippet', {}).get('requirement', '')}"
            )

            keyboard = [["⏮ Назад", "⏭ Вперёд"], ["🔍 Поиск"]]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            await send_message(application.bot, user_id, text, reply_markup=reply_markup)

        logger.info(f"Показан элемент {page} для пользователя {user_id}")

    except Exception as e:
        logger.error(f"Ошибка показа элемента для пользователя {user_id}: {e}")


async def handle_text_input(user_id: int, text: str):
    """Обработка текстового ввода - делегируем в resume_handler"""
    return await resume_handler.handle_text_input(user_states, application, send_message, user_id, text)



# --- CALLBACK HH ---
@api.get("/callback")
async def hh_callback(code: str, state: str):
    user_id = int(state)
    logger.info(f"Получен callback для пользователя {user_id}")

    # Получаем токены
    token_url = "https://hh.ru/oauth/token"
    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }

    try:
        resp = requests.post(token_url, data=data)
        if resp.status_code != 200:
            logger.error(f"Ошибка получения токенов: {resp.text}")
            return {"error": resp.text}

        tokens = resp.json()
        access_token = tokens.get("access_token")
        if not access_token:
            logger.error("В ответе отсутствует access_token")
            return {"error": "No access_token in response"}

        # Получаем данные о себе
        me_resp = requests.get(
            "https://api.hh.ru/me",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        if me_resp.status_code != 200:
            logger.error(f"Ошибка получения данных пользователя: {me_resp.text}")
            return {"error": me_resp.text}

        me_data = me_resp.json()
        logger.info(f"Получены данные пользователя {user_id}")

        # Определяем роль
        role = None
        if me_data.get("is_applicant", False):
            role = "seeker"
        elif me_data.get("employer", None) is not None:
            role = "employer"
        else:
            role = "seeker"

        # Сохраняем
        user_states[user_id] = {
            "authorized": True,
            "tokens": tokens,
            "role": role,
            "me": me_data,
            "items": [],
            "page": 0,
            "current_mode": "browse",
            "edit_data": {},
            "waiting_for_input": None
        }

        # Отправляем сообщение через правильный event loop
        role_text = "👨‍💻 Соискатель" if role == "seeker" else "🏢 Работодатель"
        send_message_sync(
            user_id,
            f"✅ Авторизация успешна!\nВы вошли как {role_text}"
        )

        if role == "seeker":
            resumes = resume_handler.get_my_resumes(access_token)
            if resumes and resumes.get("items"):
                user_states[user_id]["items"] = resumes["items"]
                logger.info(f"Найдено {len(resumes['items'])} резюме для пользователя {user_id}")
                show_current_item_sync(user_id)
            else:
                logger.warning(f"У пользователя {user_id} нет резюме")
                send_message_sync(user_id,
                                  "⚠️ У вас пока нет резюме.\nИспользуйте кнопку '➕ Новое резюме' для создания.")
        else:
            employer = me_data.get("employer")
            if employer and employer.get("id"):
                vacancies = get_my_vacancies(access_token, employer["id"])
                if vacancies and vacancies.get("items"):
                    user_states[user_id]["items"] = vacancies["items"]
                    logger.info(f"Найдено {len(vacancies['items'])} вакансий для пользователя {user_id}")
                    show_current_item_sync(user_id)
                else:
                    logger.warning(f"У пользователя {user_id} нет вакансий")
                    send_message_sync(user_id, "⚠️ У вас пока нет вакансий.")

        return {"status": "ok", "role": role}

    except Exception as e:
        logger.error(f"Ошибка в callback обработке: {e}")
        return {"error": str(e)}


async def start_search_for_user(user_id: int):
    try:
        keyboard = [["👍 Лайк", "👎 Дизлайк", "⏸ Пауза"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await send_message(application.bot, user_id, "🔍 Начинаем поиск…", reply_markup=reply_markup)
        logger.info(f"Начат поиск для пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка начала поиска для пользователя {user_id}: {e}")


# --- TELEGRAM BOT HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.effective_user.id
        user_states[uid] = {
            "authorized": False,
            "role": None,
            "tokens": None,
            "me": None,
            "items": [],
            "page": 0,
            "current_mode": "browse",
            "edit_data": {},
            "waiting_for_input": None
        }
        await update.message.reply_text(
            "👋 Привет! Это бот для работы с резюме на HH.ru\n\n"
            "Доступный функционал:\n"
            "📋 Просмотр ваших резюме\n"
            "✏️ Редактирование резюме\n"
            "➕ Создание новых резюме\n"
            "🗑 Удаление резюме\n"
            "🔍 Поиск вакансий\n\n"
            "Используйте /auth для авторизации через HH.ru"
        )
        logger.info(f"Пользователь {uid} начал работу с ботом")
    except Exception as e:
        logger.error(f"Ошибка в команде start: {e}")


async def auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.effective_user.id
        auth_url = (
            f"https://hh.ru/oauth/authorize"
            f"?response_type=code"
            f"&client_id={CLIENT_ID}"
            f"&redirect_uri={REDIRECT_URI}"
            f"&state={uid}"
        )
        await update.message.reply_text(
            f"🔑 Перейдите по ссылке для авторизации:\n{auth_url}"
        )
        logger.info(f"Пользователь {uid} запросил авторизацию")
    except Exception as e:
        logger.error(f"Ошибка в команде auth: {e}")


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик inline кнопок"""
    try:
        query = update.callback_query
        await query.answer()

        uid = query.from_user.id
        data = query.data

        logger.info(f"Пользователь {uid} нажал inline кнопку: {data}")

        if data.startswith("resume_details:"):
            resume_id = data.split(":", 1)[1]
            await resume_handler.show_resume_details(user_states, application, send_message, uid, resume_id)

        elif data.startswith("resume_edit:"):
            resume_id = data.split(":", 1)[1]
            await resume_handler.start_resume_editing(user_states, application, send_message, uid, resume_id)

        elif data.startswith("resume_delete:"):
            resume_id = data.split(":", 1)[1]
            await resume_handler.delete_resume_confirm(user_states, application, send_message, uid, resume_id)

        elif data.startswith("resume_copy:"):
            resume_id = data.split(":", 1)[1]
            await resume_handler.copy_resume(user_states, application, send_message, uid, resume_id)

        elif data.startswith("confirm_delete:"):
            resume_id = data.split(":", 1)[1]
            await resume_handler.confirm_resume_deletion(user_states, application, send_message, uid, resume_id)

        elif data == "back_to_list":
            st = user_states.get(uid, {})
            st["current_mode"] = "browse"
            st["waiting_for_input"] = None
            st["edit_data"] = {}
            await show_current_item(uid)

        elif data == "edit_title":
            st = user_states.get(uid, {})
            st["waiting_for_input"] = "title"
            await send_message(application.bot, uid, "✏️ Введите новое название должности:")

        elif data == "edit_salary":
            st = user_states.get(uid, {})
            st["waiting_for_input"] = "salary"
            await send_message(application.bot, uid, "💰 Введите желаемую зарплату (или 0 для удаления):")

        elif data == "edit_experience":
            await resume_handler.show_experience_options(user_states, application, send_message, uid)

        elif data == "edit_skills":
            st = user_states.get(uid, {})
            st["waiting_for_input"] = "skills"
            current_skills = ", ".join(st.get("edit_data", {}).get("skills", []))
            await send_message(
                application.bot,
                uid,
                f"🔧 Текущие навыки: {current_skills or 'не указаны'}\n\nВведите новые навыки через запятую:"
            )

        elif data == "save_resume":
            await resume_handler.save_resume_changes(user_states, application, send_message, uid)

        elif data.startswith("exp_"):
            exp_id = data.replace("exp_", "")
            st = user_states.get(uid, {})
            st["edit_data"]["experience"] = {"id": exp_id}
            await send_message(application.bot, uid, f"✅ Опыт работы изменен")
            await resume_handler.start_resume_editing(user_states, application, send_message, uid, st.get("edit_resume_id"))

    except Exception as e:
        logger.error(f"Ошибка обработки callback query: {e}")



async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.effective_user.id
        st = user_states.get(uid, {})
        text = update.message.text

        logger.info(f"Пользователь {uid} нажал кнопку: {repr(text)}")

        # Сначала проверяем, ждем ли мы текстовый ввод
        if await handle_text_input(uid, text):
            return  # Обработали как ввод, выходим

        # Обычные кнопки навигации
        if text == "⏮ Назад":
            if st.get("page", 0) > 0:
                st["page"] -= 1
                await show_current_item(uid)
            else:
                await update.message.reply_text("Вы на первой странице.")

        elif text == "⏭ Вперёд":
            if st.get("page", 0) < len(st.get("items", [])) - 1:
                st["page"] += 1
                await show_current_item(uid)
            else:
                await update.message.reply_text("Вы на последней странице.")

        elif text == "➕ Новое резюме":
            if not st.get("authorized"):
                await update.message.reply_text("❌ Необходима авторизация (/auth)")
                return
            await resume_handler.start_resume_creation(user_states, application, send_message, uid)

        elif text == "🔍 Поиск":
            await start_search_for_user(uid)

        elif text == "👍 Лайк":
            await update.message.reply_text("❤️ Лайк")

        elif text == "👎 Дизлайк":
            await update.message.reply_text("❌ Дизлайк")

        elif text == "⏸ Пауза":
            # Возвращаемся к просмотру резюме/вакансий
            if st.get("authorized") and st.get("items"):
                await show_current_item(uid)
            else:
                keyboard = [["🔍 Поиск"]]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                await update.message.reply_text("⏸ Поиск приостановлен", reply_markup=reply_markup)

        else:
            await update.message.reply_text("Неизвестная команда.")

    except Exception as e:
        logger.error(f"Ошибка обработки кнопки: {e}")


def run_bot():
    global application, bot_loop
    try:
        # Создаем новый event loop для бота
        bot_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(bot_loop)

        application = Application.builder().token(TELEGRAM_TOKEN).build()

        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("auth", auth))
        application.add_handler(CallbackQueryHandler(handle_callback_query))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_buttons))

        logger.info("Бот запущен")
        application.run_polling()
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")


if __name__ == "__main__":
    logger.info("Запуск приложения")

    # Запускаем бота в отдельном потоке
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # Даем время боту инициализироваться
    import time

    time.sleep(2)

    # Запускаем FastAPI
    import uvicorn

    uvicorn.run(api, host="0.0.0.0", port=8000)