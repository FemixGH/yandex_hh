# yandex_api.py
import os
import logging
import requests
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
BASE_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1"

if not YANDEX_API_KEY:
    logger.warning("YANDEX_API_KEY не задан. Yandex API вызовы упадут.")

HEADERS = {
    "Authorization": f"Api-Key {YANDEX_API_KEY}",
    "Content-Type": "application/json"
}

def yandex_text_embedding(text: str, model_uri: Optional[str] = None) -> List[float]:
    """
    Возвращает embedding для одного текста как список float.
    model_uri - например "models/text-embedding-***" (подставьте ваш modelUri).
    Док: POST /foundationModels/v1/textEmbedding
    """
    if model_uri is None:
        model_uri = os.getenv("YAND_EMBEDDING_MODEL_URI", "models/text-embedding-clip-??")  # TODO: замените
    url = f"{BASE_URL}/textEmbedding"
    payload = {"modelUri": model_uri, "text": text}
    resp = requests.post(url, headers=HEADERS, json=payload, timeout=30)
    if resp.status_code != 200:
        logger.error("yandex_text_embedding error %s %s", resp.status_code, resp.text)
        raise RuntimeError(f"Yandex embedding error: {resp.status_code}")
    data = resp.json()
    # data: {"embedding": ["0.123", "..."], "numTokens": "..."}
    emb = data.get("embedding", [])
    # API может вернуть числа как строки — приводим к float
    return [float(x) for x in emb]

def yandex_batch_embeddings(texts: List[str], model_uri: Optional[str] = None) -> List[List[float]]:
    return [yandex_text_embedding(t, model_uri=model_uri) for t in texts]

def yandex_completion(messages: List[dict], model_uri: Optional[str] = None, temperature: float = 0.2, max_tokens: int = 1024) -> dict:
    """
    messages: list of {"role": "system/user/assistant", "text": "..."}
    Возвращает JSON ответа Yandex Completion (synchronous /completion endpoint).
    Док: POST /foundationModels/v1/completion
    """
    if model_uri is None:
        model_uri = os.getenv("YAND_TEXT_MODEL_URI", "gpt://b1g-example-id/yandexgpt-lite/latest")  # TODO: замените
    url = f"{BASE_URL}/completion"
    payload = {
        "modelUri": model_uri,
        "completionOptions": {"stream": False, "temperature": temperature, "maxTokens": str(max_tokens)},
        "messages": messages
    }
    resp = requests.post(url, headers=HEADERS, json=payload, timeout=30)
    if resp.status_code != 200:
        logger.error("yandex_completion error %s %s", resp.status_code, resp.text)
        return {"error": True, "status_code": resp.status_code, "text": resp.text}
    return resp.json()

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
    payload = {"modelUri": model_uri, "text": text}
    if examples:
        payload["examples"] = examples
    resp = requests.post(url, headers=HEADERS, json=payload, timeout=15)
    if resp.status_code != 200:
        logger.error("yandex_classify error %s %s", resp.status_code, resp.text)
        return {"error": True, "status_code": resp.status_code, "text": resp.text}
    return resp.json()
