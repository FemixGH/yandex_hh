# vectordb_qdrant.py
import os, time, json, hashlib
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

# ------------ config -------------
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", None)
COLLECTION = os.getenv("QDRANT_COLLECTION", "docs")
EMBED_MODEL = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")
EMBED_DIM = int(os.getenv("EMBED_DIM", 384))
BATCH = int(os.getenv("EMBED_BATCH", 16))

# ------------ clients -------------
qclient = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)

# local embedder
_embedder = None
def get_embedder():
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer(EMBED_MODEL)
    return _embedder

def embed_texts(texts: List[str]) -> List[List[float]]:
    model = get_embedder()
    vecs = model.encode(texts, batch_size=BATCH, convert_to_numpy=False)
    # normalize vectors if you prefer cosine (Qdrant supports cosine/inner product)
    return [list(v) for v in vecs]

# ------------ collection setup -------------
def ensure_collection():
    try:
        info = qclient.get_collection(collection_name=COLLECTION)
        print("Qdrant collection exists:", info.name)
    except Exception:
        print("Creating Qdrant collection:", COLLECTION)
        qclient.recreate_collection(
            collection_name=COLLECTION,
            vectors_config=qmodels.VectorParams(size=EMBED_DIM, distance=qmodels.Distance.COSINE),
            optimize_memory=True
        )

# ------------ helpers -------------
def sha256_text(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

# chunker: simple word-based
def chunk_text(text: str, chunk_size: int = 250, overlap: int = 50) -> List[str]:
    words = text.split()
    if not words:
        return []
    chunks = []
    i = 0
    n = len(words)
    while i < n:
        chunks.append(" ".join(words[i:i+chunk_size]))
        i += max(1, chunk_size - overlap)
    return chunks

# ------------ upsert document (chunks) -------------
def upsert_document(doc_id: str, owner_id: int, source: str, title: str,
                    text: str, skills: List[str], experience_years: int,
                    salary_from: Optional[int], salary_to: Optional[int],
                    location: Optional[str]):
    """
    1. chunk text
    2. embed batches
    3. upsert to qdrant with payload per chunk
    """
    doc_sha = sha256_text(text)
    chunks = chunk_text(text)
    if not chunks:
        return {"ok": False, "reason": "no_text"}

    # build payloads and vectors in batches
    points = []
    for b in range(0, len(chunks), BATCH):
        batch = chunks[b:b+BATCH]
        vecs = embed_texts(batch)
        for i, chunk_text in enumerate(batch):
            chunk_index = b + i
            payload = {
                "doc_id": doc_id,
                "doc_sha": doc_sha,
                "owner_id": owner_id,
                "source": source,
                "title": title,
                "skills": [s.lower() for s in (skills or [])],
                "experience_years": experience_years or 0,
                "salary_from": salary_from,
                "salary_to": salary_to,
                "location": location,
                "chunk_index": chunk_index,
                "text": chunk_text[:2000]  # limit stored text size per chunk if desired
            }
            # unique point id: doc_sha + idx
            point_id = f"{doc_sha}_{chunk_index}"
            points.append(qmodels.PointStruct(id=point_id, vector=vecs[i], payload=payload))

        # upsert batch into qdrant
        qclient.upsert(collection_name=COLLECTION, points=points)
        points.clear()
    return {"ok": True, "doc_sha": doc_sha, "chunks": len(chunks)}

# ------------ search with filter -------------
def build_qdrant_filter(must_skills: List[str]=None, min_experience: int=0,
                        location: Optional[str]=None, salary_from: Optional[int]=None, salary_to: Optional[int]=None):
    # Qdrant filter as dict; it supports "must"/"should" etc.
    must = []
    if must_skills:
        # contains_any style match (skill in skills)
        must.append({"key": "skills", "match": {"any": [s.lower() for s in must_skills]}})
    if min_experience:
        must.append({"key": "experience_years", "range": {"gte": min_experience}})
    if location:
        must.append({"key": "location", "match": {"value": location}})
    # salary_range: keep documents whose expected salary intersects vacancy range (simplified)
    if salary_from is not None or salary_to is not None:
        # e.g. require salary_to >= salary_from_vacancy (candidate.max >= vac.min)
        if salary_from is not None:
            must.append({"key": "salary_to", "range": {"gte": salary_from}})
        if salary_to is not None:
            must.append({"key": "salary_from", "range": {"lte": salary_to}})
    if not must:
        return None
    return {"must": must}

def search_similar(query: str, top_k: int = 20, filter_payload: Optional[Dict]=None):
    qvec = embed_texts([query])[0]
    qfilter = filter_payload
    hits = qclient.search(
        collection_name=COLLECTION,
        query_vector=qvec,
        limit=top_k,
        filter=qfilter
    )
    # hits: list of ScoredPoint (id, score, payload)
    results = []
    for h in hits:
        results.append({
            "id": h.id,
            "score": h.score,
            "payload": h.payload
        })
    return results

# ------------ match_vacancy pipeline -------------
def jaccard(a: List[str], b: List[str]) -> float:
    A, B = set([x.lower() for x in a]), set([x.lower() for x in b])
    if not A and not B:
        return 0.0
    inter = len(A & B)
    union = len(A | B)
    return inter / union if union else 0.0

def experience_score(req: int, cand: int) -> float:
    if cand >= req:
        return 1.0
    if req == 0:
        return 1.0
    return cand / req

def salary_score(vlow: Optional[int], vhigh: Optional[int], cand_expected: Optional[int]) -> float:
    if vlow is None or vhigh is None or cand_expected is None:
        return 0.5
    if vlow <= cand_expected <= vhigh:
        return 1.0
    # penalize simple
    if cand_expected < vlow:
        return max(0.0, 1 - (vlow - cand_expected) / max(1, vlow))
    return max(0.0, 1 - (cand_expected - vhigh) / max(1, cand_expected))

def match_vacancy(vacancy: Dict[str,Any], top_k: int=50, final_k: int=10):
    """
    vacancy: {title, description, skills, min_experience_years, salary_from, salary_to, location, strictness}
    """
    qtext = " ".join(filter(None, [vacancy.get("title",""), vacancy.get("description",""), ", ".join(vacancy.get("skills",[]))]))
    qfilter = build_qdrant_filter(must_skills=vacancy.get("must_have_skills", []) or vacancy.get("skills", []),
                                 min_experience=vacancy.get("min_experience_years",0),
                                 location=vacancy.get("location"),
                                 salary_from=vacancy.get("salary_from"),
                                 salary_to=vacancy.get("salary_to"))
    hits = search_similar(qtext, top_k=top_k, filter_payload=qfilter)
    # aggregate by doc_id: take best chunk per doc
    docs = {}
    for h in hits:
        payload = h["payload"]
        doc_id = payload.get("doc_id")
        if doc_id not in docs or h["score"] > docs[doc_id]["score"]:
            docs[doc_id] = {"doc_id": doc_id, "best_score": h["score"], "payload": payload}
    # compute final scores
    scored = []
    for d in docs.values():
        p = d["payload"]
        sim = d["best_score"]
        j = jaccard(vacancy.get("skills",[]), p.get("skills",[]))
        exp = experience_score(vacancy.get("min_experience_years",0), p.get("experience_years",0))
        sal = salary_score(vacancy.get("salary_from"), vacancy.get("salary_to"), p.get("salary_from") or p.get("salary_expected"))
        # weights - tune
        w_sim, w_j, w_e, w_s = 0.45, 0.25, 0.2, 0.1
        final = w_sim*sim + w_j*j + w_e*exp + w_s*sal
        scored.append({"doc_id": d["doc_id"], "score": final, "sim": sim, "jaccard": j, "experience": exp, "salary": sal, "payload": p})
    scored_sorted = sorted(scored, key=lambda x: x["score"], reverse=True)[:final_k]
    return scored_sorted

# ------------ delete / update helpers -------------
def delete_document_by_docsha(doc_sha: str):
    # delete points by id pattern doc_sha_idx -> use qclient.delete ?
    # Qdrant supports delete by filter:
    filt = {"must": [{"key":"doc_sha", "match": {"value": doc_sha}}]}
    qclient.delete(collection_name=COLLECTION, filter=filt)
    return {"ok": True}

# ------------ example usage -------------
if __name__ == "__main__":
    ensure_collection()
    # demo upsert
    upsert_document(
        doc_id="resume_123",
        owner_id=1,
        source="resume",
        title="Senior Python Developer",
        text="Senior backend engineer with 5 years experience in Python Django. Worked on microservices, CI/CD, PostgreSQL.",
        skills=["Python", "Django", "PostgreSQL"],
        experience_years=5,
        salary_from=150000,
        salary_to=250000,
        location="Moscow"
    )
    vac = {"title":"Senior Python dev","description":"Experience with Django and Postgres","skills":["Python","Django"], "min_experience_years":3, "salary_from":140000, "salary_to":220000, "location":"Moscow"}
    print(match_vacancy(vac))
