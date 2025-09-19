# bartender_file_handler.py - Обработчик файлов для ИИ Бармена
import os
import csv
import json
import logging
import pandas as pd
from typing import List, Dict, Optional
import boto3
import fitz
from settings import S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY

logger = logging.getLogger(__name__)

def download_file_bytes(bucket: str, key: str, endpoint: str = S3_ENDPOINT,
                       access_key: Optional[str] = None, secret_key: Optional[str] = None) -> bytes:
    """Скачивает файл из S3 бакета"""
    access_key = access_key or S3_ACCESS_KEY
    secret_key = secret_key or S3_SECRET_KEY
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    resp = s3.get_object(Bucket=bucket, Key=key)
    return resp["Body"].read()

def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """Извлекает текст из PDF"""
    doc = fitz.Document(stream=pdf_bytes, filetype="pdf")
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n".join(pages)

def extract_text_from_csv_bytes(csv_bytes: bytes, encoding: str = 'utf-8') -> str:
    """
    Извлекает текст из CSV файла, преобразуя его в читаемый формат для барменского ИИ
    """
    try:
        # Пробуем разные кодировки
        for enc in [encoding, 'utf-8', 'cp1251', 'windows-1251', 'latin-1']:
            try:
                csv_text = csv_bytes.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise UnicodeDecodeError("Не удалось декодировать CSV файл")

        # Парсим CSV
        import io
        csv_file = io.StringIO(csv_text)
        reader = csv.DictReader(csv_file)

        extracted_text = []

        # Добавляем заголовок
        if reader.fieldnames:
            extracted_text.append(f"🍸 Барная база данных - структура: {', '.join(reader.fieldnames)}")
            extracted_text.append("=" * 60)

        # Обрабатываем строки
        for i, row in enumerate(reader):
            if i >= 1000:  # Ограничиваем количество строк
                extracted_text.append(f"... и еще {sum(1 for _ in reader) + 1} записей в барной карте")
                break

            # Форматируем строку в барный стиль
            row_text = []
            for field, value in row.items():
                if value and str(value).strip():
                    # Специальное форматирование для барных данных
                    if any(keyword in field.lower() for keyword in ['название', 'name', 'cocktail', 'коктейль']):
                        row_text.append(f"🍹 {field}: {value}")
                    elif any(keyword in field.lower() for keyword in ['ингредиент', 'ingredient', 'состав']):
                        row_text.append(f"🥃 {field}: {value}")
                    elif any(keyword in field.lower() for keyword in ['рецепт', 'recipe', 'приготовление']):
                        row_text.append(f"📝 {field}: {value}")
                    elif any(keyword in field.lower() for keyword in ['алкоголь', 'alcohol', 'градус', 'крепость']):
                        row_text.append(f"🔥 {field}: {value}")
                    else:
                        row_text.append(f"{field}: {value}")

            if row_text:
                extracted_text.append(f"Позиция {i+1}: " + " | ".join(row_text))

        return "\n".join(extracted_text)

    except Exception as e:
        logger.exception("Ошибка обработки CSV: %s", e)
        # Fallback - возвращаем как простой текст
        try:
            return csv_bytes.decode('utf-8', errors='ignore')
        except:
            return csv_bytes.decode('latin-1', errors='ignore')

def extract_text_from_txt_bytes(txt_bytes: bytes, encoding: str = 'utf-8') -> str:
    """Извлекает текст из текстового файла"""
    for enc in [encoding, 'utf-8', 'cp1251', 'windows-1251', 'latin-1']:
        try:
            return txt_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return txt_bytes.decode('latin-1', errors='ignore')

def extract_text_from_json_bytes(json_bytes: bytes, encoding: str = 'utf-8') -> str:
    """Извлекает текст из JSON файла с барным контекстом"""
    try:
        json_text = json_bytes.decode(encoding)
        data = json.loads(json_text)

        # Преобразуем JSON в читаемый текст с барным контекстом
        def json_to_bartender_text(obj, indent=0):
            if isinstance(obj, dict):
                lines = []
                for key, value in obj.items():
                    if isinstance(value, (dict, list)):
                        # Добавляем эмодзи для барных терминов
                        if any(keyword in key.lower() for keyword in ['cocktail', 'коктейль', 'drink']):
                            lines.append("  " * indent + f"🍹 {key}:")
                        elif any(keyword in key.lower() for keyword in ['ingredient', 'ингредиент']):
                            lines.append("  " * indent + f"🥃 {key}:")
                        else:
                            lines.append("  " * indent + f"{key}:")
                        lines.append(json_to_bartender_text(value, indent + 1))
                    else:
                        lines.append("  " * indent + f"{key}: {value}")
                return "\n".join(lines)
            elif isinstance(obj, list):
                lines = []
                for i, item in enumerate(obj[:100]):  # Ограничиваем до 100 элементов
                    if isinstance(item, (dict, list)):
                        lines.append("  " * indent + f"Позиция {i+1}:")
                        lines.append(json_to_bartender_text(item, indent + 1))
                    else:
                        lines.append("  " * indent + f"Позиция {i+1}: {item}")
                if len(obj) > 100:
                    lines.append("  " * indent + f"... и еще {len(obj) - 100} позиций в барной карте")
                return "\n".join(lines)
            else:
                return str(obj)

        return "🍸 Барная база данных (JSON):\n" + json_to_bartender_text(data)

    except Exception as e:
        logger.exception("Ошибка обработки JSON: %s", e)
        return json_bytes.decode('utf-8', errors='ignore')

def extract_text_from_file(file_bytes: bytes, filename: str) -> str:
    """
    Универсальная функция извлечения текста из различных типов файлов для бармена
    """
    filename_lower = filename.lower()

    if filename_lower.endswith('.pdf'):
        return extract_text_from_pdf_bytes(file_bytes)
    elif filename_lower.endswith('.csv'):
        return extract_text_from_csv_bytes(file_bytes)
    elif filename_lower.endswith(('.txt', '.md', '.rst')):
        return extract_text_from_txt_bytes(file_bytes)
    elif filename_lower.endswith('.json'):
        return extract_text_from_json_bytes(file_bytes)
    else:
        # Пробуем как текстовый файл
        logger.warning("Неизвестный тип файла %s, пробуем как текст", filename)
        return extract_text_from_txt_bytes(file_bytes)

def chunk_text(text: str, max_chars: int = 1500) -> List[str]:
    """
    Разбивает текст на фрагменты с учетом ограничений API Yandex
    """
    if not text:
        return []
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    chunks = []
    start = 0
    L = len(text)

    while start < L:
        end = min(start + max_chars, L)
        if end < L:
            # Ищем разрыв строки
            cut_pos = text.rfind('\n', start, end)
            if cut_pos <= start:
                # Ищем пробел
                cut_pos = text.rfind(' ', start, end)
            if cut_pos <= start:
                # Если не нашли, режем принудительно
                cut_pos = end
        else:
            cut_pos = end

        chunk = text[start:cut_pos].strip()
        if chunk:
            chunks.append(chunk)

        if cut_pos <= start:
            start = end
        else:
            start = cut_pos

    return chunks

def build_bartender_index_from_bucket(bucket: str, prefix: str = "", embedding_model_uri: Optional[str] = None,
                                    max_chunk_chars: Optional[int] = None):
    """
    Расширенная версия построения индекса для бармена, поддерживающая множество форматов файлов
    """
    if max_chunk_chars is None:
        try:
            max_chunk_chars = int(os.getenv("YAND_MAX_CHUNK_CHARS", "1500"))
        except Exception:
            max_chunk_chars = 1500

    access_key = S3_ACCESS_KEY
    secret_key = S3_SECRET_KEY
    if not access_key or not secret_key:
        logger.error("S3 access key / secret key not set")
        return

    s3 = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )

    try:
        response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    except Exception as e:
        logger.exception("Ошибка доступа к S3: %s", e)
        return

    contents = response.get("Contents") or []
    if not contents:
        logger.warning("В бакете %s с префиксом '%s' нет файлов", bucket, prefix)
        return

    docs_for_index = []
    supported_extensions = ['.pdf', '.csv', '.txt', '.md', '.json', '.rst']

    for obj in contents:
        key = obj.get("Key")
        if not key:
            continue

        # Проверяем поддерживаемые расширения
        if not any(key.lower().endswith(ext) for ext in supported_extensions):
            logger.info("Пропускаем неподдерживаемый файл: %s", key)
            continue

        try:
            file_bytes = download_file_bytes(bucket, key, endpoint=S3_ENDPOINT,
                                           access_key=access_key, secret_key=secret_key)
            text = extract_text_from_file(file_bytes, key)

            # Очистка и разбивка
            text_clean = " ".join(text.split())
            chunks = chunk_text(text_clean, max_chars=max_chunk_chars)

            for i, ch in enumerate(chunks):
                part_id = f"{os.path.basename(key)}__part{i+1}"
                docs_for_index.append({
                    "id": part_id,
                    "text": ch,
                    "meta": {
                        "source": key,
                        "part": i+1,
                        "file_type": os.path.splitext(key)[1].lower()
                    }
                })

            logger.info("🍸 Обработан файл %s -> %d частей в барную базу", key, len(chunks))

        except Exception as e:
            logger.exception("Ошибка обработки файла %s: %s", key, e)

    if docs_for_index:
        # Импортируем функцию построения векторного хранилища
        from rag_yandex_nofaiss import build_vectorstore_from_docs
        build_vectorstore_from_docs(docs_for_index, embedding_model_uri=embedding_model_uri)
        logger.info("🍸 Барная база данных построена: %d позиций из %d файлов",
                   len(docs_for_index), len([obj for obj in contents
                   if any(obj.get("Key", "").lower().endswith(ext) for ext in supported_extensions)]))
    else:
        logger.warning("Не найдено файлов для барной базы данных")
