from settings import MOD_URL, RAG_URL
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import logging
import requests

logger = logging.getLogger(__name__)

app = FastAPI()

class QueryRequest(BaseModel):
    user_id: int
    text: str
    
    
@app.post("/query")
def query(req: QueryRequest):
    try:
        r = requests.post(f"{MOD_URL}/moderate", json={"text": req.text}, timeout=30)
        r.raise_for_status
        mod_r = r.json()
    except Exception as e:
        logger.exception("Moderation request failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Moderation service error: {e}")
    if not mod_r.get("ok"):
        logger.info("Message blocked by moderation: %s", mod_r)
        return {"ok": False, "reason": "moderation", "meta": mod_r.get("meta", {})}
    
    try:
        r = requests.post(f"{RAG_URL}/query", json={"user_id": req.user_id, "query": req.text}, timeout=60)
        r.raise_for_status()
        rag_r = r.json()
    except Exception as e:
        logger.exception("RAG request failed: %s", e)
        raise HTTPException(status_code=500, detail=f"RAG service error: {e}")
    

