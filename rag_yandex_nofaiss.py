# rag_yandex_nofaiss.py
import os
import json
import time
import math
import pickle
import logging
import requests
import numpy as np
import asyncio
import tempfile
from typing import List, Dict, Tuple, Optional
import jwt
from dotenv import load_dotenv
import os
import io
import boto3
import fitz  # pip install pymupdf


print("DEBUG:", os.getenv("SERVICE_ACCOUNT_ID"), os.getenv("KEY_ID"), os.getenv("FOLDER_ID"))



load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- Config from env ---
SERVICE_ACCOUNT_ID = os.getenv("SERVICE_ACCOUNT_ID")
KEY_ID = os.getenv("KEY_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
FOLDER_ID = os.getenv("FOLDER_ID") or os.getenv("YC_FOLDER_ID")

# Model URIs — можно явно указать в .env, иначе соберём из FOLDER_ID
EMB_MODEL_URI = os.getenv("YAND_EMBEDDING_MODEL_URI") or (f"emb://{FOLDER_ID}/text-search-doc/latest" if FOLDER_ID else None)
TEXT_MODEL_URI = os.getenv("YAND_TEXT_MODEL_URI") or (f"gpt://{FOLDER_ID}/yandexgpt/latest" if FOLDER_ID else None)
CLASSIFY_MODEL_URI = os.getenv("YAND_CLASSIFY_MODEL_URI") or (f"cls://{FOLDER_ID}/yandexgpt-lite/latest" if FOLDER_ID else None)

VECTORSTORE_DIR = os.getenv("VECTORSTORE_DIR", "./vectorstore")

S3_ENDPOINT = "https://storage.yandexcloud.net"
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY")
S3_BUCKET = "vedroo"
S3_PREFIX = ""


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
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    for page in doc:
        pages.append(page.get_text())
    return "\n".join(pages)


def chunk_text(text: str, max_chars: int = 6000) -> List[str]:
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
            cut_pos = text.rfind('\n', start, end)
            if cut_pos <= start:
                cut_pos = text.rfind(' ', start, end)
            if cut_pos <= start:
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
            max_chunk_chars = int(os.getenv("YAND_MAX_CHUNK_CHARS", "6000"))
        except Exception:
            max_chunk_chars = 6000

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
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    texts = [page.get_text() for page in doc]
    return "\n".join(texts)


# 1) Сформировать JWT для обмена на IAM-токен
def create_jwt(sa_id, key_id, private_key):
    now = int(time.time())
    payload = {
        "aud": "https://iam.api.cloud.yandex.net/iam/v1/tokens",
        "iss": sa_id,
        "iat": now,
        "exp": now + 360  # 6 минут жизни JWT
    }
    encoded = jwt.encode(
        payload,
        private_key.replace('\\n', '\n'),  # если ключ в .env с \n
        algorithm="PS256",
        headers={"kid": key_id}
    )
    return encoded

def exchange_jwt_for_iam_token(jwt_token):
    url = "https://iam.api.cloud.yandex.net/iam/v1/tokens"
    resp = requests.post(url, json={"jwt": jwt_token})
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to get IAM token: {resp.status_code} {resp.text}")
    return resp.json()["iamToken"]

if SERVICE_ACCOUNT_ID and KEY_ID and PRIVATE_KEY:
    jwt_token = create_jwt(SERVICE_ACCOUNT_ID, KEY_ID, PRIVATE_KEY)
    IAM_TOKEN = exchange_jwt_for_iam_token(jwt_token)
    logger.info("IAM token successfully obtained")
else:
    raise RuntimeError("SERVICE_ACCOUNT_ID / KEY_ID / PRIVATE_KEY not set. Cannot obtain IAM token.")

# Заголовки для Yandex API
HEADERS = {
    "Authorization": f"Bearer {IAM_TOKEN}",
    "Content-Type": "application/json"
}
if FOLDER_ID:
    HEADERS["X-Folder-Id"] = FOLDER_ID

BASE_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1"

logger.info("Using EMB_MODEL_URI=%s TEXT_MODEL_URI=%s CLASSIFY_MODEL_URI=%s VECTORSTORE_DIR=%s",
            EMB_MODEL_URI, TEXT_MODEL_URI, CLASSIFY_MODEL_URI, VECTORSTORE_DIR)

os.makedirs(VECTORSTORE_DIR, exist_ok=True)

# --- Helper for robust parsing of Yandex completion response ---
def extract_text_from_yandex_completion(resp_json: dict) -> str:
    """
    Robust extraction of textual content from Yandex completion response.
    Returns empty string if no sensible text was found.
    """
    if not isinstance(resp_json, dict):
        return ""

    # Try canonical "result" -> "choices" -> message/content/text
    try:
        res = resp_json.get("result")
        if isinstance(res, dict):
            choices = res.get("choices")
            if isinstance(choices, list) and len(choices) > 0:
                ch = choices[0]
                # If choice directly contains content list
                if isinstance(ch, dict):
                    # content (list of dicts)
                    if "content" in ch and isinstance(ch["content"], list):
                        texts = []
                        for c in ch["content"]:
                            if isinstance(c, dict) and "text" in c and isinstance(c["text"], str):
                                texts.append(c["text"])
                        if texts:
                            return "\n".join(texts).strip()
                    # message object
                    if "message" in ch and isinstance(ch["message"], dict):
                        msg = ch["message"]
                        if "content" in msg and isinstance(msg["content"], list):
                            texts = []
                            for c in msg["content"]:
                                if isinstance(c, dict) and "text" in c and isinstance(c["text"], str):
                                    texts.append(c["text"])
                            if texts:
                                return "\n".join(texts).strip()
                        if "text" in msg and isinstance(msg["text"], str):
                            # ensure it's not just 'assistant' role leaked
                            if msg["text"].strip().lower() != "assistant":
                                return msg["text"].strip()
    except Exception:
        # fallthrough to generic search
        pass

    # Old-style alternatives handling
    if "alternatives" in resp_json and isinstance(resp_json["alternatives"], list) and resp_json["alternatives"]:
        alt = resp_json["alternatives"][0]
        if isinstance(alt, dict):
            msg = alt.get("message") or alt.get("content") or alt.get("text")
            if isinstance(msg, dict) and "text" in msg and isinstance(msg["text"], str):
                if msg["text"].strip().lower() != "assistant":
                    return msg["text"].strip()
            if isinstance(msg, str):
                if msg.strip().lower() != "assistant":
                    return msg.strip()
            if isinstance(alt.get("message"), dict) and isinstance(alt["message"].get("content"), list):
                texts = []
                for c in alt["message"]["content"]:
                    if isinstance(c, dict) and "text" in c and isinstance(c["text"], str):
                        texts.append(c["text"])
                if texts:
                    return "\n".join(texts).strip()

    # Generic search: find first non-role string (skip common role labels)
    def find_first_nonrole_string(obj):
        if isinstance(obj, str):
            s = obj.strip()
            if s and s.lower() not in ("assistant", "user", "system"):
                return s
            return None
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "role":
                    continue
                found = find_first_nonrole_string(v)
                if found:
                    return found
        if isinstance(obj, list):
            for v in obj:
                found = find_first_nonrole_string(v)
                if found:
                    return found
        return None

    s = find_first_nonrole_string(resp_json)
    return s[:4000].strip() if s else ""


# --- Yandex API helpers ---
def yandex_text_embedding(text: str, model_uri: Optional[str] = None) -> List[float]:
    if model_uri is None:
        model_uri = EMB_MODEL_URI
    url = f"{BASE_URL}/textEmbedding"
    payload = {"modelUri": model_uri, "text": text}
    r = requests.post(url, headers=HEADERS, json=payload, timeout=30)
    if r.status_code != 200:
        logger.error("Embedding error %s %s", r.status_code, r.text)
        raise RuntimeError(f"Embedding error: {r.status_code}")
    j = r.json()
    emb = j.get("embedding") or j.get("embeddingVector") or []
    # ensure floats
    return [float(x) for x in emb]

def yandex_batch_embeddings(texts: List[str], model_uri: Optional[str] = None) -> List[List[float]]:
    # Yandex might not support large batch; do sequentially for safety.
    out = []
    for t in texts:
        out.append(yandex_text_embedding(t, model_uri=model_uri))
    return out

def yandex_completion(messages: List[dict], model_uri: Optional[str] = None, temperature: float = 0.2, max_tokens: int = 1024) -> dict:
    if model_uri is None:
        model_uri = TEXT_MODEL_URI
    url = f"{BASE_URL}/completion"
    payload = {
        "modelUri": model_uri,
        "completionOptions": {"stream": False, "temperature": temperature, "maxTokens": str(max_tokens)},
        "messages": messages
    }
    r = requests.post(url, headers=HEADERS, json=payload, timeout=30)
    if r.status_code != 200:
        logger.error("yandex_completion error %s %s", r.status_code, r.text)
        return {"error": True, "status_code": r.status_code, "text": r.text}
    try:
        j = r.json()
    except Exception as e:
        logger.exception("Failed to parse yandex_completion json: %s", e)
        return {"error": True, "reason": "invalid_json", "text": r.text}
    logger.debug("yandex_completion raw json: %s", json.dumps(j, ensure_ascii=False))
    return j


VECTORS_FILE = os.path.join(VECTORSTORE_DIR, "vectors.npy")
METADATA_FILE = os.path.join(VECTORSTORE_DIR, "meta.pkl")

def build_vectorstore_from_docs(docs: List[Dict], embedding_model_uri: Optional[str] = None):
    """
    docs: list of {'id': str, 'text': str, 'meta': {...}}
    """
    logger.info("Building vectorstore from %d docs...", len(docs))
    texts = [d["text"] for d in docs]
    embs = yandex_batch_embeddings(texts, model_uri=embedding_model_uri)
    mat = np.array(embs, dtype=np.float32)
    # normalize for cosine
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat = mat / norms
    np.save(VECTORS_FILE, mat)
    with open(METADATA_FILE, "wb") as f:
        pickle.dump(docs, f)
    logger.info("Vectorstore saved: %s, %s", VECTORS_FILE, METADATA_FILE)

def load_vectorstore():
    if not os.path.exists(VECTORS_FILE) or not os.path.exists(METADATA_FILE):
        raise FileNotFoundError("Vectorstore files not found; build index first.")
    mat = np.load(VECTORS_FILE)
    with open(METADATA_FILE, "rb") as f:
        docs = pickle.load(f)
    return mat, docs

def semantic_search_in_memory(query: str, k: int = 3, embedding_model_uri: Optional[str] = None) -> List[Dict]:
    """
    Возвращает top-k документов с полем 'score' (косинусного сходства).
    """
    mat, docs = load_vectorstore()
    q_emb = np.array(yandex_batch_embeddings([query], model_uri=embedding_model_uri), dtype=np.float32)
    # normalize
    q_norm = np.linalg.norm(q_emb, axis=1, keepdims=True)
    q_norm[q_norm == 0] = 1.0
    q_emb = q_emb / q_norm
    # cosine similarity via dot product
    scores = (mat @ q_emb.T).squeeze()  # shape (n,)
    # get top k
    idx = np.argsort(-scores)[:k]
    results = []
    for i in idx:
        d = docs[i].copy()
        d["score"] = float(scores[i])
        results.append(d)
    return results

# --- Moderation (quick patterns + Yandex classify/completion fallback) ---
TOXIC_PATTERNS = [
    r"\b(убий|убей|самоубийств|суицид)\b",
    r"\b(порно|порнограф|изнасилован)\b",
    r"\b(нарко|героин|кокаин|лсд|метамфетам)\b",
    r"\b(террор|бомб|взорв)\b",
    r"\b(хакер|взлом|фишинг|scam)\b",
]
COMPILED = [__import__("re").compile(p, __import__("re").IGNORECASE | __import__("re").UNICODE) for p in TOXIC_PATTERNS]

def quick_check(text: str) -> Tuple[bool, dict]:
    if not text or not text.strip():
        return False, {"reason": "empty_text"}
    for pat in COMPILED:
        if pat.search(text):
            return False, {"reason": "pattern_block", "pattern": pat.pattern}
    return True, {}

def llm_moderation_yandex(text: str) -> Tuple[bool, dict]:
    """
    Надёжная версия: всегда возвращает (ok: bool, meta: dict).
    Использует LLM (yandex_completion) и гарантирует fallback в случае ошибок.
    """
    try:
        # quick pattern check already handled outside; here only LLM check
        prompt = [
            {"role": "system", "text": (
                "Ты модератор. Определи, опасно ли содержимое текста. "
                "Если это обычные запросы про еду, напитки, рецепты, развлечения, пожелания перчинки или более острое блюдо, если приходит про сообщение коктейль | безалкогольный и тд. — пометь как SAFE. "
                "Если текст про наркотики, насилие, самоповреждение, незаконную деятельность — UNSAFE. "
                "Верни ровно одно слово: SAFE или UNSAFE."
            )},
            {"role": "user", "text": f"Проверить текст: \"{text}\". Только одно слово: SAFE или UNSAFE."}
        ]
        cresp = yandex_completion(prompt)
        if cresp.get("error"):
            logger.warning("llm_moderation_yandex: completion returned error: %s", cresp)
            return True, {"via": "completion_error", "raw": cresp}

        # log raw response for debugging
        logger.debug("llm_moderation raw json: %s", json.dumps(cresp, ensure_ascii=False))

        txt = extract_text_from_yandex_completion(cresp)
        # If model returned nothing sensible, treat as SAFE by default (for user queries)
        if not txt or txt.strip() == "" or txt.strip().lower() == "assistant":
            logger.info("llm_moderation_yandex: model returned empty text; treating as SAFE")
            return True, {"via": "completion", "label": "SAFE", "raw_text": txt}

        label = "SAFE" if "SAFE" in txt.upper() else "UNSAFE"
        return (label == "SAFE"), {"via": "completion", "label": label, "raw_text": txt}
    except Exception as e:
        logger.exception("llm_moderation_yandex exception: %s", e)
        # безопасный fallback — считать текст безопасным, но пометить в метаданных
        return True, {"via": "exception", "error": str(e)}

def pre_moderate_input(text: str) -> Tuple[bool, dict]:
    """
    Надёжно вызывает quick_check + llm_moderation_yandex и ВЕРНЁТ кортеж в любом случае.
    """
    try:
        ok, meta = quick_check(text)
        if not ok:
            return False, meta  # quick_check уже возвращает объяснение
        res = llm_moderation_yandex(text)
        # защита на случай, если llm_moderation_yandex вдруг вернёт None или не кортеж
        if not isinstance(res, tuple) or len(res) != 2:
            logger.warning("pre_moderate_input: llm_moderation_yandex returned unexpected value: %r", res)
            return True, {"via": "fallback", "reason": "llm_moderation_bad_return"}
        return res
    except Exception as e:
        logger.exception("pre_moderate_input exception: %s", e)
        # по безопасности — пусть запрос пройдёт модерацию (можно поменять поведение)
        return True, {"via": "exception", "error": str(e)}

def post_moderate_output(text: str) -> Tuple[bool, dict]:
    # quick pattern check first
    for pat in COMPILED:
        if pat.search(text):
            return False, {"reason": "post_pattern", "pattern": pat.pattern}
    return llm_moderation_yandex(text)

# --- Audit log helper ---
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
    system_prompt = "Ты — ИИ-бармен. Используй контекст, если он есть. Отвечай дружелюбно, кратко и безопасно. Если в контексте нет ответа — честно скажи, что не знаешь."
    user_prompt = f"Контекст документов:\n{context_for_model}\n\nВопрос пользователя: {user_text}\nОтветь как бармен: рекомендации, рецепты, советы."
    yresp = yandex_completion([{"role": "system", "text": system_prompt}, {"role": "user", "text": user_prompt}])
    answer = "Извините, сейчас модель недоступна."
    if not yresp.get("error"):
        answer = generate_compact_cocktail(yresp)
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

# End of module
