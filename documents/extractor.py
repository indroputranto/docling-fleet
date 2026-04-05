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
    if total_words < 3:
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


def _detect_column_split(elements: List[Dict], page_width: float) -> Optional[float]:
    """
    Detect the x-coordinate of a vertical column gap in a two-column PDF layout.

    Algorithm (bin-density):
      1. Divide the page width into 20pt bins and count elements per bin.
      2. Restrict search to the central 15–80% of page width (skip margins).
      3. Find the longest contiguous run of "sparse" bins (≤2% of total elements).
      4. A run of ≥3 bins (≥60pt) qualifies as a column gap.
      5. Return the midpoint of that gap; None if no gap found.

    Robust to isolated cross-column titles (e.g. "Ship's particulars" centred
    over the gap) because a single element is counted as sparse (≤2%).
    """
    if not elements:
        return None

    BIN = 20.0
    n_bins = int(page_width / BIN) + 2
    counts = [0] * n_bins
    for e in elements:
        b = int(e["x"] / BIN)
        if 0 <= b < n_bins:
            counts[b] += 1

    lo_b = int(page_width * 0.15 / BIN)
    hi_b = int(page_width * 0.80 / BIN)
    sparse_threshold = max(1, len(elements) * 0.02)

    best_len, best_start = 0, None
    cur_len, cur_start = 0, None

    for i in range(lo_b, hi_b + 1):
        if counts[i] <= sparse_threshold:
            cur_len += 1
            if cur_start is None:
                cur_start = i
            if cur_len > best_len:
                best_len, best_start = cur_len, cur_start
        else:
            cur_len, cur_start = 0, None

    # Require at least 3 bins = 60pt gap
    if best_len >= 3 and best_start is not None:
        gap_left = best_start * BIN
        gap_right = (best_start + best_len) * BIN
        return (gap_left + gap_right) / 2.0

    return None


def _column_to_chunks(elements: List[Dict], snap: float = 3.0) -> List[Dict]:
    """
    Convert a list of positioned text elements into {title, body} chunks.

    Works for both single-column and per-column element sets.

    Steps:
      1. Sort elements by (y, x) — top-to-bottom, left-to-right.
      2. Group elements whose y positions are within `snap` pt into the same
         visual row (handles sub-pixel baseline differences).
      3. Determine body font size (modal rounded size).
      4. For each row, decide if it is a section header:
           - All spans bold  AND  line < 120 chars  AND  starts with uppercase
             letter  →  header (handles OCEAN7-style spec sheets)
           - Max font size ≥ body_size * 1.12  →  header (font-size-based)
           - Line matches clause regex  →  header (BIMCO/contract PDFs)
      5. Flush completed chunks, filter junk bodies.
    """
    from collections import Counter

    if not elements:
        return []

    # Step 1 & 2: sort and group into visual rows
    sorted_els = sorted(elements, key=lambda e: (e["y"], e["x"]))
    rows: List[List[Dict]] = []
    current_row: List[Dict] = []
    row_y: Optional[float] = None

    for el in sorted_els:
        if row_y is None or abs(el["y"] - row_y) <= snap:
            current_row.append(el)
            if row_y is None:
                row_y = el["y"]
        else:
            if current_row:
                rows.append(current_row)
            current_row = [el]
            row_y = el["y"]
    if current_row:
        rows.append(current_row)

    # Step 3: modal body font size
    size_counts: Counter = Counter(
        round(e["size"] * 2) / 2 for e in elements
    )
    body_size = size_counts.most_common(1)[0][0] if size_counts else 10.0
    header_threshold = body_size * 1.12

    # Step 4 & 5: build chunks
    chunks: List[Dict] = []
    current_title: Optional[str] = None
    current_lines: List[str] = []

    def _flush_col():
        body = "\n".join(current_lines).strip()
        if body and not _is_junk_body(body):
            chunks.append({"title": current_title, "body": body})

    for row in rows:
        txt = _clean_pdf_text(" ".join(e["text"] for e in row)).strip()
        if not txt:
            continue

        is_bold = all(e["is_bold"] for e in row)
        max_size = max(e["size"] for e in row)
        first_char = txt[0] if txt else ""

        is_header = (
            (is_bold and len(txt) < 120 and first_char.isalpha() and first_char.isupper())
            or max_size >= header_threshold
            or _is_clause_header(txt)
        )

        if is_header:
            _flush_col()
            current_title = txt
            current_lines = []
        else:
            current_lines.append(txt)

    _flush_col()
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

    Strategy:
    - Read every page using get_text("dict") to capture per-span font size,
      bold flag, and bounding-box coordinates.
    - Filter out tiny text (< 7pt) which is typically cargo diagram labels or
      other graphical annotation noise.
    - For each page, use bin-density analysis to detect two-column layouts.
      If a column gap is found, split elements into left and right columns and
      process each independently so that row-order within each column is
      preserved correctly.
    - Within each column (or full-page for single-column layouts), group
      co-baseline elements into visual rows, then apply bold/size/clause-regex
      header detection to build {title, body} chunks.
    - Suitable for both vessel spec sheets (bold section labels, same font
      size) and charter party contracts (numbered clauses, larger headers).
    """
    import fitz

    MIN_FONT = 7.0   # pt — skip cargo diagram / slot-plan label text

    all_chunks: List[Dict] = []
    all_plain_lines: List[str] = []   # fallback accumulator

    with fitz.open(stream=raw_bytes, filetype="pdf") as pdf:
        for page in pdf:
            page_width = page.rect.width
            elements: List[Dict] = []

            for block in page.get_text("dict")["blocks"]:
                if block.get("type") != 0:   # skip image blocks
                    continue
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    text_spans = [s for s in spans if s["text"].strip()]
                    if not text_spans:
                        continue

                    # Skip sub-threshold font sizes (noise / diagram labels)
                    max_size = max(s["size"] for s in text_spans)
                    if max_size < MIN_FONT:
                        continue

                    combined = _clean_pdf_text(
                        " ".join(s["text"] for s in text_spans)
                    ).strip()
                    if not combined:
                        continue

                    # Bold = flag bit 4 (value 16) set on ALL content spans
                    is_bold = all(bool(s["flags"] & 16) for s in text_spans)
                    # x: leftmost edge of the line's bounding box
                    x_pos = min(s["bbox"][0] for s in text_spans)
                    # y: top edge — consistent anchor for row grouping
                    y_pos = min(s["bbox"][1] for s in text_spans)

                    elements.append({
                        "text": combined,
                        "size": max_size,
                        "is_bold": is_bold,
                        "x": x_pos,
                        "y": y_pos,
                    })
                    all_plain_lines.append(combined)

            if not elements:
                continue

            col_split = _detect_column_split(elements, page_width)

            if col_split is not None:
                left_els  = [e for e in elements if e["x"] <  col_split]
                right_els = [e for e in elements if e["x"] >= col_split]
                if left_els:
                    all_chunks.extend(_column_to_chunks(left_els))
                if right_els:
                    all_chunks.extend(_column_to_chunks(right_els))
            else:
                all_chunks.extend(_column_to_chunks(elements))

    if not all_chunks:
        all_chunks = _fallback_fixed_chunks(all_plain_lines)

    return all_chunks


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
