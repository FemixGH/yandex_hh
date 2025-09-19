# yandex_api.py
import os
import logging
import requests
import json
from typing import List, Optional
from settings import EMB_MODEL_URI, TEXT_MODEL_URI
from yandex_jwt_auth import HEADERS, BASE_URL
logger = logging.getLogger(__name__)


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
