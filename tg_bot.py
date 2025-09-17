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
# {
#   user_id: {
#       authorized: True/False,
#       role: "seeker"/"employer",
#       tokens: {...},
#       me: {...},
#       items: [...],
#       page: 0,
#       current_mode: "browse"/"edit"/"create",
#       edit_data: {...},
#       waiting_for_input: None/"title"/"salary"/"experience" и т.д.
#   }
# }

# FastAPI сервер
api = FastAPI()

# Глобальные переменные для синхронизации между потоками
application = None
bot_loop = None


# --- ХЕЛПЕРЫ ---
def format_salary(salary):
    """Форматирует зарплату, безопасно обрабатывая None"""
    if not salary:
        return "—"
    frm = salary.get("from") or ""
    to = salary.get("to") or ""
    cur = salary.get("currency") or ""
    if frm and to:
        return f"{frm}–{to} {cur}"
    if frm:
        return f"от {frm} {cur}"
    if to:
        return f"до {to} {cur}"
    return cur or "—"


def safe_get_experience(exp):
    """Безопасно достаём опыт работы"""
    if not exp:
        return "—"
    if isinstance(exp, dict):
        return exp.get("name", "—")
    if isinstance(exp, list):
        return ", ".join([e.get("name", "—") for e in exp if isinstance(e, dict)])
    return str(exp)


def format_resume_detailed(resume):
    """Подробное форматирование резюме"""
    text = f"📄 {resume.get('title', 'Без названия')}\n\n"

    # Основная информация
    text += f"💼 Зарплата: {format_salary(resume.get('salary'))}\n"
    text += f"🌍 Город: {resume.get('area', {}).get('name', '—')}\n"
    text += f"⭐ Опыт: {safe_get_experience(resume.get('experience'))}\n"
    text += f"📅 Обновлено: {resume.get('updated_at', '—')[:10]}\n"
    text += f"👁 Просмотров: {resume.get('views_count', 0)}\n"
    text += f"📞 Контакты: {resume.get('contact', [{}])[0].get('value', '—') if resume.get('contact') else '—'}\n"

    # Навыки
    skills = resume.get('skill_set', [])
    if skills:
        text += f"\n🔧 Навыки: {', '.join(skills[:5])}"
        if len(skills) > 5:
            text += f" и ещё {len(skills) - 5}"
        text += "\n"

    # Образование
    education = resume.get('education', {})
    if education.get('level'):
        text += f"🎓 Образование: {education['level'].get('name', '—')}\n"

    # Статус
    status = resume.get('status', {}).get('name', 'Неизвестно')
    text += f"📊 Статус: {status}\n"

    return text


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


# --- API HH ФУНКЦИИ ---
def get_my_resumes(access_token: str):
    try:
        url = "https://api.hh.ru/resumes/mine"
        headers = {"Authorization": f"Bearer {access_token}"}
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            logger.error(f"Ошибка получения резюме: {resp.status_code}")
            return None
        return resp.json()
    except Exception as e:
        logger.error(f"Исключение при получении резюме: {e}")
        return None


def get_resume_by_id(access_token: str, resume_id: str):
    """Получение полной информации о резюме"""
    try:
        url = f"https://api.hh.ru/resumes/{resume_id}"
        headers = {"Authorization": f"Bearer {access_token}"}
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            logger.error(f"Ошибка получения резюме {resume_id}: {resp.status_code}")
            return None
        return resp.json()
    except Exception as e:
        logger.error(f"Исключение при получении резюме {resume_id}: {e}")
        return None


def create_resume(access_token: str, resume_data: dict):
    """Создание нового резюме"""
    try:
        url = "https://api.hh.ru/resumes"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        resp = requests.post(url, headers=headers, json=resume_data)
        if resp.status_code not in [201, 200]:
            logger.error(f"Ошибка создания резюме: {resp.status_code}, {resp.text}")
            return None
        return resp.json()
    except Exception as e:
        logger.error(f"Исключение при создании резюме: {e}")
        return None


def update_resume(access_token: str, resume_id: str, resume_data: dict):
    """Обновление резюме"""
    try:
        url = f"https://api.hh.ru/resumes/{resume_id}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        resp = requests.put(url, headers=headers, json=resume_data)
        if resp.status_code not in [200, 204]:
            logger.error(f"Ошибка обновления резюме {resume_id}: {resp.status_code}, {resp.text}")
            return None
        return True
    except Exception as e:
        logger.error(f"Исключение при обновлении резюме {resume_id}: {e}")
        return None


def delete_resume(access_token: str, resume_id: str):
    """Удаление резюме"""
    try:
        url = f"https://api.hh.ru/resumes/{resume_id}"
        headers = {"Authorization": f"Bearer {access_token}"}
        resp = requests.delete(url, headers=headers)
        if resp.status_code not in [204, 200]:
            logger.error(f"Ошибка удаления резюме {resume_id}: {resp.status_code}")
            return False
        return True
    except Exception as e:
        logger.error(f"Исключение при удалении резюме {resume_id}: {e}")
        return False


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


# --- ФУНКЦИИ ОТОБРАЖЕНИЯ ---
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
            await show_resume_management(user_id, item)
        else:
            # Для работодателя показываем вакансию
            text = (
                f"🏢 Вакансия {page + 1}/{len(items)}:\n"
                f"Название: {item.get('name', '')}\n"
                f"Компания: {item.get('employer', {}).get('name', '')}\n"
                f"Город: {item.get('area', {}).get('name', '')}\n"
                f"Зарплата: {format_salary(item.get('salary'))}\n"
                f"Описание: {item.get('snippet', {}).get('requirement', '')}"
            )

            keyboard = [["⏮ Назад", "⏭ Вперёд"], ["🔍 Поиск"]]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            await send_message(application.bot, user_id, text, reply_markup=reply_markup)

        logger.info(f"Показан элемент {page} для пользователя {user_id}")

    except Exception as e:
        logger.error(f"Ошибка показа элемента для пользователя {user_id}: {e}")


async def show_resume_management(user_id: int, resume: dict):
    """Показ резюме с кнопками управления"""
    try:
        st = user_states.get(user_id, {})
        items = st.get("items", [])
        page = st.get("page", 0)

        text = f"📄 Резюме {page + 1}/{len(items)}:\n"
        text += f"Название: {resume.get('title', 'Без названия')}\n"
        text += f"Зарплата: {format_salary(resume.get('salary'))}\n"
        text += f"Город: {resume.get('area', {}).get('name', '—')}\n"
        text += f"Опыт: {safe_get_experience(resume.get('experience'))}\n"
        text += f"Обновлено: {resume.get('updated_at', '—')[:10]}\n"

        # Inline кнопки для управления резюме
        inline_keyboard = [
            [
                InlineKeyboardButton("👁 Детали", callback_data=f"resume_details:{resume['id']}"),
                InlineKeyboardButton("✏️ Редактировать", callback_data=f"resume_edit:{resume['id']}")
            ],
            [
                InlineKeyboardButton("🗑 Удалить", callback_data=f"resume_delete:{resume['id']}"),
                InlineKeyboardButton("📋 Копировать", callback_data=f"resume_copy:{resume['id']}")
            ]
        ]
        inline_markup = InlineKeyboardMarkup(inline_keyboard)

        # Обычные кнопки навигации
        keyboard = [
            ["⏮ Назад", "⏭ Вперёд"],
            ["➕ Новое резюме", "🔍 Поиск"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        await send_message(
            application.bot,
            user_id,
            text,
            reply_markup=reply_markup
        )

        # Отправляем второе сообщение с inline кнопками
        await application.bot.send_message(
            chat_id=user_id,
            text="Выберите действие:",
            reply_markup=inline_markup
        )

    except Exception as e:
        logger.error(f"Ошибка показа управления резюме для пользователя {user_id}: {e}")


async def show_resume_details(user_id: int, resume_id: str):
    """Показ подробной информации о резюме"""
    try:
        st = user_states.get(user_id)
        if not st or not st.get("authorized"):
            await send_message(application.bot, user_id, "❌ Необходима авторизация")
            return

        access_token = st["tokens"]["access_token"]
        resume = get_resume_by_id(access_token, resume_id)

        if not resume:
            await send_message(application.bot, user_id, "❌ Не удалось получить резюме")
            return

        text = format_resume_detailed(resume)

        # Кнопка возврата
        inline_keyboard = [[InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_list")]]
        inline_markup = InlineKeyboardMarkup(inline_keyboard)

        await send_message(application.bot, user_id, text, reply_markup=inline_markup)

    except Exception as e:
        logger.error(f"Ошибка показа деталей резюме {resume_id} для пользователя {user_id}: {e}")


async def start_resume_creation(user_id: int):
    """Начало создания нового резюме"""
    try:
        st = user_states.get(user_id, {})
        st["current_mode"] = "create"
        st["edit_data"] = {
            "title": "",
            "salary": {"amount": None, "currency": "RUR"},
            "experience": {"id": "noExperience"},
            "skills": [],
            "contacts": []
        }
        st["waiting_for_input"] = "title"

        await send_message(
            application.bot,
            user_id,
            "📝 Создание нового резюме\n\nВведите название должности:"
        )

    except Exception as e:
        logger.error(f"Ошибка начала создания резюме для пользователя {user_id}: {e}")


async def start_resume_editing(user_id: int, resume_id: str):
    """Начало редактирования резюме"""
    try:
        st = user_states.get(user_id)
        if not st or not st.get("authorized"):
            await send_message(application.bot, user_id, "❌ Необходима авторизация")
            return

        access_token = st["tokens"]["access_token"]
        resume = get_resume_by_id(access_token, resume_id)

        if not resume:
            await send_message(application.bot, user_id, "❌ Не удалось получить резюме для редактирования")
            return

        st["current_mode"] = "edit"
        st["edit_resume_id"] = resume_id
        st["edit_data"] = {
            "title": resume.get("title", ""),
            "salary": resume.get("salary", {}),
            "experience": resume.get("experience", {}),
            "skills": resume.get("skill_set", []),
        }

        text = "✏️ Редактирование резюме\n\nВыберите что изменить:"

        inline_keyboard = [
            [InlineKeyboardButton("📝 Название", callback_data="edit_title")],
            [InlineKeyboardButton("💰 Зарплата", callback_data="edit_salary")],
            [InlineKeyboardButton("⭐ Опыт", callback_data="edit_experience")],
            [InlineKeyboardButton("🔧 Навыки", callback_data="edit_skills")],
            [InlineKeyboardButton("💾 Сохранить", callback_data="save_resume")],
            [InlineKeyboardButton("🔙 Отмена", callback_data="back_to_list")]
        ]
        inline_markup = InlineKeyboardMarkup(inline_keyboard)

        await send_message(application.bot, user_id, text, reply_markup=inline_markup)

    except Exception as e:
        logger.error(f"Ошибка начала редактирования резюме {resume_id} для пользователя {user_id}: {e}")


async def handle_text_input(user_id: int, text: str):
    """Обработка текстового ввода"""
    try:
        st = user_states.get(user_id, {})
        waiting_for = st.get("waiting_for_input")

        if not waiting_for:
            return False  # Не обрабатываем как ввод

        edit_data = st.get("edit_data", {})

        if waiting_for == "title":
            edit_data["title"] = text
            if st.get("current_mode") == "create":
                st["waiting_for_input"] = "salary"
                await send_message(
                    application.bot,
                    user_id,
                    f"✅ Название: {text}\n\nВведите желаемую зарплату (или 0 для пропуска):"
                )
            else:
                st["waiting_for_input"] = None
                await send_message(application.bot, user_id, f"✅ Название изменено на: {text}")
                await start_resume_editing(user_id, st.get("edit_resume_id"))

        elif waiting_for == "salary":
            try:
                salary_amount = int(text)
                if salary_amount > 0:
                    edit_data["salary"] = {"amount": salary_amount, "currency": "RUR"}
                else:
                    edit_data["salary"] = None

                if st.get("current_mode") == "create":
                    st["waiting_for_input"] = "skills"
                    await send_message(
                        application.bot,
                        user_id,
                        f"✅ Зарплата: {salary_amount if salary_amount > 0 else 'не указана'}\n\nВведите навыки через запятую (или пропустите):"
                    )
                else:
                    st["waiting_for_input"] = None
                    await send_message(application.bot, user_id, f"✅ Зарплата изменена на: {salary_amount}")
                    await start_resume_editing(user_id, st.get("edit_resume_id"))
            except ValueError:
                await send_message(application.bot, user_id, "❌ Введите число или 0:")

        elif waiting_for == "skills":
            if text.strip():
                skills = [skill.strip() for skill in text.split(",") if skill.strip()]
                edit_data["skills"] = skills
            else:
                edit_data["skills"] = []

            if st.get("current_mode") == "create":
                # Завершаем создание резюме
                await finish_resume_creation(user_id)
            else:
                st["waiting_for_input"] = None
                await send_message(application.bot, user_id, f"✅ Навыки изменены")
                await start_resume_editing(user_id, st.get("edit_resume_id"))

        return True  # Обработали как ввод

    except Exception as e:
        logger.error(f"Ошибка обработки текстового ввода для пользователя {user_id}: {e}")
        return False


async def finish_resume_creation(user_id: int):
    """Завершение создания резюме"""
    try:
        st = user_states.get(user_id)
        if not st or st.get("current_mode") != "create":
            return

        access_token = st["tokens"]["access_token"]
        edit_data = st["edit_data"]

        # Формируем данные для создания резюме
        resume_data = {
            "title": edit_data["title"],
            "area": {"id": "1"},  # Москва по умолчанию
            "experience": {"id": "noExperience"},
            "education": {"level": {"id": "higher"}},
            "language": [{"id": "ru", "level": {"id": "native"}}],
            "schedule": {"id": "fullDay"},
            "employment": [{"id": "full"}],
            "contacts": [{"type": {"id": "phone"}, "value": "+7"}]
        }

        if edit_data.get("salary") and edit_data["salary"].get("amount"):
            resume_data["salary"] = {
                "amount": edit_data["salary"]["amount"],
                "currency": edit_data["salary"]["currency"]
            }

        if edit_data.get("skills"):
            resume_data["skill_set"] = edit_data["skills"]

        # Создаем резюме
        result = create_resume(access_token, resume_data)

        if result:
            await send_message(application.bot, user_id, "✅ Резюме успешно создано!")
            # Обновляем список резюме
            await refresh_resume_list(user_id)
        else:
            await send_message(application.bot, user_id, "❌ Ошибка создания резюме")

        # Сбрасываем состояние
        st["current_mode"] = "browse"
        st["waiting_for_input"] = None
        st["edit_data"] = {}

    except Exception as e:
        logger.error(f"Ошибка завершения создания резюме для пользователя {user_id}: {e}")


async def save_resume_changes(user_id: int):
    """Сохранение изменений резюме"""
    try:
        st = user_states.get(user_id)
        if not st or st.get("current_mode") != "edit":
            return

        access_token = st["tokens"]["access_token"]
        resume_id = st.get("edit_resume_id")
        edit_data = st["edit_data"]

        # Получаем текущее резюме
        current_resume = get_resume_by_id(access_token, resume_id)
        if not current_resume:
            await send_message(application.bot, user_id, "❌ Не удалось получить резюме для сохранения")
            return

        # Обновляем только измененные поля
        update_data = {}

        if edit_data.get("title"):
            update_data["title"] = edit_data["title"]

        if edit_data.get("salary"):
            update_data["salary"] = edit_data["salary"]

        if edit_data.get("skills"):
            update_data["skill_set"] = edit_data["skills"]

        # Сохраняем изменения
        if update_data:
            result = update_resume(access_token, resume_id, update_data)
            if result:
                await send_message(application.bot, user_id, "✅ Резюме успешно обновлено!")
                await refresh_resume_list(user_id)
            else:
                await send_message(application.bot, user_id, "❌ Ошибка обновления резюме")
        else:
            await send_message(application.bot, user_id, "ℹ️ Нет изменений для сохранения")

        # Сбрасываем состояние
        st["current_mode"] = "browse"
        st["waiting_for_input"] = None
        st["edit_data"] = {}
        st.pop("edit_resume_id", None)

    except Exception as e:
        logger.error(f"Ошибка сохранения изменений резюме для пользователя {user_id}: {e}")


async def delete_resume_confirm(user_id: int, resume_id: str):
    """Подтверждение удаления резюме"""
    try:
        text = "⚠️ Вы уверены, что хотите удалить это резюме?\nЭто действие нельзя отменить."

        inline_keyboard = [
            [
                InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_delete:{resume_id}"),
                InlineKeyboardButton("❌ Отмена", callback_data="back_to_list")
            ]
        ]
        inline_markup = InlineKeyboardMarkup(inline_keyboard)

        await send_message(application.bot, user_id, text, reply_markup=inline_markup)

    except Exception as e:
        logger.error(f"Ошибка подтверждения удаления резюме для пользователя {user_id}: {e}")


async def confirm_resume_deletion(user_id: int, resume_id: str):
    """Подтвержденное удаление резюме"""
    try:
        st = user_states.get(user_id)
        if not st or not st.get("authorized"):
            await send_message(application.bot, user_id, "❌ Необходима авторизация")
            return

        access_token = st["tokens"]["access_token"]

        if delete_resume(access_token, resume_id):
            await send_message(application.bot, user_id, "✅ Резюме удалено")
            await refresh_resume_list(user_id)
        else:
            await send_message(application.bot, user_id, "❌ Ошибка удаления резюме")

    except Exception as e:
        logger.error(f"Ошибка удаления резюме {resume_id} для пользователя {user_id}: {e}")


async def refresh_resume_list(user_id: int):
    """Обновление списка резюме"""
    try:
        st = user_states.get(user_id)
        if not st or not st.get("authorized"):
            return

        access_token = st["tokens"]["access_token"]
        resumes = get_my_resumes(access_token)

        if resumes and resumes.get("items"):
            st["items"] = resumes["items"]
            st["page"] = 0
            await show_current_item(user_id)
        else:
            st["items"] = []
            await send_message(application.bot, user_id, "📋 У вас нет резюме")

    except Exception as e:
        logger.error(f"Ошибка обновления списка резюме для пользователя {user_id}: {e}")


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
            resumes = get_my_resumes(access_token)
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
            await show_resume_details(uid, resume_id)

        elif data.startswith("resume_edit:"):
            resume_id = data.split(":", 1)[1]
            await start_resume_editing(uid, resume_id)

        elif data.startswith("resume_delete:"):
            resume_id = data.split(":", 1)[1]
            await delete_resume_confirm(uid, resume_id)

        elif data.startswith("resume_copy:"):
            resume_id = data.split(":", 1)[1]
            # Копирование резюме (создание на основе существующего)
            await copy_resume(uid, resume_id)

        elif data.startswith("confirm_delete:"):
            resume_id = data.split(":", 1)[1]
            await confirm_resume_deletion(uid, resume_id)

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
            await show_experience_options(uid)

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
            await save_resume_changes(uid)

        elif data.startswith("exp_"):
            exp_id = data.replace("exp_", "")
            st = user_states.get(uid, {})
            st["edit_data"]["experience"] = {"id": exp_id}
            await send_message(application.bot, uid, f"✅ Опыт работы изменен")
            await start_resume_editing(uid, st.get("edit_resume_id"))

    except Exception as e:
        logger.error(f"Ошибка обработки callback query: {e}")


async def show_experience_options(user_id: int):
    """Показ опций опыта работы"""
    try:
        text = "⭐ Выберите ваш опыт работы:"

        inline_keyboard = [
            [InlineKeyboardButton("Нет опыта", callback_data="exp_noExperience")],
            [InlineKeyboardButton("От 1 года до 3 лет", callback_data="exp_between1And3")],
            [InlineKeyboardButton("От 3 до 6 лет", callback_data="exp_between3And6")],
            [InlineKeyboardButton("Более 6 лет", callback_data="exp_moreThan6")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_edit")]
        ]
        inline_markup = InlineKeyboardMarkup(inline_keyboard)

        await send_message(application.bot, user_id, text, reply_markup=inline_markup)

    except Exception as e:
        logger.error(f"Ошибка показа опций опыта для пользователя {user_id}: {e}")


async def copy_resume(user_id: int, resume_id: str):
    """Копирование существующего резюме"""
    try:
        st = user_states.get(user_id)
        if not st or not st.get("authorized"):
            await send_message(application.bot, user_id, "❌ Необходима авторизация")
            return

        access_token = st["tokens"]["access_token"]
        original_resume = get_resume_by_id(access_token, resume_id)

        if not original_resume:
            await send_message(application.bot, user_id, "❌ Не удалось получить резюме для копирования")
            return

        # Формируем данные для нового резюме на основе существующего
        resume_data = {
            "title": f"{original_resume.get('title', '')} (копия)",
            "area": original_resume.get("area", {"id": "1"}),
            "experience": original_resume.get("experience", {"id": "noExperience"}),
            "education": original_resume.get("education", {"level": {"id": "higher"}}),
            "language": original_resume.get("language", [{"id": "ru", "level": {"id": "native"}}]),
            "schedule": original_resume.get("schedule", {"id": "fullDay"}),
            "employment": original_resume.get("employment", [{"id": "full"}]),
            "contacts": original_resume.get("contact", [{"type": {"id": "phone"}, "value": "+7"}])
        }

        if original_resume.get("salary"):
            resume_data["salary"] = original_resume["salary"]

        if original_resume.get("skill_set"):
            resume_data["skill_set"] = original_resume["skill_set"]

        # Создаем копию
        result = create_resume(access_token, resume_data)

        if result:
            await send_message(application.bot, user_id, "✅ Резюме успешно скопировано!")
            await refresh_resume_list(user_id)
        else:
            await send_message(application.bot, user_id, "❌ Ошибка копирования резюме")

    except Exception as e:
        logger.error(f"Ошибка копирования резюме {resume_id} для пользователя {user_id}: {e}")


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
            await start_resume_creation(uid)

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