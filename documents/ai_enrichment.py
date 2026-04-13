#!/usr/bin/env python3
"""
AI Enrichment — post-processing pass for extracted document chunks.

Takes the raw baseline output from extractor.py and sends it to an OpenAI
chat completion (gpt-4o-mini) which:
  - Assigns a concise, descriptive title to every chunk
  - Splits multi-topic chunks into individual, focused chunks
  - Preserves all original text verbatim — no paraphrasing, no omissions

Falls back to the raw extractor output on any failure (missing API key,
network error, malformed JSON response, empty result) so the upload pipeline
is never blocked by an enrichment error.

Usage:
    from documents.ai_enrichment import enrich_chunks
    enriched = enrich_chunks(raw_chunks, filename, vessel_name="MV ADRIATIC")
"""

import os
import re
import json
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

MODEL = "gpt-4o-mini"

# ─────────────────────────────────────────────────────────────────────────────
# Document type inference from filename
# ─────────────────────────────────────────────────────────────────────────────

_DOC_TYPE_HINTS = [
    (["recap", "fixture", "recapitulation"],              "Fixture Recap"),
    (["addendum", "addenda", "amendment", "rider"],       "Charter Party Addendum"),
    (["specification", "spec", "description", "particulars"], "Vessel Specification"),
    (["instruction", "circular", "notice", "advisory"],   "Operational Instructions"),
    (["agreement", "contract", "charter", "gencon", "bimco"], "Charter Party"),
    (["invoice", "statement", "account"],                 "Financial Document"),
    (["crew", "manning", "seafarer"],                     "Crew Document"),
    (["cargo", "manifest", "loading", "stowage"],         "Cargo Document"),
]


def _infer_doc_type(filename: str) -> str:
    """Infer a human-readable document type label from the filename."""
    name = filename.lower()
    for keywords, label in _DOC_TYPE_HINTS:
        if any(kw in name for kw in keywords):
            return label
    return "Maritime Document"


# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a maritime document analyst preparing content for a RAG (Retrieval-Augmented Generation) \
knowledge base used by ship operators, charterers, and fleet managers.

You will receive raw text chunks extracted from maritime documents — fixture recaps, addenda, \
vessel specifications, crew agreements, port instructions, and similar. Your job is to transform \
them into clean, well-structured knowledge chunks optimised for semantic search and retrieval.

Rules:
1. NEVER modify, paraphrase, or omit the original text. Preserve every word exactly as given.
2. ALWAYS assign a concise, descriptive title to every chunk (2–6 words, Title Case). The title \
must reflect the actual content — be specific, not generic \
(e.g. "Crew Nationality and Count", not "Crew Information").
3. SPLIT a chunk when it contains multiple distinct topics or clauses. Each clause beginning with \
a dash (-) in a fixture recap or addendum should generally become its own chunk if it covers a \
self-contained subject.
4. KEEP content together when it belongs to the same topic — do not over-fragment continuous prose \
or sub-points that only make sense as a group.
5. For spec sheets and vessel descriptions, group related technical parameters \
(e.g. all dimensions together, all tonnage figures together).
6. Return your answer as a JSON object with a single key "chunks" whose value is an array of objects, \
each with "title" (string) and "content" (string) fields. \
No explanation, no commentary, no markdown — pure JSON only.
7. CRITICAL — STRIKETHROUGH MARKERS: Text wrapped in ~~double tildes~~ (e.g. ~~deleted clause~~) \
represents struck-out or deleted text in a negotiated contract. These markers carry legal significance \
and MUST be reproduced exactly as-is in your output. Never remove, summarise, or alter text inside \
~~…~~ markers. A chunk whose entire body is struck through must still appear in full with every \
~~…~~ wrapper intact.

Example output shape:
{"chunks": [{"title": "Vessel Name and Type", "content": "..."}, ...]}
"""


def _build_user_prompt(
    raw_chunks: List[Dict],
    filename: str,
    doc_type: str,
    vessel_name: str,
) -> str:
    payload = json.dumps(
        [{"title": c.get("title") or "", "content": c["body"]} for c in raw_chunks],
        ensure_ascii=False,
        indent=2,
    )
    return (
        f"Document type: {doc_type}\n"
        f"Source file: {filename}\n"
        f"Vessel (if known): {vessel_name}\n\n"
        f"Below are the raw extracted chunks from this document. "
        f"Process them according to your instructions and return the enriched JSON.\n\n"
        f"---\n{payload}\n---"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Response parsing
# ─────────────────────────────────────────────────────────────────────────────

def _parse_response(text: str) -> Optional[List[Dict]]:
    """
    Parse the model's text response into a list of {"title", "body"} dicts.

    Handles:
    - Clean JSON object: {"chunks": [...]}
    - Markdown-fenced JSON (```json ... ```)
    - Bare JSON array (fallback)
    """
    # Strip markdown fences if present
    text = text.strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"[enrichment] JSON parse error: {e}")
        return None

    # Expect {"chunks": [...]}
    if isinstance(parsed, dict) and "chunks" in parsed:
        items = parsed["chunks"]
    elif isinstance(parsed, list):
        items = parsed
    else:
        logger.error(f"[enrichment] Unexpected JSON shape: {type(parsed)}")
        return None

    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title   = str(item.get("title") or "").strip()
        content = str(item.get("content") or item.get("body") or "").strip()
        if content:
            result.append({"title": title, "body": content})

    return result if result else None


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def enrich_chunks(
    raw_chunks: List[Dict],
    filename: str,
    vessel_name: Optional[str] = None,
    document_category: Optional[str] = None,
) -> List[Dict]:
    """
    Send raw extractor chunks through an OpenAI enrichment pass.

    Args:
        raw_chunks:          Output of extractor.extract() — list of {"title", "body"} dicts.
        filename:            Original filename (fallback for doc type inference).
        vessel_name:         Vessel name from the CMS vessel record, if available.
        document_category:   Explicit category key from the Vessel Dossier UI
                             (e.g. "fixture_recap", "charter_party"). When provided
                             this is used directly instead of inferring from filename.

    Returns:
        Enriched list of {"title": str, "body": str} dicts.
        Falls back silently to raw_chunks on any failure.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("[enrichment] OPENAI_API_KEY not set — skipping AI enrichment")
        return raw_chunks

    if not raw_chunks:
        return raw_chunks

    # Use explicit category label if provided; otherwise infer from filename
    if document_category:
        # Map the slug key to a human-readable label for the prompt
        _CATEGORY_LABELS = {
            "vessel_specifications": "Vessel Specification",
            "addendum":              "Charter Party Addendum",
            "fixture_recap":         "Fixture Recap",
            "charter_party":         "Charter Party",
            "delivery_details":      "Delivery Details",
            "speed_consumption":     "Speed & Consumption",
            "inventory":             "Inventory",
            "lifting_equipment":     "Lifting Equipment",
            "hseq_documents":        "HSEQ Documents",
            "vessel_owners_details": "Vessel & Owners Details",
        }
        doc_type = _CATEGORY_LABELS.get(document_category, document_category.replace("_", " ").title())
    else:
        doc_type = _infer_doc_type(filename)

    vessel_str  = vessel_name or "Unknown"

    user_prompt = _build_user_prompt(raw_chunks, filename, doc_type, vessel_str)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        logger.info(
            f"[enrichment] Calling {MODEL} for '{filename}' | "
            f"doc_type='{doc_type}' | vessel='{vessel_str}' | "
            f"{len(raw_chunks)} raw chunks"
        )

        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )

        raw_text = response.choices[0].message.content or ""
        enriched = _parse_response(raw_text)

        if not enriched:
            logger.warning(
                f"[enrichment] Empty or unparseable response for '{filename}' — "
                f"falling back to raw chunks"
            )
            return raw_chunks

        # Safety check: if the model dropped more than 30% of the source words
        # (common when it incorrectly strips ~~strikethrough~~ content), fall back
        # to raw chunks so no legal text is silently lost.
        raw_words      = sum(len(c["body"].split()) for c in raw_chunks)
        enriched_words = sum(len(c["body"].split()) for c in enriched)
        if raw_words > 0 and enriched_words < raw_words * 0.70:
            logger.warning(
                f"[enrichment] '{filename}': word count dropped "
                f"{enriched_words / raw_words:.0%} "
                f"({raw_words} → {enriched_words} words) — "
                f"falling back to raw chunks to preserve content"
            )
            return raw_chunks

        logger.info(
            f"[enrichment] '{filename}' enriched: "
            f"{len(raw_chunks)} raw → {len(enriched)} chunks"
        )
        return enriched

    except Exception as e:
        logger.error(
            f"[enrichment] Failed for '{filename}': {e} — falling back to raw chunks",
            exc_info=True,
        )
        return raw_chunks
