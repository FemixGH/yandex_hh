
import os
import faiss
import pickle
import numpy as np
from typing import List
from yandex_api import yandex_batch_embeddings
import logging

logger = logging.getLogger(__name__)
INDEX_DIR = os.getenv("FAISS_INDEX_DIR", "faiss_index_yandex")
METADATA_FILE = os.path.join(INDEX_DIR, "meta.pkl")
VECTORS_FILE = os.path.join(INDEX_DIR, "vectors.npy")
IDX_FILE = os.path.join(INDEX_DIR, "index.faiss")

os.makedirs(INDEX_DIR, exist_ok=True)

def build_index(docs: List[dict], model_uri: str = None):
    """
    docs: list of {'id': str, 'text': str, 'meta': {...}}
    """
    texts = [d["text"] for d in docs]
    embeddings = yandex_batch_embeddings(texts, model_uri=model_uri)  # list of vectors
    dim = len(embeddings[0])
    mat = np.array(embeddings, dtype='float32')
    # L2 index (inner product or cosine — we can normalize for cosine)
    index = faiss.IndexFlatIP(dim)
    # normalize rows for cosine similarity
    faiss.normalize_L2(mat)
    index.add(mat)
    faiss.write_index(index, IDX_FILE)
    np.save(VECTORS_FILE, mat)
    # save metadata
    with open(METADATA_FILE, "wb") as f:
        pickle.dump(docs, f)
    logger.info("FAISS index built: %s (dim=%d, n=%d)", IDX_FILE, dim, len(docs))
    return True

def load_index():
    if not os.path.exists(IDX_FILE):
        raise FileNotFoundError("FAISS index not found")
    index = faiss.read_index(IDX_FILE)
    with open(METADATA_FILE, "rb") as f:
        docs = pickle.load(f)
    mat = np.load(VECTORS_FILE)
    return index, mat, docs

def semantic_search(query: str, k: int = 3, model_uri: str = None):
    index, mat, docs = load_index()
    q_emb = np.array(yandex_batch_embeddings([query], model_uri=model_uri), dtype='float32')
    faiss.normalize_L2(q_emb)
    D, I = index.search(q_emb, k)
    results = []
    for idx, dist in zip(I[0], D[0]):
        if idx < 0 or idx >= len(docs):
            continue
        d = docs[idx].copy()
        d["score"] = float(dist)
        results.append(d)
    return results
