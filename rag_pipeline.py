import os
import tempfile
import logging
import boto3
import requests
import json
from dotenv import load_dotenv

# langchain
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
 # TODO модель эмбедингов какая?
from langchain_community.vectorstores import FAISS


# === Настройка логов ===
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

load_dotenv()

# проверка переменных окружения
REQUIRED_VARS = {
    "S3_ENDPOINT": os.getenv("S3_ENDPOINT"),
    "S3_ACCESS_KEY": os.getenv("S3_ACCESS_KEY"),
    "S3_SECRET_KEY": os.getenv("S3_SECRET_KEY"),
    "S3_BUCKET": os.getenv("S3_BUCKET"),
    "YANDEX_API_KEY": os.getenv("YANDEX_API_KEY"),
}

for var_name, value in REQUIRED_VARS.items():
    if not value or value.strip().lower() == "none":
        raise ValueError(f"{var_name} не задан. Проверьте .env.")


# безопасная загрузка из S3
def download_from_s3(key: str):
    if not isinstance(key, str):
        return None
    if key.endswith('/'):
        return None
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=REQUIRED_VARS["S3_ENDPOINT"],
            aws_access_key_id=REQUIRED_VARS["S3_ACCESS_KEY"],
            aws_secret_access_key=REQUIRED_VARS["S3_SECRET_KEY"],
        )
        head = s3.head_object(Bucket=REQUIRED_VARS["S3_BUCKET"], Key=key)
        size_before = head.get("ContentLength", 0)

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            s3.download_fileobj(REQUIRED_VARS["S3_BUCKET"], key, tmp)
            tmp.flush()
            size_after = os.path.getsize(tmp.name)

        if size_before != size_after:
            os.remove(tmp.name)
            return None

        logging.info(f"Файл {key} успешно загружен ({size_after} байт)")
        return tmp.name
    except Exception as e:
        logging.error(f"Ошибка при скачивании {key}: {e}")
        return None


# валидация содержимого документов
def validate_docs(loaded):
    valid_docs = [
        doc for doc in loaded
        if hasattr(doc, 'page_content')
        and isinstance(doc.page_content, str)
        and doc.page_content.strip()
    ]
    logging.info(f"После валидации осталось {len(valid_docs)} документов")
    return valid_docs


# парсинг документов
def load_document(path):
    if path.endswith(".pdf"):
        docs = PyPDFLoader(path).load()
    elif path.endswith(".txt"):
        docs = TextLoader(path).load()
    else:
        docs = []
    logging.info(f"Документ {path} загружен, получено {len(docs)} страниц/чанков")
    return docs


# разбиение и создание индекса
def build_faiss_index(docs, index_path="faiss_index"):
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    chunks = splitter.split_documents(docs)
    logging.info(f"Разбито на {len(chunks)} чанков")

    embeddings =  # TODO модель эмбедингов какая?
    vectorstore = FAISS.from_documents(chunks, embeddings)
    vectorstore.save_local(index_path)
    logging.info(f"FAISS-индекс сохранён: {index_path}")
    return vectorstore


# поиск по индексу
def semantic_search(query, index_path="faiss_index", k=3, threshold=0.7):
    embeddings = # TODO модель эмбедингов какая?
    vectorstore = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
    docs = vectorstore.similarity_search(query, k=k)
    logging.info(f"Поиск по запросу '{query}' вернул {len(docs)} результатов")

    # фильтрация по порогу
    valid = [d for d in docs if d.metadata.get("score", 1.0) >= threshold]
    logging.info(f"После фильтрации осталось {len(valid)} результатов")
    return valid


# запрос к YandexGPT
def ask_yandexgpt(query, context):
    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {
        "Authorization": f"Api-Key {REQUIRED_VARS['YANDEX_API_KEY']}",
        "Content-Type": "application/json"
    }
    data = {
        "modelUri": "gpt://b1g-example-id/yandexgpt-lite/latest",
        "completionOptions": {"stream": False, "temperature": 0.3},
        "messages": [
            {"role": "system", "text": f"Контекст: {context}"},
            {"role": "user", "text": query}
        ]
    }
    resp = requests.post(url, headers=headers, data=json.dumps(data))
    if resp.status_code == 200:
        logging.info("Запрос к YandexGPT успешен")
        return resp.json()
    else:
        logging.error(f"Ошибка YandexGPT: {resp.status_code}, {resp.text}")
        return None


# === Пример пайплайна ===
if __name__ == "__main__":
    file_path = download_from_s3("docs/instruction.pdf")
    if not file_path:
        raise SystemExit("Файл не скачан")

    docs = load_document(file_path)
    docs = validate_docs(docs)

    build_faiss_index(docs)

    results = semantic_search("Кто имеет доступ к системе?")
    context = " ".join([r.page_content for r in results])

    answer = ask_yandexgpt("Кто имеет доступ к системе?", context)
    print(json.dumps(answer, ensure_ascii=False, indent=2))
