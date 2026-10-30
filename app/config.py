from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
STATIC_DIR = Path(__file__).resolve().parent / "static"

XML_PATH = DATA_DIR / "ecfr-title40-part1065.xml"
META_PATH = DATA_DIR / "ingest_meta.json"
SECTIONS_PATH = DATA_DIR / "sections.json"
CHROMA_DIR = DATA_DIR / "chroma"

COLLECTION_NAME = "cfr_1065"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

CFR_TITLE = 40
CFR_PART = "1065"
ECFR_VERSIONER = "https://www.ecfr.gov/api/versioner/v1"
ECFR_TITLES_URL = f"{ECFR_VERSIONER}/titles.json"
ECFR_USER_AGENT = "1065-local-chatbot/1.0 (local research aid; public eCFR)"

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "").strip()
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "30m").strip()
PREFERRED_MODELS = ("llama3.1:8b", "llama3.2:3b", "llama3.1", "llama3.2", "llama3")
NUM_CTX = 8192

VECTOR_RESULTS = 6
MAX_CONTEXT_CHUNKS = 6
MAX_CHUNK_CHARS = 1800
CHUNK_OVERLAP = 200
HISTORY_TURNS = 4
HISTORY_MAX_CHARS = 1200
