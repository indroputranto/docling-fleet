#!/usr/bin/env python3
"""
Document text extraction for the upload pipeline.

Supports:
  .docx — python-docx; clause/heading-aware chunking
  .pdf  — pdfplumber; page + section-aware chunking
  .xlsx — openpyxl; one chunk per sheet

Each extractor returns a list of dicts:
  [{"title": str | None, "body": str}, ...]

Chunking strategy for maritime documents:
  1. Detect numbered clause headers (CLAUSE 1, 1., Clause 14, etc.)
  2. Each clause boundary → new chunk
  3. Fallback: fixed-size chunks of MAX_CHUNK_WORDS words
"""

import re
import logging
from typing import IO, List, Dict, Optional

logger = logging.getLogger(__name__)

MAX_CHUNK_WORDS = 600   # soft word limit per chunk before splitting


# ─────────────────────────────────────────────────────────────────────────────
# Clause detection
# ─────────────────────────────────────────────────────────────────────────────

# Matches common maritime clause patterns:
#   "CLAUSE 1", "Clause 14", "1.", "14 —", "PART II", "ANNEX A"
_CLAUSE_RE = re.compile(
    r"^\s*(?:"
    r"(?:CLAUSE|Clause)\s+\d+"          # CLAUSE 1 / Clause 14
    r"|(?:PART|Part)\s+(?:[IVX]+|\d+)"  # PART II / Part 3
    r"|(?:ANNEX|Annex|APPENDIX|Appendix)\s+\S+"  # ANNEX A
    r"|\d{1,3}\s*[.\-—]\s*\S"           # 1. / 14 — / 3 -
    r")",
    re.MULTILINE,
)


def _is_clause_header(text: str) -> bool:
    return bool(_CLAUSE_RE.match(text.strip()))


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
            is_heading = "heading" in style_name or _is_clause_header(text)

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
# PDF extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_pdf(file_obj: IO[bytes]) -> List[Dict]:
    """
    Extract chunks from a .pdf file using pdfplumber.

    Strategy:
    - Extract text page by page.
    - Detect clause headers within each page's text.
    - Group paragraphs into chunks at clause boundaries.
    - Fall back to page-per-chunk if no clause structure detected.
    """
    try:
        import pdfplumber
    except ImportError:
        raise ImportError(
            "pdfplumber is required for PDF extraction. "
            "Run: pip install pdfplumber"
        )

    full_text_lines: List[str] = []

    with pdfplumber.open(file_obj) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text_lines.extend(text.splitlines())

    if not full_text_lines:
        return [{"title": None, "body": ""}]

    # Walk lines and chunk at clause boundaries
    chunks: List[Dict] = []
    current_title: Optional[str] = None
    current_lines: List[str] = []

    def _flush():
        body = "\n".join(current_lines).strip()
        if body:
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

    if not chunks:
        # No clause structure — fall back to fixed chunks
        all_text = " ".join(full_text_lines)
        words = all_text.split()
        for i in range(0, len(words), MAX_CHUNK_WORDS):
            part = " ".join(words[i : i + MAX_CHUNK_WORDS])
            chunk_num = i // MAX_CHUNK_WORDS + 1
            chunks.append({"title": f"Section {chunk_num}", "body": part})

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
