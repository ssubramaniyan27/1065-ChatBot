from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import (
    HISTORY_TURNS,
    MAX_CONTEXT_CHUNKS,
    OLLAMA_HOST,
    OLLAMA_MODEL,
    PREFERRED_MODELS,
    VECTOR_RESULTS,
)
from app.ingest import load_meta, load_section_index
from app.store import collection_count, get_by_sections, query_vectors

SECTION_RE = re.compile(
    r"(?:§\s*)?(?:40\s*C\.?\s*F\.?\s*R\.?\s*)?(1065\.\d+[a-z]?)",
    re.IGNORECASE,
)

SYSTEM_PROMPT = """You are a local research assistant for the official public text of 40 CFR Part 1065 (EPA engine-testing procedures).

Your job is to explain what the regulation already says so engineers can follow it. That is lawful research, not advice on evading environmental law.

Rules:
- Answer the user's question using the provided Part 1065 excerpts.
- Cite the controlling text as 40 CFR 1065.xxx (or the appendix citation).
- If the excerpts do not contain the answer, say you cannot find it in the retrieved Part 1065 text. Do not guess.
- Do not invent limits, procedures, equipment requirements, or exceptions.
- Do not refuse ordinary questions about test procedures, sampling, dewpoint, instruments, fuels, calculations, or applicability. Those questions are about complying with the regulation.
- Only refuse if the user clearly asks how to falsify results, hide noncompliance, or cheat a test.
- This is a research aid, not legal advice. eCFR is authoritative but unofficial.
- Prefer the specific section the user asked about when it is present in the excerpts.
- Keep answers concise and precise. Quote short phrases when they matter.
"""


def extract_section_ids(question: str) -> list[str]:
    found: list[str] = []
    for match in SECTION_RE.finditer(question):
        section = match.group(1)
        if section not in found:
            found.append(section)
    return found


def neighbor_sections(section: str, catalog: list[dict[str, str]]) -> list[str]:
    ids = [item["section"] for item in catalog if item.get("kind") != "appendix"]
    lowered = {item.lower(): item for item in ids}
    resolved = lowered.get(section.lower())
    if not resolved:
        return [section]
    index = ids.index(resolved)
    nearby = ids[max(0, index - 1) : min(len(ids), index + 2)]
    return nearby


def fetch_sections(section_ids: list[str]) -> list[dict[str, Any]]:
    if not section_ids:
        return []
    return get_by_sections(section_ids)


def vector_search(question: str, limit: int = VECTOR_RESULTS) -> list[dict[str, Any]]:
    return query_vectors(question, limit)


def retrieve(question: str) -> list[dict[str, Any]]:
    catalog = load_section_index()
    wanted: list[str] = []
    for section in extract_section_ids(question):
        for neighbor in neighbor_sections(section, catalog):
            if neighbor not in wanted:
                wanted.append(neighbor)

    merged: dict[str, dict[str, Any]] = {}
    for row in fetch_sections(wanted) + vector_search(question):
        merged[row["id"]] = row

    exact = {section.lower() for section in extract_section_ids(question)}

    def sort_key(row: dict[str, Any]) -> tuple:
        section = str(row["metadata"].get("section") or "").lower()
        is_exact = 0 if section in exact else 1
        distance = row["distance"] if row["distance"] >= 0 else -1.0
        chunk_index = int(row["metadata"].get("chunk_index") or 0)
        return (is_exact, distance, section, chunk_index)

    ranked = sorted(merged.values(), key=sort_key)
    return ranked[:MAX_CONTEXT_CHUNKS]


def source_payload(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    sources: list[dict[str, str]] = []
    for row in rows:
        meta = row["metadata"]
        citation = str(meta.get("citation") or "")
        if not citation or citation in seen:
            continue
        seen.add(citation)
        document = row["document"]
        excerpt = re.sub(r"\s+", " ", document).strip()
        if len(excerpt) > 280:
            excerpt = excerpt[:277].rstrip() + "..."
        sources.append(
            {
                "citation": citation,
                "heading": str(meta.get("heading") or citation),
                "subpart": str(meta.get("subpart") or ""),
                "url": str(meta.get("url") or ""),
                "excerpt": excerpt,
            }
        )
    return sources


def build_context(rows: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for index, row in enumerate(rows, start=1):
        meta = row["metadata"]
        header = meta.get("citation") or meta.get("section")
        heading = meta.get("heading") or ""
        subpart = meta.get("subpart") or ""
        label = f"[{index}] {header} — {heading}"
        if subpart:
            label += f" ({subpart})"
        parts.append(f"{label}\n{row['document']}")
    return "\n\n-----\n\n".join(parts)


async def ollama_tags() -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{OLLAMA_HOST}/api/tags")
            response.raise_for_status()
            models = response.json().get("models") or []
            return [str(model.get("name") or "") for model in models if model.get("name")]
    except Exception:
        return []


def pick_model(names: list[str]) -> str | None:
    if OLLAMA_MODEL:
        return OLLAMA_MODEL
    lowered = names
    for preferred in PREFERRED_MODELS:
        for name in lowered:
            if name == preferred or name.startswith(f"{preferred}-") or name.startswith(f"{preferred}:"):
                return name
    return names[0] if names else None


async def status_payload() -> dict[str, Any]:
    meta = load_meta() or {}
    models = await ollama_tags()
    model = pick_model(models)
    return {
        "index_ready": collection_count() > 0 and bool(meta),
        "ecfr_date": meta.get("ecfr_date"),
        "source_url": meta.get("source_url"),
        "human_url": meta.get("human_url"),
        "part": meta.get("part", "40 CFR Part 1065"),
        "title": meta.get("title", "Engine-Testing Procedures"),
        "section_count": meta.get("section_count", 0),
        "chunk_count": meta.get("chunk_count", collection_count()),
        "ollama": {
            "ok": bool(models),
            "host": OLLAMA_HOST,
            "model": model,
            "models": models,
        },
    }


def history_messages(history: list[dict[str, str]]) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for item in history[-HISTORY_TURNS:]:
        role = item.get("role")
        content = (item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            cleaned.append({"role": role, "content": content[:2000]})
    return cleaned


def ndjson(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


async def stream_answer(question: str, history: list[dict[str, str]] | None = None) -> AsyncIterator[str]:
    status = await status_payload()
    if not status["index_ready"]:
        yield ndjson(
            {
                "type": "error",
                "message": "The Part 1065 index is not built yet. From this project folder run: python -m app.ingest",
            }
        )
        return
    if not status["ollama"]["ok"] or not status["ollama"]["model"]:
        yield ndjson(
            {
                "type": "error",
                "message": (
                    "Ollama is not available on localhost. Install Ollama, pull llama3.1:8b "
                    "(or llama3.2:3b), and keep it running."
                ),
            }
        )
        return

    rows = retrieve(question)
    sources = source_payload(rows)
    yield ndjson({"type": "sources", "sources": sources, "ecfr_date": status.get("ecfr_date")})

    context = build_context(rows)
    user_prompt = (
        f"eCFR point-in-time date: {status.get('ecfr_date')}\n\n"
        f"Retrieved 40 CFR Part 1065 excerpts:\n\n{context}\n\n"
        "The user wants a plain-language explanation of what this public regulation says. "
        "Do not refuse the question unless it asks how to cheat a test or hide noncompliance.\n\n"
        f"User question:\n{question}"
    )
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history_messages(history or []))
    messages.append({"role": "user", "content": user_prompt})

    payload = {
        "model": status["ollama"]["model"],
        "messages": messages,
        "stream": True,
        "options": {"temperature": 0.1, "num_ctx": 8192},
    }

    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", f"{OLLAMA_HOST}/api/chat", json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    yield ndjson(
                        {
                            "type": "error",
                            "message": f"Ollama returned HTTP {response.status_code}: {body[:400]}",
                        }
                    )
                    return
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    token = (data.get("message") or {}).get("content") or ""
                    if token:
                        yield ndjson({"type": "token", "text": token})
                    if data.get("done"):
                        yield ndjson({"type": "done", "model": status["ollama"]["model"]})
                        return
    except httpx.ConnectError:
        yield ndjson(
            {
                "type": "error",
                "message": "Could not connect to Ollama at 127.0.0.1. Start Ollama and try again.",
            }
        )
        return

    yield ndjson({"type": "done", "model": status["ollama"]["model"]})
