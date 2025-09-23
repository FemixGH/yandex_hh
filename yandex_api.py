# yandex_api.py
import os
import logging
import requests
import json
import time
from typing import List, Optional, Dict, Any
from settings import EMB_MODEL_URI, TEXT_MODEL_URI
from yandex_jwt_auth import get_headers, BASE_URL

logger = logging.getLogger(__name__)


def yandex_text_embedding(text: str, model_uri: Optional[str] = None, max_retries: int = 3, delay: float = 1.0) -> List[float]:
    """
    Получение эмбеддинга текста с повторными попытками при ошибках сервера
    """
    if model_uri is None:
        model_uri = EMB_MODEL_URI
    url = f"{BASE_URL}/textEmbedding"
    payload = {"modelUri": model_uri, "text": text}

    for attempt in range(max_retries):
        try:
            headers = get_headers()
            r = requests.post(url, headers=headers, json=payload, timeout=60)

            if r.status_code == 200:
                resp = r.json()
                embedding = resp.get("embedding")
                if embedding:
                    return [float(x) for x in embedding]
                else:
                    logger.warning("yandex_text_embedding: no embedding in response")
                    return []
            else:
                logger.warning(f"yandex_text_embedding: HTTP {r.status_code}, response: {r.text}")
                if r.status_code >= 500 and attempt < max_retries - 1:
                    logger.info(f"Server error, retrying in {delay} seconds (attempt {attempt + 1}/{max_retries})")
                    time.sleep(delay)
                    delay *= 2  # Exponential backoff
                    continue
                else:
                    return []
        except requests.exceptions.Timeout:
            logger.warning(f"yandex_text_embedding: timeout on attempt {attempt + 1}")
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            else:
                return []
        except Exception as e:
            logger.error(f"yandex_text_embedding error: {e}")
            if "API credentials not configured" in str(e) or "SERVICE_ACCOUNT_ID" in str(e):
                logger.warning("Yandex API credentials not configured - returning empty embedding")
                return []
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            else:
                return []

    return []


def yandex_batch_embeddings(texts: List[str], model_uri: Optional[str] = None) -> List[List[float]]:
    """
    Получение эмбеддингов для списка текстов
    """
    embeddings = []
    for text in texts:
        emb = yandex_text_embedding(text, model_uri)
        embeddings.append(emb)
    return embeddings


def yandex_completion(prompt, model_uri: Optional[str] = None, max_tokens: int = 2000, temperature: float = 0.3) -> Dict[str, Any]:
    """
    Получение текстового ответа от YandexGPT
    """
    if model_uri is None:
        model_uri = TEXT_MODEL_URI

    url = f"{BASE_URL}/completion"

    # Форматируем промпт в правильный формат для API
    if isinstance(prompt, list):
        messages = []
        for msg in prompt:
            if isinstance(msg, dict) and "role" in msg and "text" in msg:
                messages.append({
                    "role": msg["role"],
                    "text": msg["text"]
                })
        prompt_formatted = messages
    elif isinstance(prompt, str):
        prompt_formatted = [{"role": "user", "text": prompt}]
    else:
        prompt_formatted = [{"role": "user", "text": str(prompt)}]

    payload = {
        "modelUri": model_uri,
        "completionOptions": {
            "stream": False,
            "temperature": temperature,
            "maxTokens": max_tokens
        },
        "messages": prompt_formatted
    }

    try:
        headers = get_headers()
        response = requests.post(url, headers=headers, json=payload, timeout=60)

        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"yandex_completion: HTTP {response.status_code}, response: {response.text}")
            return {"error": f"HTTP {response.status_code}: {response.text}"}

    except Exception as e:
        logger.error(f"yandex_completion error: {e}")
        if "API credentials not configured" in str(e) or "SERVICE_ACCOUNT_ID" in str(e):
            return {"error": "Yandex API credentials not configured"}
        return {"error": str(e)}


def yandex_classify(text: str, model_uri: Optional[str] = None, examples: Optional[List[dict]] = None) -> dict:
    """
    Используем TextClassification API (если нужно) для модерации/разметки.
    Док: TextClassification.Classify (REST).
    NOTE: в некоторых акках требуется gRPC; здесь — пример REST через endpoint /textClassification/classify — подстройте, если у вас иная схема.
    """
    if model_uri is None:
        model_uri = os.getenv("YAND_CLASSIFY_MODEL_URI", "models/text-classification-??")  # TODO: замените
    # TextClassification REST path (примерное): https://llm.api.cloud.yandex.net/foundationModels/v1/textClassification/classify
    url = f"{BASE_URL}/textClassification/classify"
    payload: Dict[str, Any] = {"modelUri": model_uri, "text": text}
    if examples:
        payload["examples"] = examples

    try:
        headers = get_headers()
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        if resp.status_code != 200:
            logger.error("yandex_classify error %s %s", resp.status_code, resp.text)
            return {"error": True, "status_code": resp.status_code, "text": resp.text}
        return resp.json()
    except Exception as e:
        logger.error(f"yandex_classify error: {e}")
        if "API credentials not configured" in str(e) or "SERVICE_ACCOUNT_ID" in str(e):
            return {"error": "Yandex API credentials not configured"}
        return {"error": str(e)}
