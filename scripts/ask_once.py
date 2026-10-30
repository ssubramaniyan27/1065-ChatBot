import json
import sys

import httpx

QUESTION = sys.argv[1] if len(sys.argv) > 1 else "What does 40 CFR 1065.170 require?"
sources: list[str] = []
tokens: list[str] = []
err = None

with httpx.stream(
    "POST",
    "http://127.0.0.1:8000/api/chat",
    json={"message": QUESTION, "history": []},
    timeout=None,
) as response:
    print(f"HTTP {response.status_code}", flush=True)
    for line in response.iter_lines():
        if not line:
            continue
        event = json.loads(line)
        kind = event.get("type")
        if kind == "sources":
            sources = [item.get("citation") or "" for item in event.get("sources") or []]
            print("SOURCES:", "; ".join(sources), flush=True)
        elif kind == "token":
            tokens.append(event.get("text") or "")
            print(event.get("text") or "", end="", flush=True)
        elif kind == "error":
            err = event.get("message")
            print("\nERR:", err, flush=True)
            break
        elif kind == "done":
            break

print("\n---DONE---", flush=True)
if err:
    sys.exit(1)
if not tokens:
    sys.exit(2)
