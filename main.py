import requests
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackContext
import logging
import jwt
import time
from dotenv import load_dotenv
import os


REQUIRED_VARS = {
    "S3_ENDPOINT": os.getenv("S3_ENDPOINT"),
    "S3_ACCESS_KEY": os.getenv("S3_ACCESS_KEY"),
    "S3_SECRET_KEY": os.getenv("S3_SECRET_KEY"),
    "S3_BUCKET": os.getenv("S3_BUCKET"),
}

for var_name, value in REQUIRED_VARS.items():
    if not value or value.strip().lower() == "none":
        raise ValueError(f"{var_name} не задан. Проверьте .env.")