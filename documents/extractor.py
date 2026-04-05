#!/usr/bin/env python3
"""
Document text extraction for the upload pipeline.

Supports:
  .docx — python-docx; clause/heading-aware chunking
  .pdf  — PyMuPDF (font-aware) + pdfplumber fallback; section-aware chunking
  .xlsx — openpyxl; one chunk per sheet

Each extractor returns a list of dicts:
  [{"title": str | None, "body": str}, ...]

Chunking strategy for maritime documents:
  PDFs:  1. Use PyMuPDF get_text("dict") to read font metadata
             — lines visually larger or bold → section header boundary
             — regex-based clause detection as secondary signal
         2. Filter "junk" chunks (cargo diagrams, slot-plan labels)
         3. Fallback: pdfplumber + regex-only chunking
         4. Last resort: fixed-size chunks of MAX_CHUNK_WORDS words
  DOCX:  Heading styles + clause regex
"""

import re
import unicodedata
import logging
from typing import IO, List, Dict, Optional

logger = logging.getLogger(__name__)

MAX_CHUNK_WORDS = 600   # soft word limit per chunk before splitting


# ─────────────────────────────────────────────────────────────────────────────
# PDF text cleaning — ligature & encoding artifact repair
# ─────────────────────────────────────────────────────────────────────────────

# BIMCO SmartCon and similar commercial charter-party PDFs embed proprietary
# fonts whose ToUnicode CMap tables incorrectly map common ligature glyphs to
# Latin Extended codepoints instead of the correct ASCII sequences.
#
# Observed mappings (confirmed on BIMCO SmartCon GENCON 2022):
#   U+019F  Ɵ  → "ti"   (LATIN CAPITAL LETTER O WITH MIDDLE TILDE used for ti-ligature)
#   U+014C  Ō  → "ft"   (LATIN CAPITAL LETTER O WITH MACRON used for ft-ligature)
#   U+01A9  Ʃ  → "tt"   (LATIN CAPITAL LETTER ESH used for tt-ligature)
#
# Add further entries here if new artifacts are discovered in other PDF sources.
_BIMCO_LIGATURE_MAP: dict = {
    '\u019F': 'ti',   # Ɵ → ti
    '\u014C': 'ft',   # Ō → ft
    '\u01A9': 'tt',   # Ʃ → tt
}

# Build a str.translate()-compatible table (codepoint → replacement string)
_PDF_TRANSLATE_TABLE = str.maketrans(_BIMCO_LIGATURE_MAP)


def _clean_pdf_text(text: str) -> str:
    """
    Repair encoding artifacts from PDFs with broken ToUnicode CMap entries.

    Two-pass strategy:
      1. NFKC normalization — decomposes standard Unicode ligatures
         (ﬁ→fi, ﬂ→fl, ﬀ→ff, ﬃ→ffi, ﬄ→ffl, ﬅ/ﬆ→st) into ASCII pairs.
      2. Character substitution — corrects BIMCO SmartCon font mis-encodings
         where ligature glyphs are mapped to wrong Latin Extended codepoints.
    """
    # Pass 1: standard Unicode ligatures (U+FB00–U+FB06)
    text = unicodedata.normalize('NFKC', text)
    # Pass 2: BIMCO-specific wrong-codepoint mappings
    return text.translate(_PDF_TRANSLATE_TABLE)


# ─────────────────────────────────────────────────────────────────────────────
# Clause / section header detection  (regex fallback for contract PDFs)
# ─────────────────────────────────────────────────────────────────────────────

# Matches common maritime clause patterns:
#   "CLAUSE 1", "Clause 14", "1. DEFINITIONS", "14 — LIEN", "PART II", "ANNEX A"
#
# NOTE: The numbered pattern requires a SPACE then a LETTER after the separator
# so that decimal numbers (6.438, 16.0m, 3.5 mt/m2) are NOT mistaken for
# clause headers.  "1." alone (no following word) is intentionally excluded
# because in vessel description PDFs single numbers ending with "." are common
# data values, not structural headers.
_CLAUSE_RE = re.compile(
    r"^\s*(?:"
    r"(?:CLAUSE|Clause)\s+\d+"              # CLAUSE 1 / Clause 14
    r"|(?:PART|Part)\s+(?:[IVX]+|\d+)"      # PART II / Part 3
    r"|(?:ANNEX|Annex|APPENDIX|Appendix)\s+\S+"  # ANNEX A / Appendix B
    r"|\d{1,3}\s*[.\-—]\s+[A-Za-z]"        # "1. Definitions" / "14 — Lien"
    r")",                                   # NOTE: space required before letter
    re.MULTILINE,
)


def _is_clause_header(text: str) -> bool:
    return bool(_CLAUSE_RE.match(text.strip()))


def _is_docx_subsection_label(text: str) -> bool:
    """
    Detect paragraph-style subsection labels used in vessel-description DOCX
    files where section headers carry no heading style but are identifiable
    because they end with ':' and have no value after the colon.

    Examples that match:
        "General Information:"  "Tonnage:"  "Propulsion & Maneuvering:"
        "Hold and Hatch Sizes:"  "RoRo Features:"  "Container Capacity:"

    Examples that do NOT match:
        "Call Sign: PEVT"          (has value after ':')
        "DWAT (closed/open): 4540" (has value after ':')
        "Grain fitted"             (does not end with ':')
        "conditions:"              (starts lowercase → likely a sentence fragment)
        "This is a very long sentence that ends with a colon:"  (>80 chars)
    """
    stripped = text.strip()
    if not stripped or not stripped.endswith(':'):
        return False
    # Must be short — real labels are terse
    if len(stripped) > 80:
        return False
    # Must start with an uppercase letter (section titles, not sentence fragments)
    if stripped[0].islower():
        return False
    # Must not contain ': ' followed by content (that would be a key-value pair)
    if ': ' in stripped[:-1]:   # ignore the final ':'
        return False
    return True


def _split_into_chunks(segments: List[Dict]) -> List[Dict]:
    """
    Given a list of {title, body} where body may be very long,
    split any chunk exceeding MAX_CHUNK_WORDS into sub-chunks.
    """
    result = []
    for seg in segments:
        words = seg["body"].split()
        if len(words) <= MAX_CHUNK_WORDS:
            result.append(seg)
        else:
            # Split into sub-chunks; carry the original title on the first only
            parts = [
                words[i : i + MAX_CHUNK_WORDS]
                for i in range(0, len(words), MAX_CHUNK_WORDS)
            ]
            for idx, part in enumerate(parts):
                suffix = f" (part {idx + 1})" if len(parts) > 1 else ""
                result.append({
                    "title": (seg["title"] or "") + suffix if idx == 0 else f"{seg['title'] or 'Continued'} (part {idx + 1})",
                    "body":  " ".join(part),
                })
    return result


# ─────────────────────────────────────────────────────────────────────────────
# DOCX extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_docx(file_obj: IO[bytes]) -> List[Dict]:
    """
    Extract chunks from a .docx file.

    Strategy:
    - Iterate paragraphs in document order.
    - A paragraph is a chunk boundary if it uses a Heading style OR
      matches the clause header pattern.
    - Table cells are appended as plain text after the paragraph that
      precedes them.
    """
    try:
        import docx as python_docx
    except ImportError:
        from docx import Document as _D
        python_docx = type("_m", (), {"Document": _D})()

    from docx import Document as DocxDocument
    doc = DocxDocument(file_obj)

    chunks: List[Dict] = []
    current_title: Optional[str] = None
    current_lines: List[str] = []

    def _flush():
        body = "\n".join(current_lines).strip()
        if body:
            chunks.append({"title": current_title, "body": body})

    for block in doc.element.body:
        tag = block.tag.split("}")[-1]  # "p" or "tbl"

        if tag == "p":
            from docx.oxml.ns import qn
            from docx.text.paragraph import Paragraph
            para = Paragraph(block, doc)
            text = para.text.strip()
            if not text:
                continue

            style_name = (para.style.name or "").lower()
            is_heading = (
                "heading" in style_name
                or _is_clause_header(text)
                or _is_docx_subsection_label(text)
            )

            if is_heading:
                _flush()
                current_title = text
                current_lines = []
            else:
                current_lines.append(text)

        elif tag == "tbl":
            # Extract table as markdown-ish text
            from docx.table import Table
            try:
                tbl = Table(block, doc)
                rows = []
                for row in tbl.rows:
                    cells = [c.text.strip() for c in row.cells]
                    rows.append(" | ".join(cells))
                if rows:
                    current_lines.append("\n".join(rows))
            except Exception:
                pass

    _flush()

    if not chunks:
        # Fallback: whole document as one chunk
        full_text = "\n".join(
            p.text.strip() for p in doc.paragraphs if p.text.strip()
        )
        chunks = [{"title": None, "body": full_text}]

    return _split_into_chunks(chunks)


# ─────────────────────────────────────────────────────────────────────────────
# PDF extraction — helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_junk_body(body: str) -> bool:
    """
    Return True when the chunk body is extraction noise rather than useful text.

    Catches:
    - Cargo slot diagrams / hold plans: position labels like A B C … and
      bay/row numbers 1 2 3 … extracted as individual lines from graphical
      vector drawings.  Heuristic: >60 % of non-empty lines are ≤2 chars.
    - Trivially short chunks (fewer than 5 content words total).
    """
    lines = [l.strip() for l in body.split("\n") if l.strip()]
    if not lines:
        return True
    total_words = sum(len(l.split()) for l in lines)
    if total_words < 5:
        return True
    short = sum(1 for l in lines if len(l) <= 2)
    return short / len(lines) > 0.60


def _chunk_lines(
    lines_meta: List[Dict],
    body_size: float,
) -> List[Dict]:
    """
    Walk a list of {text, size, is_bold} line dicts and group them into
    {title, body} chunks using font metadata + clause-regex signals.

    Header signals (any one triggers a new chunk):
      1. Font size >= body_size * 1.12  (visually larger than body text)
      2. All spans bold + line is short (<120 chars) + doesn't start with digit
         (short bold lines are captions/labels; long bold lines are body prose)
      3. Line matches _CLAUSE_RE (numbered clauses in contract PDFs)
    """
    chunks: List[Dict] = []
    current_title: Optional[str] = None
    current_lines: List[str] = []

    header_threshold = body_size * 1.12

    def _flush():
        body = "\n".join(current_lines).strip()
        if body and not _is_junk_body(body):
            chunks.append({"title": current_title, "body": body})

    for lm in lines_meta:
        text = lm["text"]
        size = lm["size"]
        is_bold = lm["is_bold"]

        is_header = (
            size >= header_threshold
            or (is_bold and len(text) < 120 and not text[0].isdigit())
            or _is_clause_header(text)
        )

        if is_header:
            _flush()
            current_title = text
            current_lines = []
        else:
            current_lines.append(text)

    _flush()
    return chunks


def _fallback_fixed_chunks(text_lines: List[str]) -> List[Dict]:
    """Last-resort: split plain text into fixed-size word chunks."""
    words = " ".join(text_lines).split()
    chunks = []
    for i in range(0, len(words), MAX_CHUNK_WORDS):
        part = " ".join(words[i : i + MAX_CHUNK_WORDS])
        chunk_num = i // MAX_CHUNK_WORDS + 1
        chunks.append({"title": f"Section {chunk_num}", "body": part})
    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# PDF extraction — PyMuPDF (primary)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_pdf_fitz(raw_bytes: bytes) -> List[Dict]:
    """
    Extract structured chunks from a PDF using PyMuPDF font metadata.

    Uses get_text("dict") to read every span's font size and bold flag.
    This allows accurate detection of section headers in both:
    - Vessel description / spec sheets  (bold or larger section labels)
    - Charter party contracts           (numbered clauses, often bold)

    Falls back to fixed-size chunking if no headers are detected.
    """
    import fitz
    from collections import Counter

    lines_meta: List[Dict] = []

    with fitz.open(stream=raw_bytes, filetype="pdf") as pdf:
        for page in pdf:
            for block in page.get_text("dict")["blocks"]:
                if block.get("type") != 0:   # skip image blocks
                    continue
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    if not spans:
                        continue

                    # Combine all spans on this line into one string
                    combined = _clean_pdf_text(
                        "".join(s["text"] for s in spans)
                    ).strip()
                    if not combined:
                        continue

                    # Font metrics — use spans that have actual text content
                    text_spans = [s for s in spans if s["text"].strip()]
                    if not text_spans:
                        continue
                    max_size = max(s["size"] for s in text_spans)
                    # Bold = flag bit 4 (value 16) set on ALL text spans
                    is_bold = all(bool(s["flags"] & 16) for s in text_spans)

                    lines_meta.append({
                        "text": combined,
                        "size": max_size,
                        "is_bold": is_bold,
                    })

    if not lines_meta:
        return []

    # Determine body font size: most common rounded size across all lines
    size_counts = Counter(round(lm["size"] * 2) / 2 for lm in lines_meta)
    body_size = size_counts.most_common(1)[0][0]

    chunks = _chunk_lines(lines_meta, body_size)

    if not chunks:
        # No detectable structure — fall back to fixed chunks
        plain_lines = [lm["text"] for lm in lines_meta]
        chunks = _fallback_fixed_chunks(plain_lines)

    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# PDF extraction — pdfplumber (fallback, no font metadata)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_pdf_pdfplumber(raw_bytes: bytes) -> List[Dict]:
    """
    Extract chunks from PDF via pdfplumber (clause-regex only, no font info).
    Used when PyMuPDF is not installed.
    """
    import io, pdfplumber

    full_text_lines: List[str] = []
    with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                text = _clean_pdf_text(text)
                full_text_lines.extend(text.splitlines())

    if not full_text_lines:
        return []

    chunks: List[Dict] = []
    current_title: Optional[str] = None
    current_lines: List[str] = []

    def _flush():
        body = "\n".join(current_lines).strip()
        if body and not _is_junk_body(body):
            chunks.append({"title": current_title, "body": body})

    for line in full_text_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _is_clause_header(stripped):
            _flush()
            current_title = stripped
            current_lines = []
        else:
            current_lines.append(stripped)

    _flush()

    return chunks or _fallback_fixed_chunks(full_text_lines)


# ─────────────────────────────────────────────────────────────────────────────
# PDF extraction — public entry point
# ─────────────────────────────────────────────────────────────────────────────

def extract_pdf(file_obj: IO[bytes]) -> List[Dict]:
    """
    Extract chunks from a .pdf file.

    Dispatch order:
      1. PyMuPDF  — font-aware header detection (bold / larger text)
      2. pdfplumber — clause-regex only (if PyMuPDF not installed)
      3. Fixed-size word chunks (if neither library produces structure)
    """
    raw_bytes = file_obj.read()

    try:
        chunks = _extract_pdf_fitz(raw_bytes)
    except ImportError:
        try:
            chunks = _extract_pdf_pdfplumber(raw_bytes)
        except ImportError:
            raise ImportError(
                "No PDF library found. Run: pip install PyMuPDF "
                "(or pip install pdfplumber as fallback)"
            )

    if not chunks:
        return [{"title": None, "body": ""}]

    return _split_into_chunks(chunks)


# ─────────────────────────────────────────────────────────────────────────────
# XLSX extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_xlsx(file_obj: IO[bytes]) -> List[Dict]:
    """
    Extract chunks from an .xlsx file using openpyxl.

    Strategy:
    - One chunk per worksheet.
    - Each row is rendered as "Col Header: value" pairs.
    - If the first row appears to be headers, use them as column names.
    """
    import openpyxl

    wb = openpyxl.load_workbook(file_obj, data_only=True)
    chunks: List[Dict] = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue

        lines: List[str] = []

        # Detect header row: first row where most cells are non-numeric strings
        first_row = rows[0]
        has_headers = all(
            isinstance(c, str) or c is None for c in first_row
        )

        if has_headers and len(rows) > 1:
            headers = [str(c).strip() if c is not None else f"Col{i+1}"
                       for i, c in enumerate(first_row)]
            data_rows = rows[1:]
        else:
            col_count = max(len(r) for r in rows)
            headers = [f"Column {i+1}" for i in range(col_count)]
            data_rows = rows

        for row in data_rows:
            if all(c is None for c in row):
                continue
            pairs = []
            for h, v in zip(headers, row):
                if v is not None:
                    pairs.append(f"{h}: {v}")
            if pairs:
                lines.append("  |  ".join(pairs))

        body = "\n".join(lines)
        if body.strip():
            chunks.append({"title": sheet_name, "body": body})

    if not chunks:
        chunks = [{"title": None, "body": "No data found in spreadsheet."}]

    return _split_into_chunks(chunks)


# ─────────────────────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────────────────────

def extract(file_obj: IO[bytes], filename: str) -> List[Dict]:
    """
    Route to the correct extractor based on file extension.

    Returns:
        List of {"title": str|None, "body": str} dicts, ready for DB insertion.
    Raises:
        ValueError: unsupported file type
        ImportError: missing optional dependency (pdfplumber)
    """
    ext = filename.rsplit(".", 1)[-1].lower()
    logger.info(f"[extractor] Extracting {filename} (type={ext})")

    if ext == "docx":
        return extract_docx(file_obj)
    elif ext == "pdf":
        return extract_pdf(file_obj)
    elif ext in ("xlsx", "xls"):
        return extract_xlsx(file_obj)
    else:
        raise ValueError(f"Unsupported file type: .{ext}")
