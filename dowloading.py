import os
import boto3
import tempfile
from pathlib import Path
from settings import REQUIRED_VARS


def download_from_s3():
    """Скачивает все файлы из S3 и возвращает список локальных путей"""
    s3 = boto3.client(
        "s3",
        endpoint_url=REQUIRED_VARS["S3_ENDPOINT"],
        aws_access_key_id=REQUIRED_VARS["S3_ACCESS_KEY"],
        aws_secret_access_key=REQUIRED_VARS["S3_SECRET_KEY"],
        region_name="ru-central1"
    )

    try:
        objects = s3.list_objects_v2(Bucket=REQUIRED_VARS["S3_BUCKET"], Prefix=REQUIRED_VARS["S3_PREFIX"])
    except Exception as e:
        print(f"Ошибка подключения: {e}")
        return []

    if "Contents" not in objects:
        return []

    local_files = []
    tmpdir = tempfile.mkdtemp()

    for obj in objects["Contents"]:
        key = obj.get("Key")
        if not key or key.endswith("/"):
            continue

        local_path = Path(tmpdir) / os.path.basename(key)
        try:
            s3.download_file(REQUIRED_VARS["S3_BUCKET"], key, str(local_path))
            if local_path.stat().st_size > 0:
                local_files.append(str(local_path))
        except Exception as e:
            print(f"Ошибка скачивания {key}: {e}")

    return local_files


def load_documents():
    """Пример загрузки содержимого файлов в память"""
    files = download_from_s3()
    docs = []
    for file in files:
        try:
            with open(file, "r", encoding="utf-8") as f:
                text = f.read()
                docs.append({"path": file, "content": text})
        except Exception as e:
            print(f"Ошибка чтения {file}: {e}")
    return docs
