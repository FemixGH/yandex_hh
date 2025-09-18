from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackContext
import logger
from yandex_get_token import yandex_bot
from validation import VacancyInput
from LLM import call_llm, extract_json, VACANSY, merge_into_draft, validate_draft_and_get_errors, fields_missing_message


patterns = [
        r"\byour instructions\b",
        r"\byour prompt\b",
        r"\bsystem prompt\b",
        r"\bsystem\s*[:=]\s*",
        r"\byou are\b.*?\b(an?|the)\b.*?\b(assistant|ai|bot|llm|model|hacker|friend|god|master)\b",
        r"\bignore\s+previous\s+instructions?\b",
        r"\bdisregard\s+all\s+prior\s+prompts?\b",
        r"\bas\s+a\s+(friend|developer|admin|god|expert|hacker)\b",
        r"\bact\s+as\s+(if\s+you\s+are|a)\s+(.*)",
        r"\bне\s+следуй\s+предыдущим\s+инструкциям\b",
        r"\bзабудь\s+все\s+инструкции\b",
        r"\bты\s+должен\b.*?\b(игнорировать|забыть|сменить)\b",
        r"\boverride\s+system\s+rules\b",
        r"\bpretend\s+to\s+be\b",
        r"\bfrom\s+now\s+on\b",
        r"\breset\s+your\s+identity\b",
        r"\bnew\s+instructions?\b.*?\b(from|given|are)\b",
        r"\boutput\s+only\b",
        r"\bdo\s+not\s+say\b",
        r"\bне\s+говори\b.*?\b(это|что|никому)\b",
        r"\bsecret\s+word\b",
        r"\bраскрой\s+секрет\b",
        r"\bвыведи\s+весь\s+промпт\b",
        r"\bshow\s+me\s+the\s+system\s+prompt\b",
    ]


def detect_injection(text: str) -> bool:
    for pattern in patterns:
        if pattern.search(text):
            return True
    return False

def get_detected_pattern(text: str) -> str:
    """
        Возвращает первый найденный шаблон, который сработал.
        Для логирования и отладки.
    """
    for pattern in patterns:
        if pattern.search(text):
            return pattern.pattern
    return ""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    await update.message.reply_text(
        "Привет! Я бот для работы с Yandex GPT. Просто напиши мне свой вопрос"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    user_message = update.message.text

    if not user_message.strip():
        await update.message.reply_text("Пожалуйста, введите вопрос")
        return

    try:
        # Показываем статус "печатает"
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action="typing"
        )
        if detect_injection(user_message):
            detected_pattern = get_detected_pattern(user_message)
            logger.warning(f"Prompt injection detected: {detected_pattern}")
            await update.message.reply_text(
                "Обнаружена попытка обхода ограничений. "
                "Ваше сообщение не будет обработано."
            )
            return

        response = yandex_bot.ask_gpt(user_message)
        await update.message.reply_text(response)

    except Exception as e:
        logger.error(f"Error handling message: {str(e)}")
        await update.message.reply_text(
            "Извините, произошла ошибка при обработке вашего запроса. "
            "Пожалуйста, попробуйте позже."
        )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Update {update} caused error {context.error}")
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "Произошла ошибка. Пожалуйста, попробуйте позже."
        )
        

async def handle_vacancy_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text or ""
    # 1) быстрый чек инъекций
    if detect_injection(text):
        await update.message.reply_text("Обнаружена попытка обхода ограничений. Сообщение не обработано.")
        return

    # 2) вызывать LLM для извлечения
    ok, raw = call_llm(text)
    if not ok:
        await update.message.reply_text("Ошибка при анализе текста. Повторите попытку позже.")
        return

    # 3) попытка извлечь JSON
    got, extracted = extract_json(raw)
    if not got:
        # если модель не отдала JSON — уведомим и предложим форму
        await update.message.reply_text(
            "Не удалось автоматически извлечь структуру вакансии из текста. "
            "Пожалуйста, пришлите данные в формате:\n"
            "/vacancy {\"title\":\"...\",\"skills\":\"python,django\",...}\n"
            "Или воспользуйтесь пошаговой формой."
        )
        return

    # 4) сливаем в черновик
    draft = merge_into_draft(user_id, extracted)

    # 5) валидация
    ok_valid, errors = validate_draft_and_get_errors(draft)
    if ok_valid:
        # всё готово — можно показать итог и спросить подтверждение
        # строим текст вывода (кратко)
        final = VacancyInput(**draft)
        resp_text = (
            "Вакансия собрана успешно. Проверьте её:\n\n"
            f"Название: {final.title}\n"
            f"Навыки: {', '.join(final.skills or [])}\n"
            f"Опыт: {final.min_experience_years} лет\n"
            f"Зарплата: {final.salary_from} — {final.salary_to}\n"
            f"Локация: {final.location}\n\n"
            "Нажмите 'Опубликовать' чтобы сохранить или пришлите правки."
        )
        kb = ReplyKeyboardMarkup([["Опубликовать", "Править"]], resize_keyboard=True)
        await update.message.reply_text(resp_text, reply_markup=kb)
        return
    else:
        # сообщаем какие поля некорректны/отсутствуют и просим уточнить
        msg = fields_missing_message(errors)
        await update.message.reply_text(msg)
        return
