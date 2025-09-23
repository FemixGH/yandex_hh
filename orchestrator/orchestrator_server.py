from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import logging
import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

# Импорт ваших сервисов
from services.auth.auth import start_auth
from services.faiss.faiss import load_index
from services.rag.incremental_rag import update_rag_incremental
from services.orchestrator import query as orch_query_sync
from services.moderation.moderation import pre_moderate_input
from services.faiss.faiss import build_docs_from_s3, build_index
from services.rag.rag import answer_user_query_sync

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class QueryRequest(BaseModel):
    user_id: int
    text: str
    k: int = 3

class QueryResponse(BaseModel):
    answer: str
    blocked: bool = False
    reason: str = None
    meta: dict = {}

class BuildIndexRequest(BaseModel):
    bucket: str
    prefix: str = ""
    max_chunk_chars: int = 6000
    background: bool = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🚀 Starting Bartender AI Orchestrator...")
    
    # Инициализация аутентификации
    try:
        start_auth()
        logger.info("✅ Yandex Cloud auth initialized")
    except Exception as e:
        logger.error(f"❌ Auth initialization failed: {e}")
    
    # Инициализация индекса
    try:
        force_rebuild = os.getenv("FORCE_REBUILD_INDEX", "").lower() in ["true", "1", "yes"]
        
        if not force_rebuild:
            logger.info("🔄 Checking for incremental updates...")
            incremental_success = update_rag_incremental("vedroo")
            if incremental_success:
                logger.info("✅ Incremental update completed")
            else:
                logger.warning("⚠️ Incremental update failed, performing full rebuild")
                docs = build_docs_from_s3("vedroo", "")
                if docs:
                    build_index(docs)
        else:
            logger.info("🔄 Forced index rebuild...")
            docs = build_docs_from_s3("vedroo", "")
            if docs:
                build_index(docs)
        
        # Загрузка индекса
        index, vectors, docs = load_index()
        logger.info(f"📚 Vector index loaded ({len(docs)} documents)")
        
    except Exception as e:
        logger.warning(f"FAISS index not available: {e}")
    
    yield
    
    # Shutdown
    logger.info("🛑 Shutting down orchestrator...")

app = FastAPI(
    title="Bartender AI Orchestrator",
    description="Orchestration service for bartender AI",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/query", response_model=QueryResponse)
async def orchestrator_query(request: QueryRequest):
    """Основной эндпоинт для обработки запросов"""
    try:
        # Запускаем синхронную функцию оркестратора в thread pool
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor() as pool:
            result = await loop.run_in_executor(
                pool, 
                orch_query_sync, 
                request.user_id, 
                request.text,
                request.k
            )
        
        return QueryResponse(**result)
        
    except Exception as e:
        logger.exception(f"Query failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/moderate")
async def moderate_text(text: str):
    """Эндпоинт для модерации текста"""
    try:
        ok, meta = pre_moderate_input(text)
        return {"ok": ok, "meta": meta}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/build-index")
async def build_index_endpoint(request: BuildIndexRequest, background_tasks: BackgroundTasks):
    """Построение индекса"""
    def build_task():
        docs = build_docs_from_s3(request.bucket, request.prefix, request.max_chunk_chars)
        if docs:
            success = build_index(docs)
            return {"success": success, "docs_processed": len(docs)}
        return {"success": False, "reason": "No documents found"}
    
    if request.background:
        background_tasks.add_task(build_task)
        return {"status": "started", "message": "Index build started in background"}
    else:
        return build_task()

@app.post("/rag/answer")
async def rag_answer(request: QueryRequest):
    """Прямой вызов RAG (без оркестрации)"""
    try:
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor() as pool:
            answer, meta = await loop.run_in_executor(
                pool,
                answer_user_query_sync,
                request.text,
                request.user_id,
                request.k
            )
        
        return {"answer": answer, "meta": meta}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    return {
        "status": "healthy", 
        "service": "orchestrator",
        "timestamp": asyncio.get_event_loop().time()
    }

@app.get("/")
async def root():
    return {
        "message": "Bartender AI Orchestrator API",
        "version": "1.0.0",
        "endpoints": {
            "query": "/query",
            "moderate": "/moderate", 
            "build_index": "/build-index",
            "rag": "/rag/answer",
            "health": "/health"
        }
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("ORCHESTRATOR_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)