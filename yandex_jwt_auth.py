import os
import time
import jwt
import requests
from settings import SERVICE_ACCOUNT_ID, KEY_ID, FOLDER_ID, EMB_MODEL_URI, TEXT_MODEL_URI, CLASSIFY_MODEL_URI, VECTORSTORE_DIR
import logging

logger = logging.getLogger(__name__)

def load_private_key_from_pem(pem_file_path: str) -> str:
    """Загружает приватный ключ из PEM файла"""
    try:
        with open(pem_file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        logger.error(f"PEM файл не найден: {pem_file_path}")
        raise
    except Exception as e:
        logger.error(f"Ошибка при чтении PEM файла: {e}")
        raise

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
        private_key,  # ключ уже в правильном формате из PEM файла
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

if SERVICE_ACCOUNT_ID and KEY_ID:
    # Загружаем приватный ключ из PEM файла
    pem_file_path = os.path.join(os.path.dirname(__file__), "private-key.pem")
    PRIVATE_KEY = load_private_key_from_pem(pem_file_path)

    jwt_token = create_jwt(SERVICE_ACCOUNT_ID, KEY_ID, PRIVATE_KEY)
    IAM_TOKEN = exchange_jwt_for_iam_token(jwt_token)
    logger.info("IAM token successfully obtained")
else:
    raise RuntimeError("SERVICE_ACCOUNT_ID / KEY_ID not set. Cannot obtain IAM token.")

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