# yandex_api.py
import os
import logging
import requests
import json
import time
from typing import List, Optional, Dict, Any
from services.auth.auth import get_headers, BASE_URL
logger = logging.getLogger(__name__)

EMB_MODEL_URI = os.getenv("EMB_MODEL_URI", "your_emb_model_uri")
TEXT_MODEL_URI = os.getenv("TEXT_MODEL_URI", "your_text_model_uri")

def yandex_text_embedding(text: str, model_uri: Optional[str] = None, max_retries: int = 3, delay: float = 1.0) -> List[float]:
    """
    Получение эмбеддинга текста с повторными попытками при ошибках сервера
    """
    HEADERS = get_headers()
    if model_uri is None:
        model_uri = EMB_MODEL_URI
    url = f"{BASE_URL}/textEmbedding"
    print(model_uri + "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
    payload = {"modelUri": model_uri, "text": text}

    for attempt in range(max_retries):
        try:
            r = requests.post(url, headers=HEADERS, json=payload, timeout=60)

            if r.status_code == 200:
                resp = r.json()
                embedding = resp.get("embedding")
                if embedding:
                    return [float(x) for x in embedding]
                else:
                    logger.warning("Пустой эмбеддинг в ответе, попытка %d", attempt + 1)
            elif r.status_code == 500:
                logger.warning("Ошибка 500 от Yandex API, попытка %d из %d", attempt + 1, max_retries)
                if attempt < max_retries - 1:
                    time.sleep(delay * (2 ** attempt))  # Экспоненциальная задержка
                    continue
            elif r.status_code == 429:
                logger.warning("Rate limit, ожидание %d секунд", delay * 2)
                time.sleep(delay * 2)
                if attempt < max_retries - 1:
                    continue
            else:
                logger.error("HTTP %d: %s", r.status_code, r.text)

        except Exception as e:
            logger.warning("Ошибка при получении эмбеддинга (попытка %d): %s", attempt + 1, e)
            if attempt < max_retries - 1:
                time.sleep(delay)
                continue

    raise RuntimeError(f"Embedding error: {r.status_code}")


def yandex_batch_embeddings(texts: List[str], model_uri: Optional[str] = None, batch_size: int = 5) -> List[List[float]]:
    """
    Пакетное получение эмбеддингов с обработкой ошибок и батчингом
    """
    out = []
    total = len(texts)
    # Обрабатываем по батчам для снижения нагрузки на API
    for i in range(0, total, batch_size):
        batch = texts[i:i + batch_size]
        logger.info("Обрабатываю батч %d-%d из %d текстов", i + 1, min(i + batch_size, total), total)

        for j, text in enumerate(batch):
            try:
                embedding = yandex_text_embedding(text, model_uri=model_uri)
                out.append(embedding)

                # Небольшая задержка между запросами
                if j < len(batch) - 1:
                    time.sleep(0.1)

            except Exception as e:
                logger.error("Не удалось получить эмбеддинг для текста %d: %s", i + j + 1, e)
                # Добавляем нулевой вектор как fallback
                out.append([0.0] * 256)  # Размер вектора Yandex

        # Задержка между батчами
        if i + batch_size < total:
            time.sleep(0.5)

    logger.info("Получено эмбеддингов: %d из %d", len([e for e in out if sum(e) != 0]), total)
    return out


def yandex_completion(messages: List[dict], model_uri: Optional[str] = None, temperature: float = 0.2, max_tokens: int = 1024) -> dict:
    HEADERS = get_headers()
    if model_uri is None:
        model_uri = TEXT_MODEL_URI
    print(model_uri + "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
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
    try:
        answer_text = j["result"]["alternatives"][0]["message"]["text"]
    except (KeyError, IndexError):
        answer_text = None

    return {
        "error": False,
        "raw": j,
        "text": answer_text
    }


def yandex_classify(text: str, model_uri: Optional[str] = None, examples: Optional[List[dict]] = None) -> dict:
    """
    Используем TextClassification API (если нужно) для модерации/разметки.
    Док: TextClassification.Classify (REST).
    NOTE: в некоторых акках требуется gRPC; здесь — пример REST через endpoint /textClassification/classify — подстройте, если у вас иная схема.
    """
    HEADERS = get_headers()
    if model_uri is None:
        model_uri = os.getenv("YAND_CLASSIFY_MODEL_URI", "models/text-classification-??")  # TODO: замените
    # TextClassification REST path (примерное): https://llm.api.cloud.yandex.net/foundationModels/v1/textClassification/classify
    url = f"{BASE_URL}/textClassification/classify"
    payload: Dict[str, Any] = {"modelUri": model_uri, "text": text}
    if examples:
        payload["examples"] = examples
    resp = requests.post(url, headers=HEADERS, json=payload, timeout=15)
    if resp.status_code != 200:
        logger.error("yandex_classify error %s %s", resp.status_code, resp.text)
        return {"error": True, "status_code": resp.status_code, "text": resp.text}
    return resp.json()
