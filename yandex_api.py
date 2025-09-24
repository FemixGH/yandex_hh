# yandex_api.py
import logging
import os
import time
from typing import Any, Dict, List, Optional

import requests
from settings import (
    EMB_MODEL_URI,
    FOLDER_ID,
    TEXT_MODEL_NAME,
    TEXT_MODEL_VERSION,
    TEXT_MODEL_URI,
)
from yandex_jwt_auth import BASE_URL, get_headers, get_iam_token

logger = logging.getLogger(__name__)

# --- ML SDK init (lazy) ---
_SDK = None


def _get_sdk():
    global _SDK
    if _SDK is not None:
        return _SDK
    try:
        from yandex_cloud_ml_sdk import YCloudML  # type: ignore
    except Exception as e:
        logger.error("Yandex Cloud ML SDK не установлен: %s", e)
        raise

    # Для SDK используем только IAM-токен (gRPC не принимает Api-Key)
    iam_token = None
    try:
        iam_token = get_iam_token()
    except Exception as e:
        logger.warning("Не удалось получить IAM токен для SDK: %s", e)

    if not iam_token:
        # Не пытаемся инициализировать SDK с Api-Key, чтобы избежать UNAUTHENTICATED
        logger.warning(
            "IAM токен недоступен — пропускаем SDK и используем REST через get_headers()"
        )
        raise RuntimeError("IAM token required for Yandex ML SDK")

    if not FOLDER_ID:
        raise RuntimeError("FOLDER_ID не задан для инициализации Yandex ML SDK")

    logger.info("Инициализация Yandex ML SDK с IAM токеном")
    _SDK = YCloudML(folder_id=FOLDER_ID, auth=iam_token)
    return _SDK


def yandex_text_embedding(
    text: str, model_uri: Optional[str] = None, max_retries: int = 3, delay: float = 1.0
) -> List[float]:
    """
    Получение эмбеддинга текста с повторными попытками при ошибках сервера (REST API).
    """
    uri = model_uri or EMB_MODEL_URI
    url = f"{BASE_URL}/textEmbedding"
    payload = {"modelUri": uri, "text": text}

    for attempt in range(max_retries):
        try:
            headers = get_headers()
            r = requests.post(url, headers=headers, json=payload, timeout=60)
            if r.status_code == 200:
                resp = r.json()
                embedding = resp.get("embedding")
                return [float(x) for x in embedding] if embedding else []
            logger.warning("yandex_text_embedding: HTTP %s %s", r.status_code, r.text)
            if r.status_code >= 500 and attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            return []
        except requests.exceptions.Timeout:
            logger.warning("yandex_text_embedding: timeout on attempt %d", attempt + 1)
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            return []
        except Exception as e:
            logger.error("yandex_text_embedding error: %s", e)
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            return []
    return []


def yandex_batch_embeddings(texts: List[str], model_uri: Optional[str] = None) -> List[List[float]]:
    return [yandex_text_embedding(t, model_uri) for t in texts]


def _normalize_sdk_alternatives(result) -> Dict[str, Any]:
    """Нормализация результата SDK к формату {alternatives:[{message:{role,text}}]}"""
    alternatives: List[Dict[str, Any]] = []
    try:
        for alt in result:
            role = getattr(alt, "role", "assistant") or "assistant"
            text = getattr(alt, "text", None)
            if isinstance(alt, str) and text is None:
                text = alt
            if text is None and isinstance(alt, dict):
                text = alt.get("text")
                role = alt.get("role", role)
            alternatives.append({"message": {"role": role, "text": text or ""}})
    except Exception as e:
        logger.warning("Normalize SDK result failed: %s", e)
    return {"alternatives": alternatives, "model": {"name": TEXT_MODEL_NAME, "version": TEXT_MODEL_VERSION}}


def yandex_completion(
    prompt, model_uri: Optional[str] = None, max_tokens: int = 2000, temperature: float = 0.3
) -> Dict[str, Any]:
    """
    Генерация текста через Yandex Cloud ML SDK (llama/latest по умолчанию).
    Возвращает словарь с ключом 'alternatives' для совместимости с существующим кодом.
    """
    # Приводим промпт к messages
    if isinstance(prompt, list):
        messages: List[Dict[str, str]] = []
        for msg in prompt:
            if isinstance(msg, dict) and "role" in msg and "text" in msg:
                messages.append({"role": str(msg["role"]), "text": str(msg["text"])})
    elif isinstance(prompt, str):
        messages = [{"role": "user", "text": prompt}]
    else:
        messages = [{"role": "user", "text": str(prompt)}]

    # Попытка SDK
    try:
        sdk = _get_sdk()
        model = sdk.models.completions(TEXT_MODEL_NAME, model_version=TEXT_MODEL_VERSION)
        try:
            model = model.configure(temperature=temperature)
        except Exception:
            pass
        result = model.run(messages)
        return _normalize_sdk_alternatives(result)
    except Exception as e:
        logger.exception("yandex_completion via SDK failed: %s", e)
        # REST fallback
        try:
            mu = model_uri or TEXT_MODEL_URI
            if not mu:
                return {"error": f"SDK error: {e}"}
            url = f"{BASE_URL}/completion"
            payload = {
                "modelUri": mu,
                "completionOptions": {
                    "stream": False,
                    "temperature": temperature,
                    "maxTokens": max_tokens,
                },
                "messages": messages,
            }
            headers = get_headers()
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            if resp.status_code == 200:
                return resp.json()
            logger.error("yandex_completion REST fallback: HTTP %s %s", resp.status_code, resp.text)
            return {"error": f"HTTP {resp.status_code}: {resp.text}"}
        except Exception as e2:
            logger.error("yandex_completion REST fallback failed: %s", e2)
            return {"error": str(e)}


def yandex_classify(text: str, model_uri: Optional[str] = None, examples: Optional[List[dict]] = None) -> dict:
    uri = model_uri or os.getenv("YAND_CLASSIFY_MODEL_URI", "models/text-classification-??")
    url = f"{BASE_URL}/textClassification/classify"
    payload: Dict[str, Any] = {"modelUri": uri, "text": text}
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
        logger.error("yandex_classify error: %s", e)
        return {"error": str(e)}
