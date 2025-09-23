# services/auth/auth.py
import os
import time
import jwt
import requests
import logging
from typing import Optional
from settings import SERVICE_ACCOUNT_ID, KEY_ID, FOLDER_ID, VECTORSTORE_DIR

logger = logging.getLogger(__name__)

# Базовый URL Yandex — можно переопределить в окружении
BASE_URL = os.getenv("YANDEX_BASE_URL", "https://llm.api.cloud.yandex.net")

# Модульные переменные, которыми будут пользоваться другие модули
HEADERS: Optional[dict] = None
IAM_TOKEN: Optional[str] = None

def load_private_key_from_pem(pem_file_path: str) -> str:
    try:
        with open(pem_file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Ошибка при чтении PEM файла {pem_file_path}: {e}")
        raise

def create_jwt(sa_id: str, key_id: str, private_key: str) -> str:
    now = int(time.time())
    payload = {
        "aud": "https://iam.api.cloud.yandex.net/iam/v1/tokens",
        "iss": sa_id,
        "iat": now,
        "exp": now + 360  # 6 минут жизни JWT
    }
    encoded = jwt.encode(
        payload,
        private_key,
        algorithm="PS256",
        headers={"kid": key_id}
    )
    # PyJWT может возвращать bytes или str в зависимости от версии
    if isinstance(encoded, bytes):
        encoded = encoded.decode("utf-8")
    return encoded

def exchange_jwt_for_iam_token(jwt_token: str) -> str:
    url = "https://iam.api.cloud.yandex.net/iam/v1/tokens"
    resp = requests.post(url, json={"jwt": jwt_token})
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to get IAM token: {resp.status_code} {resp.text}")
    data = resp.json()
    return data["iamToken"]

def start_auth() -> dict:
    """
    Выполняет аутентификацию и возвращает HEADERS.
    Устанавливает модульную переменную HEADERS.
    """
    global HEADERS, IAM_TOKEN

    if not SERVICE_ACCOUNT_ID or not KEY_ID:
        raise RuntimeError("SERVICE_ACCOUNT_ID / KEY_ID not set. Cannot obtain IAM token.")

    pem_file_path = os.path.join(os.path.dirname(__file__), "private-key.pem")
    private_key = load_private_key_from_pem(pem_file_path)
    jwt_token = create_jwt(SERVICE_ACCOUNT_ID, KEY_ID, private_key)
    IAM_TOKEN = exchange_jwt_for_iam_token(jwt_token)
    logger.info("IAM token successfully obtained")

    HEADERS = {
        "Authorization": f"Bearer {IAM_TOKEN}",
        "Content-Type": "application/json"
    }
    if FOLDER_ID:
        HEADERS["X-Folder-Id"] = FOLDER_ID

    # ensure vectorstore dir exists (as before)
    os.makedirs(VECTORSTORE_DIR, exist_ok=True)

    logger.info("Auth headers set, using BASE_URL=%s", BASE_URL)
    return HEADERS

def get_headers(auto_start: bool = True) -> dict:
    """
    Возвращает HEADERS. Если HEADERS ещё нет и auto_start=True — запускает start_auth().
    """
    global HEADERS
    if HEADERS is None:
        if auto_start:
            return start_auth()
        raise RuntimeError("Auth headers not set. Call start_auth() first.")
    return HEADERS
