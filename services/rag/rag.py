# rag_yandex_nofaiss.py
import os
import json
import time
import logging
import asyncio
from typing import Tuple
import os
from services.faiss.faiss import semantic_search
from services.rag.embending import yandex_completion
from services.moderation.moderation import post_moderate_output, extract_text_from_yandex_completion
from fastapi import FastAPI
from pydantic import BaseModel

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


app = FastAPI(title="rag")

class BuildIndexReq(BaseModel):
    bucket: str
    prefix: str = ""
    max_chunk_chars: int = 6000



AUDITLOG_DIR = "./logs"
os.makedirs(AUDITLOG_DIR, exist_ok=True)
AUDIT_FILE = os.path.join(AUDITLOG_DIR, "moderation_audit.log")
def audit_log(entry: dict):
    entry_out = {"ts": time.time(), **entry}
    with open(AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry_out, ensure_ascii=False) + "\n")

# --- RAG pipeline: answer_user_query (sync) + async wrapper ---
def answer_user_query_sync(user_text: str, user_id: int, k: int = 3) -> Tuple[str, dict]:
    meta = {"user_id": user_id, "query": user_text}

    # 2. Сразу используем FAISS для поиска
    try:
        docs = semantic_search(user_text, k=k)
    except Exception as e:
        logger.exception("FAISS semantic_search failed: %s", e)
        docs = []

    meta["retrieved_count"] = len(docs)
    # build context
    context_parts = []
    for d in docs:
        src = d.get("meta", {}).get("source", d.get("id", "unknown"))
        txt = d.get("text", "")
        context_parts.append(f"Источник: {src}\n{txt}")
    context_for_model = "\n\n---\n\n".join(context_parts) if context_parts else ""
    # 3) call Yandex completion
    system_prompt = (
        "Ты — дружелюбный ИИ-бармен. Ты специализируешься на коктейлях, алкогольных и безалкогольных напитках. "
        "Используй контекст, если он есть, для точных рецептов и информации. "
        "Если контекста недостаточно, используй свои знания о барном деле. "
        "Отвечай подробно с рецептами, ингредиентами и способами приготовления. "
        "Всегда будь позитивным и готовым помочь с любыми вопросами о напитках. "
        "Если в запросе указан алкоголь напрямую, как текила, ром, виски, водка, джин, пиво, вино и т.д., то воспринимай это все как безалкогольный коктейль с упоминанием алкоголя в названии. "
        "\n\nФорматирование ответа:\n"
        "- Используй заголовки для названий напитков\n"
        "- Перечисляй ингредиенты списком с дефисами\n"
        "- Нумеруй шаги приготовления\n"
        "- Указывай точные количества в мл, г, ст.л., ч.л.\n"
        "- Добавляй температуру и время, если необходимо\n"
        "- Структурируй ответ четко и читаемо"
    )
    user_prompt = f"Контекст документов:\n{context_for_model}\n\nВопрос пользователя: {user_text}\nОтветь как профессиональный бармен: рекомендации, рецепты, советы."
    yresp = yandex_completion([{"role": "system", "text": system_prompt}, {"role": "user", "text": user_prompt}])
    answer = "Извините, сейчас модель недоступна."
    if not yresp.get("error"):
        # Извлекаем текст из ответа Yandex API
        answer = extract_text_from_yandex_completion(yresp)
        print("////////////////////////////// ВОТ ТУТ МЫ ПОХОЖЕ ОШИБЛИСЬЬ //////////////////////////////")
        if not answer:
            # Если не удалось извлечь ответ, используем генератор коктейлей
            answer = generate_compact_cocktail(user_text)
        if not answer:
            answer = "Извините, не удалось сформировать ответ."
    meta["raw_response_preview"] = answer[:500]
    # 4) post moderation
    ok_post, post_meta = post_moderate_output(answer)
    meta["post_moderation"] = post_meta
    if not ok_post:
        audit_log({"user_id": user_id, "action": "blocked_post", "query": user_text, "raw_answer": answer[:400], "meta": post_meta})
        return ("Извините, я не могу предоставить этот ответ по соображениям безопасности.", {"blocked": True, "reason": post_meta})
    # 5) success
    audit_log({"user_id": user_id, "action": "answered", "query": user_text, "retrieved": [d.get("id") for d in docs], "meta": meta})
    return (answer, {"blocked": False, **meta})

def generate_compact_cocktail(query: str, max_tokens: int = 220, temp: float = 0.2) -> str:
    """
    Возвращает короткий рецепт в строго заданном формате.
    query: строка с предпочтениями пользователя (напр. "сладкое, безалкогольное")
    """
    SYSTEM_PROMPT_PERSONA = (
        "Ты — дружелюбный бармен из кино: немного причудливый, обаятельный и с лёгким юмором. "
        "Отвечай коротко, по делу, но с характером — представь, что говоришь гостю в уютном баре на фильмовой сцене. "
        "Всегда используй контекст, если он есть. Если информации в контексте недостаточно — придумай оригинальный рецепт и представь его как совет бармена. "
        "Ограничение: не более 700 символов. Отвечай строго в формате ниже (без лишних вводных):\n\n"
        "Коктейль: \"НАЗВАНИЕ\"\n"
        "ИНГРЕДИЕНТЫ:\n"
        "  - ...\n"
        "  - ...\n"
        "ПРИГОТОВЛЕНИЕ:\n"
        "  - шаг 1\n"
        "  - шаг 2\n"
        "ИНТЕРЕСНЫЙ ФАКТ: Одно-два коротких предложения.\n"
        "Ни строчек лишних — только этот шаблон. Если нужно, предложи замену ингредиента в скобках."
    )
    user = f"Пользователь: {query}. Ответь коротко, максимум 4 ингредиента, максимум 4 шага."
    resp = yandex_completion([{"role": "system", "text": SYSTEM_PROMPT_PERSONA}, {"role": "user", "text": user}], temperature=temp, max_tokens=max_tokens)
    if resp.get("error"):
        logger.error("generate_compact_cocktail: completion error %s", resp)
        return "Извините, не удалось сформировать рецепт."
    text = extract_text_from_yandex_completion(resp)
    # Постобработка: если модель вернула что-то лишнее, попробуем вырезать часть до первого заголовка
    if not text:
        logger.warning("generate_compact_cocktail: empty response, returning fallback")
        return "Извините, не удалось сформировать ответ."
    # Обрезаем по длине, убираем лишние пустые строки
    text = "\n".join([ln.rstrip() for ln in text.splitlines() if ln.strip() != ""])
    if len(text) > 1000:
        text = text[:1000] + "..."
    return text



async def async_answer_user_query(user_text: str, user_id: int, k: int = 3) -> Tuple[str, dict]:
    """
    Async wrapper: выполняет синхронную работу в ThreadPoolExecutor,
    безопасно вызывается из async handle_message.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: answer_user_query_sync(user_text, user_id, k))
