# test_build_index.py
import logging
import os
from rag_yandex_nofaiss import build_index_from_bucket
from faiss_index_yandex import load_index

logging.basicConfig(level=logging.INFO)

# ⚠️ Укажи свои переменные
BUCKET = os.getenv("S3_BUCKET", "your-bucket-name")
PREFIX = os.getenv("S3_PREFIX", "vedroo/")  # если папка внутри бакета
EMBEDDING_MODEL_URI = os.getenv("EMBED_MODEL", None)

def main():
    logging.info("🔧 Запускаю build_index_from_bucket...")
    build_index_from_bucket(bucket=BUCKET, prefix=PREFIX, embedding_model_uri=EMBEDDING_MODEL_URI)

    logging.info("✅ Проверяю, что индекс реально создался...")
    try:
        index, mat, docs = load_index()
        logging.info("🎉 Индекс найден! Размер матрицы: %s, количество документов: %d", mat.shape, len(docs))
        logging.info("Пример документа: %s", docs[0])
    except FileNotFoundError:
        logging.error("❌ FAISS index не найден — проверь, что в бакете есть PDF и что у тебя есть доступ.")
    except Exception as e:
        logging.exception("❌ Ошибка при загрузке индекса: %s", e)

if __name__ == "__main__":
    main()
