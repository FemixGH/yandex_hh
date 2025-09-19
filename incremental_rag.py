# incremental_rag.py - Инкрементальное обновление RAG индекса
import os
import json
import time
import pickle
import logging
import hashlib
from typing import List, Dict, Tuple, Optional, Set
import boto3
import numpy as np
from datetime import datetime

from settings import VECTORSTORE_DIR, S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY
from bartender_file_handler import extract_text_from_file, chunk_text, download_file_bytes
from faiss_index_yandex import build_index, load_index, semantic_search, VECTORS_FILE, METADATA_FILE
from yandex_api import yandex_batch_embeddings

logger = logging.getLogger(__name__)

INCREMENTAL_STATE_FILE = os.path.join(VECTORSTORE_DIR, "incremental_state.json")

def process_file_for_bartender(file_bytes: bytes, filename: str, file_type: str = None) -> List[Dict]:
    """
    Обрабатывает файл и создает документы для индексации

    Args:
        file_bytes: Содержимое файла в байтах
        filename: Имя файла
        file_type: Тип файла (опционально)

    Returns:
        List[Dict]: Список документов с метаданными
    """
    try:
        # Извлекаем текст из файла
        text = extract_text_from_file(file_bytes, filename)

        if not text or not text.strip():
            logger.warning("Пустой текст извлечен из файла: %s", filename)
            return []

        # Разбиваем текст на чанки
        chunks = chunk_text(text, max_chars=1500)

        if not chunks:
            logger.warning("Не удалось создать чанки из файла: %s", filename)
            return []

        # Создаем документы
        documents = []
        for i, chunk in enumerate(chunks):
            doc = {
                "content": chunk.strip(),
                "metadata": {
                    "source": filename,
                    "chunk_id": i,
                    "total_chunks": len(chunks),
                    "file_type": file_type or filename.split('.')[-1].lower()
                }
            }
            documents.append(doc)

        logger.info("Создано %d документов из файла %s", len(documents), filename)
        return documents

    except Exception as e:
        logger.error("Ошибка при обработке файла %s: %s", filename, e)
        return []

def get_file_hash(content: bytes) -> str:
    """Вычисляет хеш содержимого файла"""
    return hashlib.md5(content).hexdigest()

def load_incremental_state() -> Dict:
    """Загружает состояние инкрементального обновления"""
    if os.path.exists(INCREMENTAL_STATE_FILE):
        try:
            with open(INCREMENTAL_STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Не удалось загрузить состояние инкрементального обновления: %s", e)

    return {
        "processed_files": {},  # file_key -> {"hash": "...", "last_modified": "..."}
        "last_update": None
    }

def save_incremental_state(state: Dict):
    """Сохраняет состояние инкрементального обновления"""
    try:
        os.makedirs(os.path.dirname(INCREMENTAL_STATE_FILE), exist_ok=True)
        with open(INCREMENTAL_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("Не удалось сохранить состояние инкрементального обновления: %s", e)

def get_bucket_files(bucket_name: str) -> List[Dict]:
    """Получает список файлов из S3 бакета с метаданными"""
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT,
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY,
        )

        response = s3.list_objects_v2(Bucket=bucket_name)
        contents = response.get("Contents", [])

        files = []
        for obj in contents:
            key = obj.get("Key", "")
            if key.lower().endswith(('.csv', '.pdf', '.txt', '.json')):
                files.append({
                    "key": key,
                    "last_modified": obj.get("LastModified").isoformat() if obj.get("LastModified") else None,
                    "size": obj.get("Size", 0)
                })

        return files
    except Exception as e:
        logger.error("Ошибка при получении списка файлов из бакета %s: %s", bucket_name, e)
        return []

def find_new_or_modified_files(bucket_name: str, state: Dict) -> List[Dict]:
    """Находит новые или измененные файлы"""
    current_files = get_bucket_files(bucket_name)
    processed_files = state.get("processed_files", {})

    new_or_modified = []

    for file_info in current_files:
        key = file_info["key"]
        last_modified = file_info["last_modified"]

        if key not in processed_files:
            # Новый файл
            logger.info("Найден новый файл: %s", key)
            new_or_modified.append(file_info)
        elif processed_files[key].get("last_modified") != last_modified:
            # Измененный файл
            logger.info("Найден измененный файл: %s", key)
            new_or_modified.append(file_info)

    return new_or_modified

def process_new_files(bucket_name: str, new_files: List[Dict]) -> Tuple[List[np.ndarray], List[Dict]]:
    """Обрабатывает новые файлы и создает эмбеддинги"""
    all_vectors = []
    all_docs = []

    for file_info in new_files:
        key = file_info["key"]
        logger.info("Обрабатываю файл: %s", key)

        try:
            # Скачиваем файл
            content = download_file_bytes(bucket_name, key)

            # Обрабатываем файл в зависимости от типа
            if key.lower().endswith('.csv'):
                docs = process_file_for_bartender(content, key, file_type='csv')
            elif key.lower().endswith('.pdf'):
                docs = process_file_for_bartender(content, key, file_type='pdf')
            elif key.lower().endswith(('.txt', '.json')):
                docs = process_file_for_bartender(content, key, file_type='txt')
            else:
                logger.warning("Неподдерживаемый тип файла: %s", key)
                continue

            if not docs:
                logger.warning("Не удалось извлечь документы из файла: %s", key)
                continue

            # Создаем эмбеддинги для документов
            texts = [doc["content"] for doc in docs]
            vectors = yandex_batch_embeddings(texts)

            if vectors is None or len(vectors) == 0:
                logger.warning("Не удалось создать эмбеддинги для файла: %s", key)
                continue

            # Добавляем метаданные о файле к каждому документу
            for i, doc in enumerate(docs):
                doc["source_file"] = key
                doc["file_hash"] = get_file_hash(content)
                doc["processed_at"] = datetime.now().isoformat()

            all_vectors.extend(vectors)
            all_docs.extend(docs)

            logger.info("Обработан файл %s: %d документов, %d векторов", key, len(docs), len(vectors))

        except Exception as e:
            logger.error("Ошибка при обработке файла %s: %s", key, e)
            continue

    return all_vectors, all_docs

def update_rag_incremental(bucket_name: str) -> bool:
    """
    Выполняет инкрементальное обновление RAG индекса

    Returns:
        bool: True если обновление прошло успешно, False если нужна полная перестройка
    """
    try:
        logger.info("Начинаю инкрементальное обновление RAG индекса...")

        # Загружаем текущее состояние
        state = load_incremental_state()

        # Проверяем существование текущего индекса
        if not os.path.exists(os.path.join(VECTORSTORE_DIR, VECTORS_FILE)) or \
           not os.path.exists(os.path.join(VECTORSTORE_DIR, METADATA_FILE)):
            logger.info("Индекс не существует, требуется полная перестройка")
            return False

        # Находим новые или измененные файлы
        new_files = find_new_or_modified_files(bucket_name, state)

        if not new_files:
            logger.info("Новых или измененных файлов не найдено")
            return True

        logger.info("Найдено новых/измененных файлов: %d", len(new_files))

        # Загружаем существующий индекс
        existing_vectors, existing_docs = load_index()
        logger.info("Загружен существующий индекс: %d документов", len(existing_docs))

        # Обрабатываем новые файлы
        new_vectors, new_docs = process_new_files(bucket_name, new_files)

        if not new_vectors:
            logger.warning("Не удалось обработать ни одного нового файла")
            return True

        logger.info("Обработано новых документов: %d", len(new_docs))

        # Объединяем существующие и новые данные
        all_vectors = existing_vectors + new_vectors
        all_docs = existing_docs + new_docs

        # Пересоздаем индекс с объединенными данными
        vectors_array = np.array(all_vectors)
        build_index(vectors_array, all_docs)

        # Обновляем состояние
        for file_info in new_files:
            key = file_info["key"]
            content = download_file_bytes(bucket_name, key)
            state["processed_files"][key] = {
                "hash": get_file_hash(content),
                "last_modified": file_info["last_modified"]
            }

        state["last_update"] = datetime.now().isoformat()
        save_incremental_state(state)

        logger.info("✅ Инкрементальное обновление завершено успешно. Всего документов: %d", len(all_docs))
        return True

    except Exception as e:
        logger.error("❌ Ошибка при инкрементальном обновлении: %s", e)
        return False

def force_full_rebuild(bucket_name: str):
    """Принудительно сбрасывает состояние для полной перестройки"""
    try:
        if os.path.exists(INCREMENTAL_STATE_FILE):
            os.remove(INCREMENTAL_STATE_FILE)
            logger.info("Состояние инкрементального обновления сброшено")

        # Удаляем существующий индекс
        vectors_file = os.path.join(VECTORSTORE_DIR, VECTORS_FILE)
        metadata_file = os.path.join(VECTORSTORE_DIR, METADATA_FILE)

        if os.path.exists(vectors_file):
            os.remove(vectors_file)
        if os.path.exists(metadata_file):
            os.remove(metadata_file)

        logger.info("Существующий индекс удален, требуется полная перестройка")

    except Exception as e:
        logger.error("Ошибка при сбросе состояния: %s", e)

if __name__ == "__main__":
    # Тестирование инкрементального обновления
    logging.basicConfig(level=logging.INFO)

    bucket_name = "vedroo"
    success = update_rag_incremental(bucket_name)

    if success:
        print("✅ Инкрементальное обновление завершено успешно")
    else:
        print("❌ Требуется полная перестройка индекса")
