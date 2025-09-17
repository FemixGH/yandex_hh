import requests
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackContext
import logging
import jwt
import time
from dotenv import load_dotenv
import os
import tempfile
import boto3


REQUIRED_VARS = {
    "S3_ENDPOINT": os.getenv("S3_ENDPOINT"),
    "S3_ACCESS_KEY": os.getenv("S3_ACCESS_KEY"),
    "S3_SECRET_KEY": os.getenv("S3_SECRET_KEY"),
    "S3_BUCKET": os.getenv("S3_BUCKET"),
}

for var_name, value in REQUIRED_VARS.items():
    if not value or value.strip().lower() == "none":
        raise ValueError(f"{var_name} не задан. Проверьте .env.")
    
    
# безопасная загрузка из S3
def download_from_s3(key: str):
    # 1. Проверяем что key строка
    if not isinstance(key, str):
        return None
    # 2. Пропускаем папки
    if key.endswith('/'):
        return None
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=REQUIRED_VARS["S3_ENDPOINT"],
            aws_access_key_id=REQUIRED_VARS["S3_ACCESS_KEY"],
            aws_secret_access_key=REQUIRED_VARS["S3_SECRET_KEY"],
        )
        # 3. Проверяем размер до скачивания
        head = s3.head_object(Bucket=REQUIRED_VARS["S3_BUCKET"], Key=key)
        size_before = head.get("ContentLength", 0)

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            s3.download_fileobj(REQUIRED_VARS["S3_BUCKET"], key, tmp)
            tmp.flush()
            size_after = os.path.getsize(tmp.name)

        if size_before != size_after:
            os.remove(tmp.name)
            return None

        return tmp.name
    except Exception as e:
        logging.error(f"Ошибка при скачивании {key}: {e}")
        return None


# валидация содержимого документов
def validate_docs(loaded):
    valid_docs = [
        doc for doc in loaded
        if hasattr(doc, 'page_content')
        and isinstance(doc.page_content, str)
        and doc.page_content.strip()
    ]
    return valid_docs