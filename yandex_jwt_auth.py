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
        private_key,
        algorithm="PS256",
        headers={"kid": key_id}
    )
    return encoded

def exchange_jwt_for_iam_token(jwt_token):
    resp = requests.post(
        "https://iam.api.cloud.yandex.net/iam/v1/tokens",
        json={"jwt": jwt_token},
        timeout=10
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to get IAM token: {resp.status_code} {resp.text}")
    return resp.json()["iamToken"]

def get_iam_token():
    """Lazy initialization of IAM token"""
    if not SERVICE_ACCOUNT_ID or not KEY_ID:
        raise RuntimeError("SERVICE_ACCOUNT_ID / KEY_ID not set. Cannot obtain IAM token.")

    # Загружаем приватный ключ из PEM файла
    pem_file_path = os.path.join(os.path.dirname(__file__), "private-key.pem")
    private_key = load_private_key_from_pem(pem_file_path)

    jwt_token = create_jwt(SERVICE_ACCOUNT_ID, KEY_ID, private_key)
    return exchange_jwt_for_iam_token(jwt_token)

def get_headers():
    """Get headers for Yandex API requests with lazy token initialization"""
    try:
        iam_token = get_iam_token()
        headers = {
            "Authorization": f"Bearer {iam_token}",
            "Content-Type": "application/json"
        }
        if FOLDER_ID:
            headers["X-Folder-Id"] = FOLDER_ID
        return headers
    except Exception as e:
        logger.error(f"Failed to get Yandex API headers: {e}")
        raise

# Initialize constants that don't require API access
BASE_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1"

# Log configuration (but don't fail if API keys are missing)
if SERVICE_ACCOUNT_ID and KEY_ID:
    logger.info("Yandex API credentials found")
else:
    logger.warning("Yandex API credentials not configured - API calls will fail")

logger.info("Using EMB_MODEL_URI=%s TEXT_MODEL_URI=%s CLASSIFY_MODEL_URI=%s VECTORSTORE_DIR=%s",
            EMB_MODEL_URI, TEXT_MODEL_URI, CLASSIFY_MODEL_URI, VECTORSTORE_DIR)

os.makedirs(VECTORSTORE_DIR, exist_ok=True)

# Legacy compatibility - provide HEADERS but with lazy evaluation
class LazyHeaders:
    def __getitem__(self, key):
        return get_headers()[key]

    def get(self, key, default=None):
        try:
            return get_headers().get(key, default)
        except:
            return default

    def items(self):
        return get_headers().items()

    def keys(self):
        return get_headers().keys()

    def values(self):

