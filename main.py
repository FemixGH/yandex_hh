from telegram.ext import Application, CommandHandler, MessageHandler, filters
from handlers import start, handle_message, error_handler
from yandex_get_token import yandex_bot
from logger import logger
from settings import REQUIRED_VARS


for var_name, value in REQUIRED_VARS.items():
    if not value or value.strip().lower() == "none":
        raise ValueError(f"{var_name} не задан. Проверьте .env.")
    

def main():
    """Основная функция"""
    try:
        # Проверяем возможность генерации токена при запуске
        yandex_bot.get_iam_token()
        logger.info("IAM token test successful")

        application = Application.builder().token(REQUIRED_VARS["TELEGRAM_TOKEN"]).build()

        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        application.add_error_handler(error_handler)

        logger.info("Бот запускается...")
        application.run_polling()

    except Exception as e:
        logger.error(f"Failed to start bot: {str(e)}")



if __name__ == "__main__":
    main()    
