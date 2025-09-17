import requests
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

logger = logging.getLogger(__name__)


# --- ХЕЛПЕРЫ ДЛЯ РЕЗЮМЕ ---
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


# --- API HH ФУНКЦИИ ДЛЯ РЕЗЮМЕ ---
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


# --- ФУНКЦИИ ОТОБРАЖЕНИЯ РЕЗЮМЕ ---
async def show_resume_management(user_states, application, send_message_func, user_id: int, resume: dict):
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

        await send_message_func(
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


async def show_resume_details(user_states, application, send_message_func, user_id: int, resume_id: str):
    """Показ подробной информации о резюме"""
    try:
        st = user_states.get(user_id)
        if not st or not st.get("authorized"):
            await send_message_func(application.bot, user_id, "❌ Необходима авторизация")
            return

        access_token = st["tokens"]["access_token"]
        resume = get_resume_by_id(access_token, resume_id)

        if not resume:
            await send_message_func(application.bot, user_id, "❌ Не удалось получить резюме")
            return

        text = format_resume_detailed(resume)

        # Кнопка возврата
        inline_keyboard = [[InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_list")]]
        inline_markup = InlineKeyboardMarkup(inline_keyboard)

        await send_message_func(application.bot, user_id, text, reply_markup=inline_markup)

    except Exception as e:
        logger.error(f"Ошибка показа деталей резюме {resume_id} для пользователя {user_id}: {e}")


async def start_resume_creation(user_states, application, send_message_func, user_id: int):
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

        await send_message_func(
            application.bot,
            user_id,
            "📝 Создание нового резюме\n\nВведите название должности:"
        )

    except Exception as e:
        logger.error(f"Ошибка начала создания резюме для пользователя {user_id}: {e}")


async def start_resume_editing(user_states, application, send_message_func, user_id: int, resume_id: str):
    """Начало редактирования резюме"""
    try:
        st = user_states.get(user_id)
        if not st or not st.get("authorized"):
            await send_message_func(application.bot, user_id, "❌ Необходима авторизация")
            return

        access_token = st["tokens"]["access_token"]
        resume = get_resume_by_id(access_token, resume_id)

        if not resume:
            await send_message_func(application.bot, user_id, "❌ Не удалось получить резюме для редактирования")
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

        await send_message_func(application.bot, user_id, text, reply_markup=inline_markup)

    except Exception as e:
        logger.error(f"Ошибка начала редактирования резюме {resume_id} для пользователя {user_id}: {e}")


async def handle_text_input(user_states, application, send_message_func, user_id: int, text: str):
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
                await send_message_func(
                    application.bot,
                    user_id,
                    f"✅ Название: {text}\n\nВведите желаемую зарплату (или 0 для пропуска):"
                )
            else:
                st["waiting_for_input"] = None
                await send_message_func(application.bot, user_id, f"✅ Название изменено на: {text}")
                await start_resume_editing(user_states, application, send_message_func, user_id, st.get("edit_resume_id"))

        elif waiting_for == "salary":
            try:
                salary_amount = int(text)
                if salary_amount > 0:
                    edit_data["salary"] = {"amount": salary_amount, "currency": "RUR"}
                else:
                    edit_data["salary"] = None

                if st.get("current_mode") == "create":
                    st["waiting_for_input"] = "skills"
                    await send_message_func(
                        application.bot,
                        user_id,
                        f"✅ Зарплата: {salary_amount if salary_amount > 0 else 'не указана'}\n\nВведите навыки через запятую (или пропустите):"
                    )
                else:
                    st["waiting_for_input"] = None
                    await send_message_func(application.bot, user_id, f"✅ Зарплата изменена на: {salary_amount}")
                    await start_resume_editing(user_states, application, send_message_func, user_id, st.get("edit_resume_id"))
            except ValueError:
                await send_message_func(application.bot, user_id, "❌ Введите число или 0:")

        elif waiting_for == "skills":
            if text.strip():
                skills = [skill.strip() for skill in text.split(",") if skill.strip()]
                edit_data["skills"] = skills
            else:
                edit_data["skills"] = []

            if st.get("current_mode") == "create":
                # Завершаем создание резюме
                await finish_resume_creation(user_states, application, send_message_func, user_id)
            else:
                st["waiting_for_input"] = None
                await send_message_func(application.bot, user_id, f"✅ Навыки изменены")
                await start_resume_editing(user_states, application, send_message_func, user_id, st.get("edit_resume_id"))

        return True  # Обработали как ввод

    except Exception as e:
        logger.error(f"Ошибка обработки текстового ввода для пользователя {user_id}: {e}")
        return False


async def finish_resume_creation(user_states, application, send_message_func, user_id: int):
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
            await send_message_func(application.bot, user_id, "✅ Резюме успешно создано!")
            # Обновляем список резюме
            await refresh_resume_list(user_states, application, send_message_func, user_id)
        else:
            await send_message_func(application.bot, user_id, "❌ Ошибка создания резюме")

        # Сбрасываем состояние
        st["current_mode"] = "browse"
        st["waiting_for_input"] = None
        st["edit_data"] = {}

    except Exception as e:
        logger.error(f"Ошибка завершения создания резюме для пользователя {user_id}: {e}")


async def save_resume_changes(user_states, application, send_message_func, user_id: int):
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
            await send_message_func(application.bot, user_id, "❌ Не удалось получить резюме для сохранения")
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
                await send_message_func(application.bot, user_id, "✅ Резюме успешно обновлено!")
                await refresh_resume_list(user_states, application, send_message_func, user_id)
            else:
                await send_message_func(application.bot, user_id, "❌ Ошибка обновления резюме")
        else:
            await send_message_func(application.bot, user_id, "ℹ️ Нет изменений для сохранения")

        # Сбрасываем состояние
        st["current_mode"] = "browse"
        st["waiting_for_input"] = None
        st["edit_data"] = {}
        st.pop("edit_resume_id", None)

    except Exception as e:
        logger.error(f"Ошибка сохранения изменений резюме для пользователя {user_id}: {e}")


async def delete_resume_confirm(user_states, application, send_message_func, user_id: int, resume_id: str):
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

        await send_message_func(application.bot, user_id, text, reply_markup=inline_markup)

    except Exception as e:
        logger.error(f"Ошибка подтверждения удаления резюме для пользователя {user_id}: {e}")


async def confirm_resume_deletion(user_states, application, send_message_func, user_id: int, resume_id: str):
    """Подтвержденное удаление резюме"""
    try:
        st = user_states.get(user_id)
        if not st or not st.get("authorized"):
            await send_message_func(application.bot, user_id, "❌ Необходима авторизация")
            return

        access_token = st["tokens"]["access_token"]

        if delete_resume(access_token, resume_id):
            await send_message_func(application.bot, user_id, "✅ Резюме удалено")
            await refresh_resume_list(user_states, application, send_message_func, user_id)
        else:
            await send_message_func(application.bot, user_id, "❌ Ошибка удаления резюме")

    except Exception as e:
        logger.error(f"Ошибка удаления резюме {resume_id} для пользователя {user_id}: {e}")


async def refresh_resume_list(user_states, application, send_message_func, user_id: int):
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
            # Нужно вызвать show_current_item из основного модуля
            # Но пока просто отправляем сообщение об обновлении
            await send_message_func(application.bot, user_id, "📋 Список резюме обновлен")
        else:
            st["items"] = []
            await send_message_func(application.bot, user_id, "📋 У вас нет резюме")

    except Exception as e:
        logger.error(f"Ошибка обновления списка резюме для пользователя {user_id}: {e}")


async def show_experience_options(user_states, application, send_message_func, user_id: int):
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

        await send_message_func(application.bot, user_id, text, reply_markup=inline_markup)

    except Exception as e:
        logger.error(f"Ошибка показа опций опыта для пользователя {user_id}: {e}")


async def copy_resume(user_states, application, send_message_func, user_id: int, resume_id: str):
    """Копирование существующего резюме"""
    try:
        st = user_states.get(user_id)
        if not st or not st.get("authorized"):
            await send_message_func(application.bot, user_id, "❌ Необходима авторизация")
            return

        access_token = st["tokens"]["access_token"]
        original_resume = get_resume_by_id(access_token, resume_id)

        if not original_resume:
            await send_message_func(application.bot, user_id, "❌ Не удалось получить резюме для копирования")
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
            await send_message_func(application.bot, user_id, "✅ Резюме успешно скопировано!")
            await refresh_resume_list(user_states, application, send_message_func, user_id)
        else:
            await send_message_func(application.bot, user_id, "❌ Ошибка копирования резюме")

    except Exception as e:
        logger.error(f"Ошибка копирования резюме {resume_id} для пользователя {user_id}: {e}")
