# incremental_rag.py - Упрощенное инкрементальное обновление RAG индекса
import os
import json
import logging
import hashlib
from typing import List, Dict, Tuple
import boto3
import numpy as np
from datetime import datetime
import tempfile
from services.rag.embending import yandex_batch_embeddings

logger = logging.getLogger(__name__)


VECTORSTORE_DIR = os.getenv("VECTORSTORE_DIR", "/app/vectorstore")
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")

INCREMENTAL_STATE_FILE = os.path.join(VECTORSTORE_DIR, "incremental_state.json")
VECTORS_FILE = "vectors.npy"
METADATA_FILE = "metadata.json"

# в services/rag/incremental_rag.py (в конец файла)


def update_state_after_full_rebuild(bucket_name: str, docs: List[dict]):
    """
    Помечает исходные S3-файлы из docs как обработанные в incremental_state.json
    docs — список документов с meta.source содержащим S3 key
    """
    try:
        bucket_files = {f["key"]: f for f in get_bucket_files(bucket_name)}
        processed = {}
        for d in docs:
            src = d.get("meta", {}).get("source")
            if not src:
                continue
            if src in processed:
                continue
            info = bucket_files.get(src)
            processed[src] = {
                "hash": None,
                "last_modified": info.get("last_modified") if info else None
            }
        state = {"processed_files": processed, "last_update": datetime.now().isoformat()}

        # atomic write
        path = INCREMENTAL_STATE_FILE
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            # fallback
            save_incremental_state(state)
        logger.info("Updated incremental_state.json with %d files", len(processed))
    except Exception as e:
        logger.exception("Failed to update incremental state after full rebuild: %s", e)



def get_file_hash(content: bytes) -> str:
    """Вычисляет MD5 хеш содержимого файла"""
    return hashlib.md5(content).hexdigest()

def load_incremental_state() -> Dict:
    """Загружает состояние инкрементального обновления"""
    if os.path.exists(INCREMENTAL_STATE_FILE):
        try:
            with open(INCREMENTAL_STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Не удалось загрузить состояние: %s", e)
    
    return {"processed_files": {}, "last_update": None}

def save_incremental_state(state: Dict):
    """Сохраняет состояние инкрементального обновления"""
    try:
        os.makedirs(os.path.dirname(INCREMENTAL_STATE_FILE), exist_ok=True)
        with open(INCREMENTAL_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("Не удалось сохранить состояние: %s", e)

def get_s3_client():
    """Создает клиент для работы с S3"""
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
    )

def download_file_bytes(bucket_name: str, key: str) -> bytes:
    """Скачивает файл из S3"""
    s3 = get_s3_client()
    response = s3.get_object(Bucket=bucket_name, Key=key)
    return response['Body'].read()

def get_bucket_files(bucket_name: str) -> List[Dict]:
    """Получает список текстовых файлов из бакета"""
    try:
        s3 = get_s3_client()
        response = s3.list_objects_v2(Bucket=bucket_name)
        contents = response.get("Contents", [])
        
        return [
            {
                "key": obj["Key"],
                "last_modified": obj["LastModified"].isoformat(),
                "size": obj["Size"]
            }
            for obj in contents 
            if obj["Key"].lower().endswith(('.csv', '.pdf', '.txt', '.json'))
        ]
    except Exception as e:
        logger.error("Ошибка при получении файлов из %s: %s", bucket_name, e)
        return []

def find_new_files(bucket_name: str, state: Dict) -> List[Dict]:
    """Находит новые или измененные файлы"""
    current_files = get_bucket_files(bucket_name)
    processed_files = state.get("processed_files", {})
    
    return [
        file_info for file_info in current_files
        if (file_info["key"] not in processed_files or 
            processed_files[file_info["key"]].get("last_modified") != file_info["last_modified"])
    ]

def extract_text_simple(content: bytes, filename: str) -> str:
    """Упрощенное извлечение текста из файла"""
    # Для текстовых файлов - прямое чтение
    if filename.lower().endswith('.txt'):
        return content.decode('utf-8', errors='ignore')
    
    # Для CSV - простой парсинг
    elif filename.lower().endswith('.csv'):
        text = content.decode('utf-8', errors='ignore')
        lines = text.split('\n')
        return ' '.join([line.strip() for line in lines if line.strip()])
    
    # Для других форматов возвращаем заглушку
    else:
        logger.warning("Формат %s требует специальной обработки", filename)
        return f"Содержимое файла {filename}"

def chunk_text_simple(text: str, max_chars: int = 1000) -> List[str]:
    """Упрощенное разбиение текста на чанки"""
    words = text.split()
    chunks = []
    current_chunk = []
    current_length = 0
    
    for word in words:
        if current_length + len(word) + 1 > max_chars and current_chunk:
            chunks.append(' '.join(current_chunk))
            current_chunk = [word]
            current_length = len(word)
        else:
            current_chunk.append(word)
            current_length += len(word) + 1
    
    if current_chunk:
        chunks.append(' '.join(current_chunk))
    
    return chunks

def process_file_simple(content: bytes, filename: str) -> List[Dict]:
    """Обрабатывает файл и создает документы для индексации"""
    try:
        text = extract_text_simple(content, filename)
        if not text.strip():
            return []
        
        chunks = chunk_text_simple(text)
        documents = []
        
        for i, chunk in enumerate(chunks):
            documents.append({
                "id": f"{filename}__{i}",
                "text": chunk,
                "meta": {
                    "source": filename,
                    "chunk": i,
                    "total_chunks": len(chunks),
                    "processed_at": datetime.now().isoformat()
                }
            })
        
        logger.info("Создано %d чанков из %s", len(documents), filename)
        return documents
        
    except Exception as e:
        logger.error("Ошибка обработки %s: %s", filename, e)
        return []

def load_existing_index() -> Tuple[any, List, List]:
    """Загружает существующий индекс"""
    vectors_path = os.path.join(VECTORSTORE_DIR, VECTORS_FILE)
    metadata_path = os.path.join(VECTORSTORE_DIR, METADATA_FILE)
    
    if not os.path.exists(vectors_path) or not os.path.exists(metadata_path):
        return None, [], []
    
    try:
        vectors = np.load(vectors_path)
        with open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
        return vectors, vectors, metadata
    except Exception as e:
        logger.error("Ошибка загрузки индекса: %s", e)
        return None, [], []

def save_index(vectors: np.ndarray, metadata: List[Dict]):
    """Сохраняет индекс на диск"""
    try:
        os.makedirs(VECTORSTORE_DIR, exist_ok=True)
        np.save(os.path.join(VECTORSTORE_DIR, VECTORS_FILE), vectors)
        with open(os.path.join(VECTORSTORE_DIR, METADATA_FILE), 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Ошибка сохранения индекса: %s", e)
        raise

def update_rag_incremental(bucket_name: str) -> bool:
    """
    Основная функция инкрементального обновления
    
    Returns:
        bool: True если обновление успешно, False если нужна полная перестройка
    """
    try:
        logger.info("🔍 Проверяем новые файлы в бакете %s", bucket_name)
        
        # Загружаем состояние и существующий индекс
        state = load_incremental_state()
        existing_index, existing_vectors, existing_metadata = load_existing_index()
        
        if existing_index is None:
            logger.info("📚 Индекс не найден, требуется полная перестройка")
            return False
        
        # Ищем новые файлы
        new_files = find_new_files(bucket_name, state)
        if not new_files:
            logger.info("✅ Новых файлов не найдено")
            return True
        
        logger.info("📦 Найдено %d новых/измененных файлов", len(new_files))
        
        # Обрабатываем новые файлы
        all_new_docs = []
        all_new_vectors = []
        
        for file_info in new_files:
            try:
                content = download_file_bytes(bucket_name, file_info["key"])
                docs = process_file_simple(content, file_info["key"])
                
                if docs:
                    texts = [doc["text"] for doc in docs]
                    vectors = yandex_batch_embeddings(texts)
                    
                    if vectors and len(vectors) == len(docs):
                        all_new_docs.extend(docs)
                        all_new_vectors.extend(vectors)
                        logger.info("✅ Обработан %s: %d документов", file_info["key"], len(docs))
                    
                    # Обновляем состояние
                    state["processed_files"][file_info["key"]] = {
                        "hash": get_file_hash(content),
                        "last_modified": file_info["last_modified"]
                    }
                    
            except Exception as e:
                logger.error("❌ Ошибка обработки %s: %s", file_info["key"], e)
                continue
        
        if not all_new_docs:
            logger.warning("⚠️ Не удалось обработать новые файлы")
            return True
        
        # Объединяем со старыми данными
        logger.info("🔗 Объединяем %d новых документов с существующими %d", 
                   len(all_new_docs), len(existing_metadata))
        
        all_vectors = np.vstack([existing_vectors, np.array(all_new_vectors, dtype=np.float32)])
        all_metadata = existing_metadata + all_new_docs
        
        # Сохраняем новый индекс
        save_index(all_vectors, all_metadata)
        state["last_update"] = datetime.now().isoformat()
        save_incremental_state(state)
        
        logger.info("✅ Инкрементальное обновление завершено. Всего документов: %d", len(all_metadata))
        return True
        
    except Exception as e:
        logger.error("❌ Фатальная ошибка инкрементального обновления: %s", e)
        return False