import os
import faiss
import pickle
import numpy as np
import boto3
import fitz
import csv
import json
import logging
from typing import List, Optional, Tuple, Dict, Any
from services.rag.embending import yandex_batch_embeddings

logger = logging.getLogger(__name__)

S3_ENDPOINT = os.getenv("S3_ENDPOINT", "")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")

logger = logging.getLogger(__name__)

# ===== Настройки директорий для FAISS =====
INDEX_DIR = os.getenv("FAISS_INDEX_DIR", "faiss_index_yandex")
VECTORSTORE_DIR = os.getenv("VECTORSTORE_DIR", "./vectorstore")

METADATA_FILE = os.path.join(VECTORSTORE_DIR, "meta.pkl")
VECTORS_FILE = os.path.join(VECTORSTORE_DIR, "vectors.npy")
IDX_FILE = os.path.join(INDEX_DIR, "index.faiss")

os.makedirs(INDEX_DIR, exist_ok=True)
os.makedirs(VECTORSTORE_DIR, exist_ok=True)


# ===== S3 загрузка =====
def download_s3_bytes(bucket: str, key: str) -> bytes:
    s3 = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
    )
    resp = s3.get_object(Bucket=bucket, Key=key)
    return resp["Body"].read()


# ===== Извлечение текста =====
def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    doc = fitz.Document(stream=pdf_bytes, filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    return text

def extract_text_from_csv_bytes(csv_bytes: bytes) -> str:
    try:
        csv_text = csv_bytes.decode('utf-8', errors='ignore')
        reader = csv.DictReader(csv_text.splitlines())
        lines = []
        if reader.fieldnames:
            lines.append(" | ".join(reader.fieldnames))
        for row in reader:
            lines.append(" | ".join(f"{k}: {v}" for k, v in row.items()))
        return "\n".join(lines)
    except Exception as e:
        logger.warning("Ошибка CSV: %s", e)
        return csv_bytes.decode('utf-8', errors='ignore')

def extract_text_from_txt_bytes(txt_bytes: bytes) -> str:
    return txt_bytes.decode('utf-8', errors='ignore')

def extract_text_from_json_bytes(json_bytes: bytes) -> str:
    try:
        data = json.loads(json_bytes.decode('utf-8', errors='ignore'))
        return json.dumps(data, ensure_ascii=False)
    except Exception as e:
        logger.warning("Ошибка JSON: %s", e)
        return json_bytes.decode('utf-8', errors='ignore')

def extract_text_from_file(file_bytes: bytes, filename: str) -> str:
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
        logger.warning("Неизвестный тип файла %s, пробуем как текст", filename)
        return extract_text_from_txt_bytes(file_bytes)


# ===== Разбивка текста на чанки =====
def chunk_text(text: str, max_chars: int = 1500) -> List[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        cut_pos = text.rfind('\n', start, end)
        if cut_pos <= start:
            cut_pos = text.rfind(' ', start, end)
        if cut_pos <= start:
            cut_pos = end
        chunk = text[start:cut_pos].strip()
        if chunk:
            chunks.append(chunk)
        start = cut_pos if cut_pos > start else end
    return chunks


# ===== Построение списка документов для индекса =====
def build_docs_from_s3(bucket: str, prefix: str = "", max_chunk_chars: int = 1500) -> List[dict]:
    s3 = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
    )
    response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    contents = response.get("Contents", [])
    docs = []

    for obj in contents:
        key = obj.get("Key")
        if not key:
            continue
        if not any(key.lower().endswith(ext) for ext in ['.pdf', '.csv', '.txt', '.md', '.json', '.rst']):
            continue
        try:
            file_bytes = download_s3_bytes(bucket, key)
            text = extract_text_from_file(file_bytes, key)
            chunks = chunk_text(" ".join(text.split()), max_chars=max_chunk_chars)
            for i, ch in enumerate(chunks):
                docs.append({
                    "id": f"{os.path.basename(key)}__{i+1}",
                    "text": ch,
                    "meta": {"source": key, "part": i+1, "file_type": os.path.splitext(key)[1].lower()}
                })
        except Exception as e:
            logger.warning("Ошибка обработки файла %s: %s", key, e)
    return docs


# ===== FAISS Индекс =====
def build_index(docs: List[dict], model_uri: Optional[str] = None) -> int:
    try:
        texts = [d["text"] for d in docs]
        embeddings_list = yandex_batch_embeddings(texts, model_uri=model_uri)
        embeddings = np.array(embeddings_list, dtype='float32')

        # <-- HERE: define n_docs and dim
        if embeddings.ndim != 2:
            raise RuntimeError("Embeddings shape unexpected: %r" % (embeddings.shape,))
        n_docs = embeddings.shape[0]
        dim = embeddings.shape[1]

        logger.info("Computed embeddings: n_docs=%d, dim=%d", n_docs, dim)

        faiss.normalize_L2(embeddings)
        index = faiss.IndexFlatIP(dim)
        index.add(embeddings)

        faiss.write_index(index, IDX_FILE)
        np.save(VECTORS_FILE, embeddings)

        # Save pickle metadata
        with open(METADATA_FILE, "wb") as f:
            pickle.dump(docs, f)

        # Also save a JSON copy for incremental module
        try:
            metadata_json_path = os.path.join(VECTORSTORE_DIR, "metadata.json")
            with open(metadata_json_path, "w", encoding="utf-8") as jf:
                json.dump(docs, jf, ensure_ascii=False, indent=2)
            logger.info("Saved metadata.json for incremental compatibility: %s", metadata_json_path)
        except Exception as je:
            logger.exception("Failed to save metadata.json: %s", je)

        logger.info("✅ FAISS индекс построен: %d документов", n_docs)
        return n_docs
    except Exception as e:
        logger.exception("Ошибка при построении FAISS индекса: %s", e)
        return 0



def load_index() -> Tuple[faiss.Index, np.ndarray, List[dict]]:
    if not (os.path.exists(IDX_FILE) and os.path.exists(VECTORS_FILE) and os.path.exists(METADATA_FILE)):
        raise FileNotFoundError("FAISS индекс или данные не найдены")
    index = faiss.read_index(IDX_FILE)
    vectors = np.load(VECTORS_FILE)
    with open(METADATA_FILE, "rb") as f:
        docs = pickle.load(f)
    return index, vectors, docs


def semantic_search(query: str, k: int = 3, model_uri: Optional[str] = None) -> List[dict]:
    index, vectors, docs = load_index()
    query_emb = np.array(yandex_batch_embeddings([query], model_uri=model_uri), dtype='float32')
    faiss.normalize_L2(query_emb)
    scores, indices = index.search(query_emb, k)
    results = []
    for i, (score, idx) in enumerate(zip(scores[0], indices[0])):
        if idx < len(docs):
            result = docs[idx].copy()
            result["score"] = float(score)
            result["rank"] = i + 1
            results.append(result)
    return results
