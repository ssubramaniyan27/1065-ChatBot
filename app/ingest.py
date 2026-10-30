"""Download 40 CFR Part 1065 from the public eCFR API and index it locally."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

import httpx
from lxml import etree

from app.config import (
    CFR_PART,
    CFR_TITLE,
    CHUNK_OVERLAP,
    DATA_DIR,
    ECFR_TITLES_URL,
    ECFR_USER_AGENT,
    ECFR_VERSIONER,
    MAX_CHUNK_CHARS,
    META_PATH,
    SECTIONS_PATH,
    XML_PATH,
)
from app.store import reset_index, save_index

SKIP_TAGS = {"PRTPAGE", "FTNT", "REVTXT"}


@dataclass
class SectionDoc:
    section: str
    citation: str
    heading: str
    subpart: str
    url: str
    text: str
    kind: str


def localname(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def normalize_space(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


def latest_title_date(client: httpx.Client) -> str:
    response = client.get(ECFR_TITLES_URL)
    response.raise_for_status()
    payload = response.json()
    for item in payload.get("titles", []):
        if int(item.get("number", 0)) == CFR_TITLE:
            for key in ("up_to_date_as_of", "latest_issue_date", "latest_amended_on"):
                value = item.get(key)
                if value:
                    return str(value)
    return date.today().isoformat()


def part_xml_url(as_of: str) -> str:
    return f"{ECFR_VERSIONER}/full/{as_of}/title-{CFR_TITLE}.xml?part={CFR_PART}"


def download_part_xml(client: httpx.Client, as_of: str) -> tuple[str, str, bytes]:
    tried: list[str] = []
    start = date.fromisoformat(as_of)
    for offset in range(0, 21):
        candidate = (start - timedelta(days=offset)).isoformat()
        url = part_xml_url(candidate)
        tried.append(candidate)
        response = client.get(url)
        if response.status_code == 200 and response.content.strip():
            return candidate, url, response.content
        if response.status_code not in {400, 404}:
            response.raise_for_status()
    raise RuntimeError(
        f"Could not download 40 CFR Part {CFR_PART} XML. Dates tried: {', '.join(tried)}"
    )


def ancestor_subpart(element: etree._Element) -> str:
    current = element.getparent()
    while current is not None:
        tag = localname(current.tag).upper()
        type_attr = (current.get("TYPE") or "").upper()
        if tag == "DIV6" or type_attr == "SUBPART":
            head = current.find(".//{*}HEAD")
            if head is None:
                for child in current:
                    if localname(child.tag).upper() == "HEAD":
                        head = child
                        break
            if head is not None:
                return normalize_space("".join(head.itertext()))
            return normalize_space(current.get("N") or "")
        current = current.getparent()
    return ""


def block_text(element: etree._Element) -> str:
    pieces: list[str] = []

    def walk(node: etree._Element) -> None:
        tag = localname(node.tag).upper()
        if tag in SKIP_TAGS:
            return
        if node.text:
            pieces.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                pieces.append(child.tail)

    walk(element)
    return normalize_space("".join(pieces))


def section_paragraphs(section_el: etree._Element) -> list[str]:
    paragraphs: list[str] = []
    for child in section_el:
        tag = localname(child.tag).upper()
        if tag in {"HEAD"} or tag in SKIP_TAGS:
            continue
        if tag.startswith("DIV"):
            paragraphs.extend(section_paragraphs(child))
            continue
        text = block_text(child)
        if text:
            paragraphs.append(text)
    if not paragraphs:
        text = block_text(section_el)
        if text:
            paragraphs.append(text)
    return paragraphs


def section_url(section: str, kind: str) -> str:
    if kind == "appendix":
        slug = section.replace(" ", "-")
        return f"https://www.ecfr.gov/current/title-40/part-{CFR_PART}/appendix-{slug}"
    return f"https://www.ecfr.gov/current/title-40/part-{CFR_PART}/section-{section}"


def heading_and_id(element: etree._Element, kind: str) -> tuple[str, str, str]:
    n_attr = normalize_space(element.get("N") or "")
    head_el = None
    for child in element:
        if localname(child.tag).upper() == "HEAD":
            head_el = child
            break
    heading = normalize_space("".join(head_el.itertext())) if head_el is not None else ""

    if kind == "appendix":
        section = n_attr or heading or "appendix"
        citation = f"40 CFR Part {CFR_PART} {section}"
        return section, citation, heading or section

    match = re.search(r"1065\.\d+[a-z]?", f"{n_attr} {heading}", re.I)
    section = match.group(0) if match else (n_attr or "1065")
    citation = f"40 CFR {section}"
    return section, citation, heading or f"§ {section}"


def parse_sections(xml_bytes: bytes) -> list[SectionDoc]:
    root = etree.fromstring(xml_bytes)
    docs: list[SectionDoc] = []
    seen: set[str] = set()

    for element in root.iter():
        tag = localname(element.tag).upper()
        type_attr = (element.get("TYPE") or "").upper()
        if tag == "DIV8" or type_attr == "SECTION":
            kind = "section"
        elif tag == "DIV9" or type_attr == "APPENDIX":
            kind = "appendix"
        else:
            continue

        section, citation, heading = heading_and_id(element, kind)
        key = f"{kind}:{section}"
        if key in seen:
            continue
        seen.add(key)

        paragraphs = section_paragraphs(element)
        text = "\n\n".join(paragraphs).strip()
        if heading and text.lower().startswith(heading.lower()):
            text = text[len(heading) :].strip()
        if not text:
            continue

        docs.append(
            SectionDoc(
                section=section,
                citation=citation,
                heading=heading,
                subpart=ancestor_subpart(element),
                url=section_url(section, kind),
                text=text,
                kind=kind,
            )
        )

    docs.sort(key=lambda item: item.section)
    return docs


def chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for paragraph in paragraphs:
        pieces = [paragraph]
        if len(paragraph) > max_chars:
            pieces = [
                paragraph[i : i + max_chars]
                for i in range(0, len(paragraph), max_chars - overlap)
            ]
        for piece in pieces:
            if current and len(current) + 2 + len(piece) > max_chars:
                overflow = current[-overlap:] if overlap and len(current) > overlap else ""
                flush()
                current = overflow.strip()
            current = f"{current}\n\n{piece}".strip() if current else piece
    flush()
    return chunks or [text[:max_chars]]


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def ingest(*, as_of: str | None = None, refresh: bool = False) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": ECFR_USER_AGENT, "Accept": "application/xml, text/xml, */*"}

    with httpx.Client(headers=headers, timeout=120.0, follow_redirects=True) as client:
        target_date = as_of or latest_title_date(client)
        if refresh or not XML_PATH.exists():
            print(f"Downloading 40 CFR Part {CFR_PART} from eCFR (target date {target_date})...")
            used_date, source_url, xml_bytes = download_part_xml(client, target_date)
            XML_PATH.write_bytes(xml_bytes)
            print(f"Saved XML to {XML_PATH} ({len(xml_bytes):,} bytes), eCFR date {used_date}")
        else:
            xml_bytes = XML_PATH.read_bytes()
            if META_PATH.exists():
                prior = json.loads(META_PATH.read_text(encoding="utf-8"))
                used_date = prior.get("ecfr_date", target_date)
                source_url = prior.get("source_url", part_xml_url(used_date))
            else:
                used_date = target_date
                source_url = part_xml_url(used_date)
            print(f"Using cached XML at {XML_PATH}")

    print("Parsing sections...")
    sections = parse_sections(xml_bytes)
    if not sections:
        raise RuntimeError("No Part 1065 sections were parsed from the eCFR XML.")
    print(f"Parsed {len(sections)} sections/appendices")

    print("Building local vector index (first run downloads the embedding model)...")
    reset_index()

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, str | int]] = []
    section_summaries: list[dict[str, str]] = []

    for doc in sections:
        chunks = chunk_text(doc.text)
        section_summaries.append(
            {
                "section": doc.section,
                "citation": doc.citation,
                "heading": doc.heading,
                "subpart": doc.subpart,
                "url": doc.url,
                "kind": doc.kind,
            }
        )
        for index, chunk in enumerate(chunks):
            chunk_id = doc.section if index == 0 and len(chunks) == 1 else f"{doc.section}#{index + 1}"
            ids.append(chunk_id)
            documents.append(f"{doc.heading}\n\n{chunk}")
            metadatas.append(
                {
                    "section": doc.section,
                    "citation": doc.citation,
                    "heading": doc.heading,
                    "subpart": doc.subpart or "",
                    "url": doc.url,
                    "kind": doc.kind,
                    "chunk_index": index,
                    "ecfr_date": used_date,
                }
            )

    save_index(ids, documents, metadatas)
    print(f"  indexed {len(ids)} chunks")

    meta = {
        "ecfr_date": used_date,
        "source_url": source_url,
        "human_url": f"https://www.ecfr.gov/current/title-40/part-{CFR_PART}",
        "part": f"40 CFR Part {CFR_PART}",
        "title": "Engine-Testing Procedures",
        "section_count": len(sections),
        "chunk_count": len(ids),
        "embedding_model": "all-MiniLM-L6-v2",
        "xml_path": str(XML_PATH),
    }
    write_json(META_PATH, meta)
    write_json(SECTIONS_PATH, section_summaries)
    print(f"Index ready: {meta['section_count']} sections, {meta['chunk_count']} chunks")
    return meta


def load_meta() -> dict | None:
    if not META_PATH.exists():
        return None
    return json.loads(META_PATH.read_text(encoding="utf-8"))


def load_section_index() -> list[dict[str, str]]:
    if not SECTIONS_PATH.exists():
        return []
    return json.loads(SECTIONS_PATH.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download and index 40 CFR Part 1065 from the public eCFR API."
    )
    parser.add_argument("--date", help="eCFR point-in-time date (YYYY-MM-DD). Default: latest Title 40 date.")
    parser.add_argument("--refresh", action="store_true", help="Re-download XML even if a cached copy exists.")
    args = parser.parse_args()
    ingest(as_of=args.date, refresh=args.refresh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
