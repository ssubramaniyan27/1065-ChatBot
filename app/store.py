from __future__ import annotations

import json
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from sentence_transformers import SentenceTransformer

from app.config import CHROMA_DIR, DATA_DIR, EMBEDDING_MODEL, SECTIONS_PATH

INDEX_DIR = CHROMA_DIR
EMBEDDINGS_PATH = INDEX_DIR / "embeddings.npy"
CHUNKS_PATH = INDEX_DIR / "chunks.json"


@lru_cache(maxsize=1)
def embedding_model() -> SentenceTransformer:
    try:
        return SentenceTransformer(EMBEDDING_MODEL, local_files_only=True)
    except OSError:
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


def _mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return 0


@lru_cache(maxsize=1)
def _cached_index(chunks_mtime: int, embeddings_mtime: int) -> tuple[list[dict[str, Any]], np.ndarray]:
    if not chunks_mtime or not embeddings_mtime:
        return [], np.zeros((0, 384), dtype=np.float32)
    chunks = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
    embeddings = np.load(EMBEDDINGS_PATH).astype(np.float32)
    return chunks, embeddings


def _index() -> tuple[list[dict[str, Any]], np.ndarray]:
    return _cached_index(_mtime_ns(CHUNKS_PATH), _mtime_ns(EMBEDDINGS_PATH))


def _load_chunks() -> list[dict[str, Any]]:
    return _index()[0]


def preload() -> None:
    _index()
    embed_texts(["warm up"])


def collection_count() -> int:
    return len(_load_chunks())


def query_vectors(question: str, n_results: int) -> list[dict[str, Any]]:
    chunks, embeddings = _index()
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
