import os
import time
import jwt
import requests
from settings import SERVICE_ACCOUNT_ID, KEY_ID, PRIVATE_KEY, FOLDER_ID, EMB_MODEL_URI, TEXT_MODEL_URI, CLASSIFY_MODEL_URI, VECTORSTORE_DIR
import logging

logger = logging.getLogger(__name__)

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