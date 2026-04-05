#!/usr/bin/env python3
"""
Vessel metadata extraction from document chunks.

When a vessel spec document (DOCX or PDF) is uploaded and its chunks saved to
the DB, this module scans those chunks for known section titles and extracts
structured vessel identity and spec fields.

Extracted fields (all optional, None when not found):
  imo_number, call_sign, flag_state, port_of_registry,
  year_built, gross_tonnage, dwat, loa

The extracted dict is used to auto-populate a Vessel record on first upload
and to update it on re-upload.  Users can override any field via the manual
edit form in the Vessel Library CMS page.
"""

import re
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Field extraction patterns
#
# Each pattern is a (field_name, regex_on_line) pair.
# Matching is done against individual body lines of the Registration / Tonnage
# / Dimensions chunks where the text is typically "Label   Value" or
# "Label: Value" format.
# ─────────────────────────────────────────────────────────────────────────────

_FIELD_PATTERNS: List[tuple] = [
    # IMO number — 7 digits, often prefixed "IMO"
    ("imo_number",       re.compile(r"IMO\s*(?:number|no\.?|#)?\s*[:\-]?\s*(\d{7})", re.I)),
    # Call sign — 4-6 uppercase alphanumeric
    ("call_sign",        re.compile(r"[Cc]all\s+sign\s*[:\-]?\s*([A-Z0-9]{4,6})")),
    # Flag state / flag country — stop at pipe separator (multi-field lines)
    ("flag_state",       re.compile(r"[Ff]lag\s+(?:state|country)\s*[:\-]?\s*([^|\n]+)")),
    # Port of registry — stop at pipe separator
    ("port_of_registry", re.compile(r"[Pp]ort\s+of\s+(?:registry|register)\s*[:\-]?\s*([^|\n]+)")),
    # Year / place of delivery — capture 4-digit year
    ("year_built",       re.compile(r"(?:year|date|delivered?)\s*(?:of\s+)?(?:built?|build|delivery|construction)?\s*[:\-]?\s*(?:[^0-9]*)(\d{4})", re.I)),
    # Gross tonnage
    ("gross_tonnage",    re.compile(r"[Gg]ross\s+tonnage\s*[:\-]?\s*([\d,\.]+)")),
    # DWAT / deadweight
    ("dwat",             re.compile(r"[Dd][Ww][Aa][Tt]\s*(?:\([^\)]*\))?\s*[:\-]?\s*([\d,\.\/]+\s*(?:mt|t)?)")),
    # Length over all
    ("loa",              re.compile(r"[Ll]ength\s+over\s+all\s*[:\-]?\s*([\d,\.]+\s*m?)")),
]

# Section titles we scan for field patterns (case-insensitive substring match)
_SPEC_SECTION_TITLES = {
    "registration", "tonnage", "dimensions", "general information",
    "vessel particulars", "ship's particulars", "particulars",
}


def _clean_value(v: str) -> str:
    """Strip trailing punctuation, whitespace, and normalise spaces."""
    v = v.strip().rstrip(".,;")
    v = re.sub(r"\s{2,}", " ", v)
    return v


def extract_vessel_metadata(chunks: List[Dict]) -> Dict[str, Optional[str]]:
    """
    Scan document chunks for vessel spec fields.

    Args:
        chunks: list of {title, body} dicts (from extractor output or DB rows)

    Returns:
        dict with keys matching Vessel model fields; values are strings or None.
    """
    result: Dict[str, Optional[str]] = {
        "imo_number":       None,
        "call_sign":        None,
        "flag_state":       None,
        "port_of_registry": None,
        "year_built":       None,
        "gross_tonnage":    None,
        "dwat":             None,
        "loa":              None,
    }

    for chunk in chunks:
        title = (chunk.get("title") or "").lower().strip()
        body  = chunk.get("body") or ""

        # Only scan spec-type sections
        if not any(kw in title for kw in _SPEC_SECTION_TITLES):
            continue

        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            for field, pattern in _FIELD_PATTERNS:
                if result[field] is not None:
                    continue   # already found
                m = pattern.search(line)
                if m:
                    val = _clean_value(m.group(1))
                    if val:
                        result[field] = val
                        logger.debug(f"[vessel_extractor] {field} = {val!r}")

    return result


def fill_vessel_metadata(vessel: "Vessel", chunks: List[Dict]) -> "Vessel":  # noqa: F821
    """
    Update an *existing* Vessel record with metadata extracted from document
    chunks.  Only fills fields that are currently NULL — manual edits made by
    the user in the Vessel Library are never overwritten.

    Call this after uploading a vessel spec sheet to auto-populate IMO,
    flag, port, year built, etc.  The caller is responsible for committing.
    """
    meta = extract_vessel_metadata(chunks)

    filled = []
    for field, value in meta.items():
        if value and getattr(vessel, field) is None:
            setattr(vessel, field, value)
            filled.append(field)

    if filled:
        logger.info(
            f"[vessel_extractor] Vessel '{vessel.name}' — auto-filled: {filled}"
        )
    return vessel


# Keep the old name as an alias so any external callers don't break.
def upsert_vessel_from_chunks(
    client_id: str,
    group_name: str,
    chunks: List[Dict],
) -> "Vessel | None":  # noqa: F821
    """Deprecated: use fill_vessel_metadata() with an explicit Vessel instance."""
    from models import db, Vessel

    vessel = Vessel.query.filter_by(client_id=client_id, name=group_name).first()
    if vessel is None:
        vessel = Vessel(client_id=client_id, name=group_name)
        db.session.add(vessel)

    return fill_vessel_metadata(vessel, chunks)
