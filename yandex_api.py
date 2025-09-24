# yandex_api.py
import os
import logging
import requests
import time
from typing import List, Optional, Dict, Any, Tuple
from settings import EMB_MODEL_URI, TEXT_MODEL_URI, FOLDER_ID
from yandex_jwt_auth import get_headers, BASE_URL, get_iam_token

logger = logging.getLogger(__name__)

# Попытка импортировать SDK (необязательно установлено в ранних окружениях)
try:
    from yandex_cloud_ml_sdk import YCloudML  # type: ignore
    _YCML_AVAILABLE = True
except Exception:
    _YCML_AVAILABLE = False


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


# --- YCloudML helpers ---

def _parse_model_from_uri(model_uri: Optional[str]) -> Tuple[str, str]:
    """Возвращает (model_name, model_version) для вызова SDK.
    Приоритет:
      1) Явно переданный model_uri (gpt://.../<name>/<version>)
      2) ENV override: YAND_TEXT_MODEL_NAME (+ YAND_TEXT_MODEL_VERSION)
      3) TEXT_MODEL_URI из настроек
      4) Дефолт: yandexgpt-5-lite/latest
    """
    env_name = os.getenv("YAND_TEXT_MODEL_NAME")
    env_ver = os.getenv("YAND_TEXT_MODEL_VERSION") or "latest"

    # 1) Явно переданный URI
    if model_uri and "://" in model_uri:
        try:
            _, rest = model_uri.split("://", 1)
            parts = rest.split("/")
            if len(parts) >= 2:
                name = parts[-2]
                ver = parts[-1] if len(parts) >= 3 else "latest"
                return (name or "yandexgpt-5-lite"), (ver or "latest")
        except Exception:
            pass

    # 2) ENV override
    if env_name:
        return env_name, (env_ver or "latest")

    # 3) TEXT_MODEL_URI
    if TEXT_MODEL_URI and "://" in TEXT_MODEL_URI:
        try:
            _, rest = TEXT_MODEL_URI.split("://", 1)
            parts = rest.split("/")
            if len(parts) >= 2:
                name = parts[-2]
                ver = parts[-1] if len(parts) >= 3 else "latest"
                return (name or "yandexgpt-5-lite"), (ver or "latest")
        except Exception:
            pass

    # 4) Дефолт
    return "yandexgpt-5-lite", "latest"


def _get_yc_auth_token() -> Optional[str]:
    """Пробуем получить IAM токен (ENV/metadata/JWT), иначе берём API Key из окружения."""
    try:
        token = get_iam_token()
        if token:
            return token
    except Exception as e:
        logger.debug(f"IAM token unavailable: {e}")
    api_key = os.environ.get("YANDEX_API_KEY")
    return api_key


def _to_alternatives_json_from_sdk_result(result: Any) -> Dict[str, Any]:
    """Конвертирует результат SDK completions к прежнему формату ответа с alternatives/message/content.
    Это необходимо для совместимости с extract_text_from_yandex_completion.
    """
    texts: List[str] = []
    try:
        for alt in result:
            # Пытаемся извлечь текст из альтернативы разных форматов
            text_item = None
            # Объект с атрибутом text
            text_item = getattr(alt, "text", None)
            if not text_item:
                # alt.message.text
                msg = getattr(alt, "message", None)
                if msg is not None:
                    text_item = getattr(msg, "text", None)
            # dict-подобный
            if not text_item and isinstance(alt, dict):
                text_item = alt.get("text") or (alt.get("message") or {}).get("text")
            if text_item:
                texts.append(str(text_item))
    except Exception as e:
        logger.debug(f"Iterating SDK result failed: {e}")

    # Фолбэк: если SDK вернул единичный объект с output_text
    if not texts:
        try:
            single_txt = getattr(result, "output_text", None) or getattr(result, "text", None)
            if single_txt:
                texts.append(str(single_txt))
        except Exception:
            pass

    out_text = "\n".join([t for t in texts if t]).strip()
    if not out_text:
        out_text = ""

    return {
        "alternatives": [
            {
                "message": {
                    "role": "assistant",
                    "text": out_text,
                    "content": [
                        {"type": "text", "text": out_text}
                    ]
                }
            }
        ]
    }


def _ycml_completion(prompt: Any, model_uri: Optional[str], max_tokens: int, temperature: float) -> Dict[str, Any]:
    """Вызов генерации через YCloudML SDK по схеме из документации."""
    if not _YCML_AVAILABLE:
        raise RuntimeError("yandex_cloud_ml_sdk is not installed")

    auth = _get_yc_auth_token()
    if not auth:
        raise RuntimeError("No IAM token or API Key available for YCloudML auth")

    sdk = YCloudML(folder_id=FOLDER_ID, auth=auth)

    name, ver = _parse_model_from_uri(model_uri)
    model = sdk.models.completions(name, model_version=ver)
    # Настройка параметров генерации
    cfg = {"temperature": temperature}
    # Параметр лимита токенов может называться по-разному; SDK обычно использует max_tokens
    try:
        model = model.configure(**cfg, max_tokens=max_tokens)
    except TypeError:
        # Если max_tokens не поддерживается напрямую — применим только temperature
        model = model.configure(**cfg)

    # Сбор сообщений
    if isinstance(prompt, list):
        messages = []
        for msg in prompt:
            if isinstance(msg, dict) and "role" in msg and "text" in msg:
                messages.append({"role": msg["role"], "text": msg["text"]})
        if not messages:
            messages = [{"role": "user", "text": str(prompt)}]
    elif isinstance(prompt, str):
        messages = [{"role": "user", "text": prompt}]
    else:
        messages = [{"role": "user", "text": str(prompt)}]

    # Запуск
    result = model.run(messages)
    return _to_alternatives_json_from_sdk_result(result)


def yandex_completion(prompt, model_uri: Optional[str] = None, max_tokens: int = 2000, temperature: float = 0.3) -> Dict[str, Any]:
    """
    Получение текстового ответа от LLM. По-умолчанию используем YCloudML SDK; при проблемах — REST-фолбэк.
    Возвращаем формат совместимый с extract_text_from_yandex_completion.
    """
    # Сначала пытаемся через SDK
    try:
        return _ycml_completion(prompt, model_uri, max_tokens, temperature)
    except Exception as e:
        logger.warning(f"YCloudML SDK completion failed, fallback to REST: {e}")

    # --- REST фолбэк ---
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
