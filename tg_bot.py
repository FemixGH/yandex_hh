import os
import logging
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(
            f"ai_bartender_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
            encoding='utf-8'
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Загружаем .env
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Хранилище состояний пользователей
user_states = {}

# Дисклеймер о вреде алкоголя
DISCLAIMER = """
⚠️ ВАЖНОЕ ПРЕДУПРЕЖДЕНИЕ ⚠️

🚫 Чрезмерное употребление алкоголя вредит вашему здоровью
🚫 Алкоголь противопоказан лицам до 18 лет
🚫 Беременным и кормящим женщинам
🚫 Лицам с заболеваниями, при которых противопоказан алкоголь

⚡ Этот бот создан исключительно в развлекательных и образовательных целях
⚡ Мы не призываем к употреблению алкоголя
⚡ Пожалуйста, употребляйте алкоголь ответственно

Если вы согласны с условиями, нажмите "Продолжить" 👇
"""

def get_ai_bartender_response(user_message: str) -> str:
    """
    Заглушка для ИИ бармена. Здесь будет логика обработки запроса
    """
    user_message_lower = user_message.lower()

    # Простые ответы на основе ключевых слов
    if any(word in user_message_lower for word in ['привет', 'здравствуй', 'добро']):
        return "🍸 Добро пожаловать в мой бар! Чем могу помочь? Расскажите, какое у вас настроение или что предпочитаете?"

    elif any(word in user_message_lower for word in ['коктейль', 'напиток', 'выпить']):
        return "🍹 Отличный выбор! Вот несколько предложений:\n\n• Мохито - освежающий с мятой\n• Пина Колада - тропический рай\n• Маргарита - классика с лаймом\n\nКакой стиль вам ближе - легкий и фруктовый или покрепче?"

    elif any(word in user_message_lower for word in ['безалкогольный', 'без алкоголя', 'безалкоголь']):
        return "🥤 Прекрасный выбор! Безалкогольные варианты:\n\n• Виргин Мохито - мята, лайм, содовая\n• Пина Колада безалкогольная\n• Фруктовые смузи\n• Лимонады домашнего приготовления\n\nЧто предпочитаете?"

    elif any(word in user_message_lower for word in ['грустно', 'плохо', 'депресс']):
        return "😔 Понимаю ваше настроение. Но помните - алкоголь не решает проблемы.\n\nЛучше попробуйте:\n🫖 Горячий чай с медом\n☕ Ароматный кофе\n🥛 Молочный коктейль\n\nИногда хорошая беседа помогает больше любого напитка."

    elif any(word in user_message_lower for word in ['рецепт', 'как приготовить']):
        return "👨‍🍳 С удовольствием поделюсь рецептом!\n\nМохито безалкогольный:\n• 10 листиков мяты\n• 1/2 лайма\n• 2 ч.л. сахара\n• Лед\n• Содовая вода\n\nРастолките мяту с сахаром, добавьте сок лайма, лед и долейте содовой. Готово! 🍃"

    else:
        return "🤔 Интересно! Расскажите больше о ваших предпочтениях. Я помогу подобрать идеальный напиток для вашего настроения!"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start с показом дисклеймера"""
    try:
        uid = update.effective_user.id

        # Инициализируем состояние пользователя
        user_states[uid] = {
            "disclaimer_shown": True,
            "accepted_disclaimer": False
        }

        # Показываем дисклеймер
        keyboard = [["✅ Продолжить", "❌ Отказаться"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

        await update.message.reply_text(
            f"🍸 Привет! Я ИИ Бармен!\n\n{DISCLAIMER}",
            reply_markup=reply_markup
        )

        logger.info(f"Пользователь {uid} начал работу с ботом")
    except Exception as e:
        logger.error(f"Ошибка в команде start: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка всех текстовых сообщений"""
    try:
        uid = update.effective_user.id
        text = update.message.text

        logger.info(f"Пользователь {uid} написал: {repr(text)}")

        # Проверяем состояние пользователя
        user_state = user_states.get(uid, {})

        # Если пользователь еще не принял дисклеймер
        if not user_state.get("accepted_disclaimer", False):
            if text == "✅ Продолжить":
                user_states[uid]["accepted_disclaimer"] = True

                keyboard = [["🍹 Коктейли", "🥤 Безалкогольные"], ["🎭 Настроение", "📖 Рецепты"]]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

                await update.message.reply_text(
                    "🎉 Отлично! Теперь я ваш персональный бармен!\n\n"
                    "💬 Просто напишите мне, что хотите, или используйте кнопки ниже.\n"
                    "Я помогу подобрать идеальный напиток для вашего настроения! 🍸",
                    reply_markup=reply_markup
                )
                return

            elif text == "❌ Отказаться":
                await update.message.reply_text(
                    "😔 Жаль, что вы не хотите продолжить.\n"
                    "Если передумаете, используйте команду /start"
                )
                return
            else:
                # Если пользователь написал что-то другое до принятия дисклеймера
                keyboard = [["✅ Продолжить", "❌ Отказаться"]]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

                await update.message.reply_text(
                    "⚠️ Пожалуйста, сначала примите или отклоните условия использования бота.",
                    reply_markup=reply_markup
                )
                return

        # Если дисклеймер принят, обрабатываем запрос
        keyboard = [["🍹 Коктейли", "🥤 Безалкогольные"], ["🎭 Настроение", "📖 Рецепты"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        # Обработка кнопок
        if text == "🍹 Коктейли":
            response = "🍸 Коктейли - это искусство! Расскажите, какой вкус предпочитаете: сладкий, кислый, горький? Или может быть, хотите что-то конкретное?"
        elif text == "🥤 Безалкогольные":
            response = get_ai_bartender_response("безалкогольный")
        elif text == "🎭 Настроение":
            response = "😊 Настроение - это ключ к идеальному напитку! Расскажите, как себя чувствуете: весело, романтично, устали, хотите расслабиться?"
        elif text == "📖 Рецепты":
            response = get_ai_bartender_response("рецепт")
        else:
            # Обычное сообщение - отправляем в ИИ
            response = get_ai_bartender_response(text)

        await update.message.reply_text(response, reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {e}")
        await update.message.reply_text(
            "😅 Извините, что-то пошло не так. Попробуйте еще раз!"
        )

def main():
    """Основная функция запуска бота"""
    try:
        if not TELEGRAM_TOKEN:
            logger.error("TELEGRAM_TOKEN не найден в переменных окружения")
            return

        # Создаем приложение
        application = Application.builder().token(TELEGRAM_TOKEN).build()

        # Добавляем обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        logger.info("🍸 ИИ Бармен запущен!")

        # Запускаем бота
        application.run_polling()

    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")

if __name__ == "__main__":
    main()
