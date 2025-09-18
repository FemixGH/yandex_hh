import os
import dotenv

dotenv.load_dotenv()

SERVICE_ACCOUNT_ID = os.getenv("SERVICE_ACCOUNT_ID")
KEY_ID = os.getenv("KEY_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
FOLDER_ID = os.getenv("FOLDER_ID") or os.getenv("YC_FOLDER_ID")

# Model URIs — можно явно указать в .env, иначе соберём из FOLDER_ID
EMB_MODEL_URI = os.getenv("YAND_EMBEDDING_MODEL_URI") or (f"emb://{FOLDER_ID}/text-search-doc/latest" if FOLDER_ID else None)
TEXT_MODEL_URI = os.getenv("YAND_TEXT_MODEL_URI") or (f"gpt://{FOLDER_ID}/yandexgpt/latest" if FOLDER_ID else None)
CLASSIFY_MODEL_URI = os.getenv("YAND_CLASSIFY_MODEL_URI") or (f"cls://{FOLDER_ID}/yandexgpt-lite/latest" if FOLDER_ID else None)

VECTORSTORE_DIR = os.getenv("VECTORSTORE_DIR", "./vectorstore")

S3_ENDPOINT = "https://storage.yandexcloud.net"
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY")
S3_BUCKET = "vedroo"
S3_PREFIX = ""