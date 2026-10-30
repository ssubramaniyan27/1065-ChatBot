from __future__ import annotations

import json
import shutil
from functools import lru_cache
from typing import Any

import numpy as np
from sentence_transformers import SentenceTransformer

from app.config import CHROMA_DIR, DATA_DIR, EMBEDDING_MODEL, SECTIONS_PATH

INDEX_DIR = CHROMA_DIR
EMBEDDINGS_PATH = INDEX_DIR / "embeddings.npy"
CHUNKS_PATH = INDEX_DIR / "chunks.json"


@lru_cache(maxsize=1)
def embedding_model() -> SentenceTransformer:
    return SentenceTransformer(EMBEDDING_MODEL)


def embed_texts(texts: list[str], show_progress: bool = False) -> np.ndarray:
    if not texts:
        return np.zeros((0, 384), dtype=np.float32)
    vectors = embedding_model().encode(
        texts,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=show_progress,
    )
    return np.asarray(vectors, dtype=np.float32)


def reset_index() -> None:
    if INDEX_DIR.exists():
        shutil.rmtree(INDEX_DIR)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)


def save_index(ids: list[str], documents: list[str], metadatas: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    embeddings = embed_texts(documents, show_progress=True)
    payload = [
        {"id": chunk_id, "document": document, "metadata": metadata}
        for chunk_id, document, metadata in zip(ids, documents, metadatas)
    ]
    np.save(EMBEDDINGS_PATH, embeddings)
    CHUNKS_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    embedding_model.cache_clear()


def _load_chunks() -> list[dict[str, Any]]:
    if not CHUNKS_PATH.exists() or not EMBEDDINGS_PATH.exists():
        return []
    return json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))


def _load_embeddings() -> np.ndarray:
    if not EMBEDDINGS_PATH.exists():
        return np.zeros((0, 384), dtype=np.float32)
    return np.load(EMBEDDINGS_PATH).astype(np.float32)


def collection_count() -> int:
    return len(_load_chunks())


def query_vectors(question: str, n_results: int) -> list[dict[str, Any]]:
    chunks = _load_chunks()
    embeddings = _load_embeddings()
    if not chunks or embeddings.size == 0:
        return []
    query = embed_texts([question])[0]
    scores = embeddings @ query
    order = np.argsort(-scores)[:n_results]
    rows: list[dict[str, Any]] = []
    for index in order:
        chunk = chunks[int(index)]
        rows.append(
            {
                "id": chunk["id"],
                "document": chunk["document"],
                "metadata": chunk.get("metadata") or {},
                "distance": float(1.0 - scores[int(index)]),
            }
        )
    return rows


def get_by_sections(section_ids: list[str]) -> list[dict[str, Any]]:
    wanted = {item.lower() for item in section_ids}
    rows: list[dict[str, Any]] = []
    for chunk in _load_chunks():
        metadata = chunk.get("metadata") or {}
        section = str(metadata.get("section") or "")
        if section.lower() in wanted:
            rows.append(
                {
                    "id": chunk["id"],
                    "document": chunk["document"],
                    "metadata": metadata,
                    "distance": -1.0,
                }
            )
    rows.sort(
        key=lambda row: (
            str(row["metadata"].get("section") or ""),
            int(row["metadata"].get("chunk_index") or 0),
        )
    )
    return rows


def index_ready() -> bool:
    return collection_count() > 0 and SECTIONS_PATH.exists()
