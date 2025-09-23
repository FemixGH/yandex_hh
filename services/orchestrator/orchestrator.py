# services/orchestrator_client.py
import os
import logging
import requests
from typing import Dict
from services.moderation.moderation import pre_moderate_input
from services.rag.rag_yandex_nofaiss import answer_user_query_sync

logger = logging.getLogger(__name__)

# MODE: "local" или "http"
MODE = os.getenv("ORCH_MODE", "local").lower()

# Для http-mode
ORCH_URL = os.getenv("ORCH_URL", "http://localhost:8000")
BOT_API_KEY = os.getenv("BOT_TO_ORCH_API_KEY", "botsecret")



# Локальные импорты (лениво, чтобы не были побочные эффекты при импорте)
def _local_query(user_id: int, text: str) -> Dict:
    # Здесь используем ваши существующие функции
    # импорт внутри функции чтобы избежать init-side effects при импорте модуля


    ok, meta = pre_moderate_input(text)
    if not ok:
        return {"ok": False, "reason": "moderation", "meta": meta}

    # answer_user_query_sync возвращает (answer, meta)
    answer, meta2 = answer_user_query_sync(text, user_id, k=3)
    if meta2.get("blocked"):
        return {"ok": False, "reason": "post_moderation", "meta": meta2}
    return {"ok": True, "answer": answer, "meta": meta2}

def _http_query(user_id: int, text: str) -> Dict:
    try:
        r = requests.post(f"{ORCH_URL}/query", json={"user_id": user_id, "text": text},
                          headers={"X-API-KEY": BOT_API_KEY}, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.exception("Orchestrator HTTP call failed")
        return {"ok": False, "reason": "orch_http_error", "meta": {"error": str(e)}}

def query(user_id: int, text: str) -> Dict:
    if MODE == "http":
        return _http_query(user_id, text)
    else:
        return _local_query(user_id, text)
