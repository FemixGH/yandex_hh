#!/usr/bin/env python3
from __future__ import annotations
import os
from yandex_cloud_ml_sdk import YCloudML

FOLDER_ID = os.getenv("FOLDER_ID") or os.getenv("YC_FOLDER_ID")
API_KEY = os.getenv("YANDEX_API_KEY")
MODEL_NAME = os.getenv("YAND_TEXT_MODEL_NAME", "llama")
MODEL_VERSION = os.getenv("YAND_TEXT_MODEL_VERSION", "latest")

messages = [
    {"role": "system", "text": "Ты помощник. Отвечай кратко."},
    {"role": "user", "text": "Привет! Что ты умеешь?"},
]

def main():
    if not FOLDER_ID:
        raise RuntimeError("Не задан FOLDER_ID (или YC_FOLDER_ID)")
    if not API_KEY:
        raise RuntimeError("Не задан YANDEX_API_KEY (можно также использовать IAM токен через SDK)")

    sdk = YCloudML(folder_id=FOLDER_ID, auth=API_KEY)
    model = sdk.models.completions(MODEL_NAME, model_version=MODEL_VERSION)
    model = model.configure(temperature=0.3)
    result = model.run(messages)

    for alt in result:
        print(f"[{getattr(alt, 'role', 'assistant')}] {getattr(alt, 'text', str(alt))}")

if __name__ == "__main__":
    main()

