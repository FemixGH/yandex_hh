# rag_yandex_nofaiss.py
import os
import json
import time
import logging
import asyncio
from typing import Tuple, Optional, List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# локальные импорты (убедитесь, что пути корректны)
from services.faiss.faiss import semantic_search, build_docs_from_s3, build_index, load_index
from services.rag.embending import yandex_completion
from services.moderation.moderation import post_moderate_output, extract_text_from_yandex_completion
from services.rag.markdawn import format_cocktail_markdown

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = FastAPI(title="rag")

class BuildIndexReq(BaseModel):
    bucket: str
    prefix: str = ""
    max_chunk_chars: int = 6000

class AnswerReq(BaseModel):
    user_text: str
    user_id: int
    k: int = 3

AUDITLOG_DIR = "./logs"
os.makedirs(AUDITLOG_DIR, exist_ok=True)
AUDIT_FILE = os.path.join(AUDITLOG_DIR, "moderation_audit.log")

def audit_log(entry: dict):
    entry_out = {"ts": time.time(), **entry}
    # append lines
    with open(AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry_out, ensure_ascii=False) + "\n")


# ---- helper: безопасно извлекаем строку из вложенной структуры ----
def safe_find_first_str(obj) -> Optional[str]:
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        for v in obj.values():
            s = safe_find_first_str(v)
            if s:
                return s
    if isinstance(obj, list):
        for v in obj:
            s = safe_find_first_str(v)
            if s:
                return s
    return None


# ---- основной sync pipeline (можно вызывать в executor) ----
def answer_user_query_sync(user_text: str, user_id: int, k: int = 3) -> Tuple[str, dict]:
    meta = {"user_id": user_id, "query": user_text}
    docs = []
    try:
        docs = semantic_search(user_text, k=k)
    except Exception as e:
        logger.exception("FAISS semantic_search failed: %s", e)
        docs = []

    meta["retrieved_count"] = len(docs)

    # build context
    context_parts: List[str] = []
    for d in docs:
        src = d.get("meta", {}).get("source", d.get("id", "unknown"))
        txt = d.get("text", "")
        context_parts.append(f"Источник: {src}\n{txt}")
    context_for_model = "\n\n---\n\n".join(context_parts) if context_parts else ""

    # prompt
    system_prompt = (
        "Ты — дружелюбный ИИ-бармен. Ты специализируешься на коктейлях, алкогольных и безалкогольных напитках. "
        "Используй контекст, если он есть, для точных рецептов и информации. "
        "Если контекста недостаточно, используй свои знания о барном деле. "
        "Отвечай подробно с рецептами, ингредиентами и способами приготовления. "
        "Всегда будь позитивным и готовым помочь с любыми вопросами о напитках. "
        "Форматирование ответа:\n"
        "- Используй заголовки для названий напитков\n"
        "- Перечисляй ингредиенты списком с дефисами\n"
        "- Нумеруй шаги приготовления\n"
        "- Указывай точные количества в мл, г, ст.л., ч.л.\n"
        "- Добавляй температуру и время, если необходимо\n"
    )
    user_prompt = f"Контекст документов:\n{context_for_model}\n\nВопрос пользователя: {user_text}\nОтветь как профессиональный бармен: рекомендации, рецепты, советы."

    # вызов completion
    try:
        yresp = yandex_completion([{"role": "system", "text": system_prompt}, {"role": "user", "text": user_prompt}])
    except Exception as e:
        logger.exception("yandex_completion raised exception: %s", e)
        yresp = {"error": True, "status_code": 500, "text": str(e)}

    # ответ модели (безопасно)
    answer = ""
    md = ""

    if yresp.get("error"):
        logger.warning("yandex_completion returned error: %s", yresp)
        # не бросаем — попробуем fallback генератор
        answer = ""
    else:
        # извлекаем текст
        try:
            answer = extract_text_from_yandex_completion(yresp) or ""
        except Exception as e:
            logger.exception("extract_text_from_yandex_completion error: %s", e)
            # fallback: try to find first string leaf
            answer = safe_find_first_str(yresp) or ""

    # если модель ничего не дала — используем компактный генератор
    if not answer:
        try:
            answer = generate_compact_cocktail(user_text)
        except Exception as e:
            logger.exception("generate_compact_cocktail failed: %s", e)
            answer = ""

    # форматируем markdown-версию (md)
    try:
        if answer:
            md = format_cocktail_markdown(answer)
        else:
            md = "Извините, не удалось сформировать ответ."
    except Exception as e:
        logger.exception("format_cocktail_markdown failed: %s", e)
        # fallback plain text
        md = answer or "Извините, не удалось сформировать ответ."

    meta["raw_response_preview"] = (md or answer or "")[:500]

    # пост-модерация (используем исходный текстовый ответ)
    try:
        ok_post, post_meta = post_moderate_output(answer)
    except Exception as e:
        logger.exception("post_moderate_output failed: %s", e)
        ok_post, post_meta = False, {"error": "moderation_error", "reason": str(e)}

    meta["post_moderation"] = post_meta

    if not ok_post:
        audit_log({
            "user_id": user_id,
            "action": "blocked_post",
            "query": user_text,
            "raw_answer": (answer or "")[:400],
            "meta": post_meta
        })
        return ("Извините, я не могу предоставить этот ответ по соображениям безопасности.", {"blocked": True, "reason": post_meta})

    # success
    audit_log({
        "user_id": user_id,
        "action": "answered",
        "query": user_text,
        "retrieved": [d.get("id") for d in docs],
        "meta": meta
    })

    return (md, {"blocked": False, **meta})


def generate_compact_cocktail(query: str, max_tokens: int = 220, temp: float = 0.2) -> str:
    """
    Возвращает короткий рецепт в строго заданном формате.
    """
    SYSTEM_PROMPT_PERSONA = (
        "Ты — дружелюбный бармен из кино: немного причудливый, обаятельный и с лёгким юмором. "
        "Отвечай коротко, по делу, но с характером. Ограничение: не более 700 символов. Отвечай строго в формате:\n\n"
        "Коктейль: \"НАЗВАНИЕ\"\n"
        "ИНГРЕДИЕНТЫ:\n"
        "  - ...\n"
        "ПРИГОТОВЛЕНИЕ:\n"
        "  - шаг 1\n"
        "ИНТЕРЕСНЫЙ ФАКТ: Одно-два коротких предложения."
    )
    user = f"Пользователь: {query}. Ответь коротко, максимум 4 ингредиента, максимум 4 шага."
    try:
        resp = yandex_completion([{"role": "system", "text": SYSTEM_PROMPT_PERSONA}, {"role": "user", "text": user}], temperature=temp, max_tokens=max_tokens)
    except Exception as e:
        logger.exception("yandex_completion in generate_compact_cocktail failed: %s", e)
        return "Извините, не удалось сформировать рецепт."

    if resp.get("error"):
        logger.error("generate_compact_cocktail: completion error %s", resp)
        return "Извините, не удалось сформировать рецепт."

    text = extract_text_from_yandex_completion(resp) or safe_find_first_str(resp) or ""
    if not text:
        logger.warning("generate_compact_cocktail: empty response, returning fallback")
        return "Извините, не удалось сформировать ответ."

    # Обрезаем лишние пустые строки и длину
    text = "\n".join([ln.rstrip() for ln in text.splitlines() if ln.strip() != ""])
    if len(text) > 1000:
        text = text[:1000] + "..."
    return text


async def async_answer_user_query(user_text: str, user_id: int, k: int = 3) -> Tuple[str, dict]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, answer_user_query_sync, user_text, user_id, k)


# ---- FastAPI endpoints ----
@app.post("/answer")
async def api_answer(req: AnswerReq):
    try:
        answer, meta = await async_answer_user_query(req.user_text, req.user_id, req.k)
        return {"answer": answer, "meta": meta}
    except Exception as e:
        logger.exception("api_answer failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/build-index")
async def api_build_index(req: BuildIndexReq):
    """
    Построение индекса: собираем документы из S3 и запускаем build_index.
    """
    try:
        docs = build_docs_from_s3(req.bucket, req.prefix, max_chunk_chars=req.max_chunk_chars)
        if not docs:
            return {"ok": False, "reason": "no_docs_found", "docs_count": 0}
        ok = build_index(docs)
        if not ok:
            raise RuntimeError("build_index returned False")
        # optional: try to update incremental state here externally if you have such logic
        return {"ok": True, "docs_count": len(docs)}
    except Exception as e:
        logger.exception("api_build_index failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
