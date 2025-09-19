# rag_yandex_nofaiss.py
import os
import json
import time
import pickle
import logging
import numpy as np
import asyncio
from typing import List, Dict, Tuple, Optional
import os
import boto3
import fitz 
from faiss_index_yandex import build_index, load_index, semantic_search, VECTORS_FILE, METADATA_FILE
from yandex_api import yandex_batch_embeddings, yandex_completion
from moderation_yandex import pre_moderate_input, post_moderate_output, extract_text_from_yandex_completion
from settings import VECTORSTORE_DIR, S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")



def download_pdf_bytes(bucket: str, key: str, endpoint: str = S3_ENDPOINT,
                       access_key: Optional[str] = None, secret_key: Optional[str] = None) -> bytes:
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
    doc = fitz.Document(stream=pdf_bytes, filetype="pdf")
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n".join(pages)


def chunk_text(text: str, max_chars: int = 1500) -> List[str]:
    """
    Разбивает текст на фрагменты с учетом ограничений API Yandex (2048 токенов).
    Используем консервативное значение 1500 символов ≈ 1000-1500 токенов.
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

def build_index_from_bucket(bucket: str, prefix: str = "", embedding_model_uri: Optional[str] = None,
                            max_chunk_chars: Optional[int] = None):
    """
    Скачивает PDF(ы) из бакета/prefix, извлекает текст, разбивает на чанки и строит векторный store.
    """
    if max_chunk_chars is None:
        try:
            max_chunk_chars = int(os.getenv("YAND_MAX_CHUNK_CHARS", "1500"))
        except Exception:
            max_chunk_chars = 1500

    access_key = S3_ACCESS_KEY
    secret_key = S3_SECRET_KEY
    if not access_key or not secret_key:
        logger.error("S3 access key / secret key not set (S3_ACCESS_KEY / S3_SECRET_KEY).")
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
        logger.exception("Ошибка доступа к S3 (list_objects_v2): %s", e)
        return

    contents = response.get("Contents") or []
    if not contents:
        logger.warning("В бакете %s с префиксом '%s' нет файлов или нет доступа.", bucket, prefix)
        return

    docs_for_index = []
    for obj in contents:
        key = obj.get("Key")
        if not key or not key.lower().endswith(".pdf"):
            continue
        try:
            pdf_bytes = download_pdf_bytes(bucket, key, endpoint=S3_ENDPOINT,
                                          access_key=access_key, secret_key=secret_key)
            text = extract_text_from_pdf_bytes(pdf_bytes)
            # Очистка и разбивка
            text_clean = " ".join(text.split())
            chunks = chunk_text(text_clean, max_chars=max_chunk_chars)
            for i, ch in enumerate(chunks):
                part_id = f"{os.path.basename(key)}__part{i+1}"
                docs_for_index.append({"id": part_id, "text": ch, "meta": {"source": key, "part": i+1}})
            logger.info("Processed %s -> %d chunks", key, len(chunks))
        except Exception as e:
            logger.exception("Ошибка обработки файла %s: %s", key, e)

    if docs_for_index:
        # build_vectorstore_from_docs ожидает список dicts {'id','text','meta'}
        build_vectorstore_from_docs(docs_for_index, embedding_model_uri=embedding_model_uri)
        logger.info("RAG индекс построен: %d чанков", len(docs_for_index))
    else:
        logger.warning("Не найдено документов для индексирования.")

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """
    Читает текст из PDF (байты) и возвращает одну большую строку.
    """
    doc = fitz.Document(stream=pdf_bytes, filetype="pdf")
    texts = [page.get_text() for page in doc]
    doc.close()
    return "\n".join(texts)


def build_vectorstore_from_docs(docs: List[Dict], embedding_model_uri: Optional[str] = None):
    """
    Делегируем построение индекса модулю faiss_index_yandex.build_index.
    Ожидаем, что там внутри вызываются эмбеддинги и создаются index.faiss, vectors.npy и meta.pkl.
    """
    logger.info("Building FAISS index for %d docs via faiss_index_yandex.build_index...", len(docs))
    try:
        return build_index(docs, model_uri=embedding_model_uri)
    except Exception as e:
        logger.exception("faiss_adapter.build_index failed: %s", e)
        # Поддерживаем поведение — если faiss падает, пробуем сохранить как numpy-фоллбек:
        logger.info("Falling back to numpy save (vectors.npy + meta.pkl).")
    # Fallback: сохранить embs в vectors.npy и meta.pkl (как раньше)
    texts = [d["text"] for d in docs]
    embs = yandex_batch_embeddings(texts, model_uri=embedding_model_uri)
    mat = np.array(embs, dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat = mat / norms
    np.save(VECTORS_FILE, mat)
    with open(METADATA_FILE, "wb") as f:
        pickle.dump(docs, f)
    logger.info("Vectorstore saved (fallback): %s, %s", VECTORS_FILE, METADATA_FILE)
    return True

def load_vectorstore():
    """
    Загружает векторное хранилище. Делегируем faiss_adapter.load_index(), ожидая (index, mat, docs).
    Возвращаем (mat, docs) для совместимости с остальным кодом.
    """
    try:
        out = load_index()
        # ожидаем tuple (index, mat, docs)
        if isinstance(out, tuple) and len(out) == 3:
            index, mat, docs = out
            logger.info("Loaded FAISS index via adapter (n=%d)", len(docs))
            return mat, docs
        # если адаптер вернул неожиданный формат — бросим исключение и уйдём в fallback
        raise RuntimeError("faiss_adapter.load_index returned unexpected format")
    except Exception as e:
        logger.exception("faiss_adapter.load_index failed: %s. Falling back to numpy files.", e)

    if not os.path.exists(VECTORS_FILE) or not os.path.exists(METADATA_FILE):
        raise FileNotFoundError("Vectorstore files not found; build index first.")
    mat = np.load(VECTORS_FILE)
    with open(METADATA_FILE, "rb") as f:
        docs = pickle.load(f)
    logger.info("Loaded vectorstore from numpy files (n=%d)", len(docs))
    return mat, docs


def semantic_search_in_memory(query: str, k: int = 3, embedding_model_uri: Optional[str] = None) -> List[Dict]:
    """
    Делегируем поиск faiss_adapter.semantic_search (ожидаем список dict с полем 'score').
    Если адаптер падает — делаем in-memory fallback.
    """
    try:
        results = semantic_search(query, k=k, model_uri=embedding_model_uri)
        if isinstance(results, list):
            return results
        logger.warning("faiss_adapter.semantic_search returned unexpected type: %r", type(results))
    except Exception as e:
        logger.exception("faiss_adapter.semantic_search failed: %s. Falling back to in-memory dot-product search.", e)

    mat, docs = load_vectorstore()
    q_emb = np.array(yandex_batch_embeddings([query], model_uri=embedding_model_uri), dtype=np.float32)
    q_norm = np.linalg.norm(q_emb, axis=1, keepdims=True)
    q_norm[q_norm == 0] = 1.0
    q_emb = q_emb / q_norm
    scores = (mat @ q_emb.T).squeeze()  # shape (n,)
    idx = np.argsort(-scores)[:k]
    results = []
    for i in idx:
        d = docs[i].copy()
        d["score"] = float(scores[i])
        results.append(d)
    return results


AUDIT_FILE = os.path.join(VECTORSTORE_DIR, "moderation_audit.log")
def audit_log(entry: dict):
    entry_out = {"ts": time.time(), **entry}
    with open(AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry_out, ensure_ascii=False) + "\n")

# --- RAG pipeline: answer_user_query (sync) + async wrapper ---
def answer_user_query_sync(user_text: str, user_id: int, k: int = 3) -> Tuple[str, dict]:
    meta = {"user_id": user_id, "query": user_text}
    # 1) pre-moderation
    try:
        ok_pre_res = pre_moderate_input(user_text)
        if not isinstance(ok_pre_res, tuple) or len(ok_pre_res) != 2:
            logger.warning("pre_moderate_input returned unexpected: %r", ok_pre_res)
            ok_pre, pre_meta = True, {"via": "fallback", "reason": "pre_moderation_bad_return"}
        else:
            ok_pre, pre_meta = ok_pre_res
    except Exception as e:
        logger.exception("pre_moderate_input raised: %s", e)
        ok_pre, pre_meta = True, {"via": "exception", "error": str(e)}

    meta["pre_moderation"] = pre_meta
    if not ok_pre:
        audit_log({"user_id": user_id, "action": "blocked_pre", "query": user_text, "meta": pre_meta})
        return ("Извините, я не могу помочь с этим запросом.", {"blocked": True, "reason": pre_meta})
    # 2) retrieval
    try:
        docs = semantic_search_in_memory(user_text, k=k)
    except Exception as e:
        logger.exception("semantic_search_in_memory failed: %s", e)
        docs = []
    meta["retrieved_count"] = len(docs)
    # build context
    context_parts = []
    for d in docs:
        src = d.get("meta", {}).get("source", d.get("id", "unknown"))
        txt = d.get("text", "")
        context_parts.append(f"Источник: {src}\n{txt}")
    context_for_model = "\n\n---\n\n".join(context_parts) if context_parts else ""
    # 3) call Yandex completion
    system_prompt = (
        "Ты — дружелюбный ИИ-бармен. Ты специализируешься на коктейлях, алкогольных и безалкогольных напитках. "
        "Используй контекст, если он есть, для точных рецептов и информации. "
        "Если контекста недостаточно, используй свои знания о барном деле. "
        "Отвечай подробно с рецептами, ингредиентами и способами приготовления. "
        "Всегда будь позитивным и готовым помочь с любыми вопросами о напитках. "
        "\n\nФорматирование ответа:\n"
        "- Используй заголовки для названий напитков\n"
        "- Перечисляй ингредиенты списком с дефисами\n"
        "- Нумеруй шаги приготовления\n"
        "- Указывай точные количества в мл, г, ст.л., ч.л.\n"
        "- Добавляй температуру и время, если необходимо\n"
        "- Структурируй ответ четко и читаемо"
    )
    user_prompt = f"Контекст документов:\n{context_for_model}\n\nВопрос пользователя: {user_text}\nОтветь как профессиональный бармен: рекомендации, рецепты, советы."
    yresp = yandex_completion([{"role": "system", "text": system_prompt}, {"role": "user", "text": user_prompt}])
    answer = "Извините, сейчас модель недоступна."
    if not yresp.get("error"):
        # Извлекаем текст из ответа Yandex API
        answer = extract_text_from_yandex_completion(yresp)
        if not answer:
            # Если не удалось извлечь ответ, используем генератор коктейлей
            answer = generate_compact_cocktail(user_text)
        if not answer:
            answer = "Извините, не удалось сформировать ответ."
    meta["raw_response_preview"] = answer[:500]
    # 4) post moderation
    ok_post, post_meta = post_moderate_output(answer)
    meta["post_moderation"] = post_meta
    if not ok_post:
        audit_log({"user_id": user_id, "action": "blocked_post", "query": user_text, "raw_answer": answer[:400], "meta": post_meta})
        return ("Извините, я не могу предоставить этот ответ по соображениям безопасности.", {"blocked": True, "reason": post_meta})
    # 5) success
    audit_log({"user_id": user_id, "action": "answered", "query": user_text, "retrieved": [d.get("id") for d in docs], "meta": meta})
    return (answer, {"blocked": False, **meta})

def generate_compact_cocktail(query: str, max_tokens: int = 220, temp: float = 0.2) -> str:
    """
    Возвращает короткий рецепт в строго заданном формате.
    query: строка с предпочтениями пользователя (напр. "сладкое, безалкогольное")
    """
    SYSTEM_PROMPT_PERSONA = (
        "Ты — дружелюбный бармен из кино: немного причудливый, обаятельный и с лёгким юмором. "
        "Отвечай коротко, по делу, но с характером — представь, что говоришь гостю в уютном баре на фильмовой сцене. "
        "Всегда используй контекст, если он есть. Если информации в контексте недостаточно — придумай оригинальный рецепт и представь его как совет бармена. "
        "Ограничение: не более 700 символов. Отвечай строго в формате ниже (без лишних вводных):\n\n"
        "Коктейль: \"НАЗВАНИЕ\"\n"
        "ИНГРЕДИЕНТЫ:\n"
        "  - ...\n"
        "  - ...\n"
        "ПРИГОТОВЛЕНИЕ:\n"
        "  - шаг 1\n"
        "  - шаг 2\n"
        "ИНТЕРЕСНЫЙ ФАКТ: Одно-два коротких предложения.\n"
        "Ни строчек лишних — только этот шаблон. Если нужно, предложи замену ингредиента в скобках."
    )
    user = f"Пользователь: {query}. Ответь коротко, максимум 4 ингредиента, максимум 4 шага."
    resp = yandex_completion([{"role": "system", "text": SYSTEM_PROMPT_PERSONA}, {"role": "user", "text": user}], temperature=temp, max_tokens=max_tokens)
    if resp.get("error"):
        logger.error("generate_compact_cocktail: completion error %s", resp)
        return "Извините, не удалось сформировать рецепт."
    text = extract_text_from_yandex_completion(resp)
    # Постобработка: если модель вернула что-то лишнее, попробуем вырезать часть до первого заголовка
    if not text:
        logger.warning("generate_compact_cocktail: empty response, returning fallback")
        return "Извините, не удалось сформировать ответ."
    # Обрезаем по длине, убираем лишние пустые строки
    text = "\n".join([ln.rstrip() for ln in text.splitlines() if ln.strip() != ""])
    if len(text) > 1000:
        text = text[:1000] + "..."
    return text

async def async_answer_user_query(user_text: str, user_id: int, k: int = 3) -> Tuple[str, dict]:
    """
    Async wrapper: выполняет синхронную работу в ThreadPoolExecutor,
    безопасно вызывается из async handle_message.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: answer_user_query_sync(user_text, user_id, k))

# --- Small utility for testing: add docs and build index ---
def build_index_from_plain_texts(text_docs: List[Tuple[str, str]], embedding_model_uri: Optional[str] = None):
    """
    text_docs: list of (id, text). Stores meta minimal.
    """
    docs = []
    for id_, txt in text_docs:
        docs.append({"id": id_, "text": txt, "meta": {"source": id_}})
    build_vectorstore_from_docs(docs, embedding_model_uri=embedding_model_uri)
    logger.info("Index built from %d texts", len(text_docs))
