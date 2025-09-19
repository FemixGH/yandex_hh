
import os
import faiss
import pickle
import numpy as np
from typing import List, Optional, Tuple
from yandex_api import yandex_batch_embeddings
import logging

logger = logging.getLogger(__name__)

# Настройки директорий
INDEX_DIR = os.getenv("FAISS_INDEX_DIR", "faiss_index_yandex")
VECTORSTORE_DIR = os.getenv("VECTORSTORE_DIR", "./vectorstore")

# Пути к файлам
METADATA_FILE = os.path.join(VECTORSTORE_DIR, "meta.pkl")
VECTORS_FILE = os.path.join(VECTORSTORE_DIR, "vectors.npy")
IDX_FILE = os.path.join(INDEX_DIR, "index.faiss")

# Создаем директории, если их нет
os.makedirs(INDEX_DIR, exist_ok=True)
os.makedirs(VECTORSTORE_DIR, exist_ok=True)

def build_index(docs: List[dict], model_uri: Optional[str] = None, embeddings: Optional[np.ndarray] = None) -> bool:
    """
    Создает FAISS индекс и сохраняет эмбеддинги

    Args:
        docs: список документов {'id': str, 'text': str, 'meta': {...}}
        model_uri: URI модели для эмбеддингов
        embeddings: предварительно вычисленные эмбеддинги (опционально)

    Returns:
        bool: True если индекс создан успешно
    """
    try:
        if embeddings is None:
            logger.info("Вычисляю эмбеддинги для %d документов...", len(docs))
            texts = [d["text"] for d in docs]
            embeddings_list = yandex_batch_embeddings(texts, model_uri=model_uri)

            if not embeddings_list:
                logger.error("Не удалось получить эмбеддинги")
                return False

            embeddings = np.array(embeddings_list, dtype='float32')
            logger.info("Эмбеддинги вычислены: %s", embeddings.shape)

        dim = embeddings.shape[1]
        n_docs = embeddings.shape[0]

        # Создаем FAISS индекс
        logger.info("Создаю FAISS индекс (dim=%d, n=%d)...", dim, n_docs)
        index = faiss.IndexFlatIP(dim)  # Inner Product для косинусного сходства

        # Нормализуем векторы для косинусного сходства
        faiss.normalize_L2(embeddings)
        index.add(embeddings)

        # Сохраняем FAISS индекс
        logger.info("Сохраняю FAISS индекс в %s", IDX_FILE)
        faiss.write_index(index, IDX_FILE)

        # Сохраняем векторы как numpy массив
        logger.info("Сохраняю векторы в %s", VECTORS_FILE)
        np.save(VECTORS_FILE, embeddings)

        # Сохраняем метаданные
        logger.info("Сохраняю метаданные в %s", METADATA_FILE)
        with open(METADATA_FILE, "wb") as f:
            pickle.dump(docs, f)

        logger.info("✅ FAISS индекс создан и сохранен: %d документов, размерность %d", n_docs, dim)
        return True

    except Exception as e:
        logger.exception("Ошибка при создании FAISS индекса: %s", e)
        return False

def load_index() -> Tuple[faiss.Index, np.ndarray, List[dict]]:
    """
    Загружает FAISS индекс, векторы и метаданные

    Returns:
        Tuple[faiss.Index, np.ndarray, List[dict]]: (индекс, векторы, документы)
    """
    try:
        # Проверяем существование файлов
        if not os.path.exists(IDX_FILE):
            logger.warning("FAISS индекс не найден: %s", IDX_FILE)
            raise FileNotFoundError(f"FAISS индекс не найден: {IDX_FILE}")

        if not os.path.exists(VECTORS_FILE):
            logger.warning("Файл векторов не найден: %s", VECTORS_FILE)
            raise FileNotFoundError(f"Файл векторов не найден: {VECTORS_FILE}")

        if not os.path.exists(METADATA_FILE):
            logger.warning("Файл метаданных не найден: %s", METADATA_FILE)
            raise FileNotFoundError(f"Файл метаданных не найден: {METADATA_FILE}")

        # Загружаем FAISS индекс
        logger.info("Загружаю FAISS индекс из %s", IDX_FILE)
        index = faiss.read_index(IDX_FILE)

        # Загружаем векторы
        logger.info("Загружаю векторы из %s", VECTORS_FILE)
        vectors = np.load(VECTORS_FILE)

        # Загружаем метаданные
        logger.info("Загружаю метаданные из %s", METADATA_FILE)
        with open(METADATA_FILE, "rb") as f:
            docs = pickle.load(f)

        logger.info("✅ Индекс загружен: %d документов, размерность %d", len(docs), vectors.shape[1])
        return index, vectors, docs

    except Exception as e:
        logger.exception("Ошибка при загрузке FAISS индекса: %s", e)
        raise

def semantic_search(query: str, k: int = 3, model_uri: Optional[str] = None) -> List[dict]:
    """
    Выполняет семантический поиск по индексу

    Args:
        query: поисковый запрос
        k: количество результатов
        model_uri: URI модели для эмбеддингов

    Returns:
        List[dict]: список найденных документов с оценками
    """
    try:
        # Загружаем индекс
        index, vectors, docs = load_index()

        # Получаем эмбеддинг запроса
        query_embedding = yandex_batch_embeddings([query], model_uri=model_uri)
        if not query_embedding:
            logger.error("Не удалось получить эмбеддинг для запроса")
            return []

        query_vector = np.array(query_embedding, dtype='float32')
        faiss.normalize_L2(query_vector)

        # Выполняем поиск
        scores, indices = index.search(query_vector, k)

        # Формируем результаты
        results = []
        for i, (score, idx) in enumerate(zip(scores[0], indices[0])):
            if idx < len(docs):
                result = docs[idx].copy()
                result["score"] = float(score)
                result["rank"] = i + 1
                results.append(result)

        logger.info("Найдено %d результатов для запроса", len(results))
        return results

    except Exception as e:
        logger.exception("Ошибка при семантическом поиске: %s", e)
        return []

def check_index_exists() -> bool:
    """
    Проверяет, существует ли индекс

    Returns:
        bool: True если все файлы индекса существуют
    """
    return (os.path.exists(IDX_FILE) and
            os.path.exists(VECTORS_FILE) and
            os.path.exists(METADATA_FILE))

def get_index_info() -> dict:
    """
    Возвращает информацию об индексе

    Returns:
        dict: информация об индексе
    """
    info = {
        "index_exists": check_index_exists(),
        "index_file": IDX_FILE,
        "vectors_file": VECTORS_FILE,
        "metadata_file": METADATA_FILE,
        "index_dir": INDEX_DIR,
        "vectorstore_dir": VECTORSTORE_DIR
    }

    if info["index_exists"]:
        try:
            # Получаем размеры файлов
            info["index_size"] = os.path.getsize(IDX_FILE)
            info["vectors_size"] = os.path.getsize(VECTORS_FILE)
            info["metadata_size"] = os.path.getsize(METADATA_FILE)

            # Загружаем метаданные для подсчета документов
            with open(METADATA_FILE, "rb") as f:
                docs = pickle.load(f)
            info["n_documents"] = len(docs)

            # Загружаем векторы для получения размерности
            vectors = np.load(VECTORS_FILE)
            info["vector_dimension"] = vectors.shape[1]

        except Exception as e:
            logger.warning("Не удалось получить информацию об индексе: %s", e)

    return info

if __name__ == "__main__":
    # Тестирование
    logging.basicConfig(level=logging.INFO)

    info = get_index_info()
    print("Информация об индексе:")
    for key, value in info.items():
        print(f"  {key}: {value}")
