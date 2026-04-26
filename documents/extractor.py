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

MAX_CHUNK_WORDS = 1500  # soft word limit per chunk before splitting
                        # Raised from 600: charter-party clauses can be 800–1200 words
                        # and must stay unified for quality Pinecone indexing.
                        # 1500 words ≈ ~2000 tokens, well within ada-002's 8191-token limit.


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

    Three-pass strategy:
      1. NFKC normalization — decomposes standard Unicode ligatures
         (ﬁ→fi, ﬂ→fl, ﬀ→ff, ﬃ→ffi, ﬄ→ffl, ﬅ/ﬆ→st) into ASCII pairs.
      2. Character substitution — corrects BIMCO SmartCon font mis-encodings
         where ligature glyphs are mapped to wrong Latin Extended codepoints.
      3. Ligature space-join — fixes cases where PyMuPDF renders a ligature
         glyph as two separate span fragments with a space between them.
         Observed in BIMCO SmartCon PDFs:
           "Dura ti on"  → "Duration"
           "condi ti on" → "condition"
           "par ti es"   → "parties"
           "cer ti fy"   → "certify"
           "no ti ce"    → "notice"
           "addi ti onal"→ "additional"
         Also handles "ft" and "tt" ligature splits ("draf t ing" → "drafting").
    """
    # Pass 1: standard Unicode ligatures (U+FB00–U+FB06)
    text = unicodedata.normalize('NFKC', text)
    # Pass 2: BIMCO-specific wrong-codepoint mappings
    text = text.translate(_PDF_TRANSLATE_TABLE)
    # Pass 3a: join word fragments split by a FLOATING ligature substring
    # Handles ligature as middle fragment:  "no ti ce" → "notice"
    # Also handles ligature as a standalone word at token start:
    #   "  ti me" → "time",  "fi nal" → "final"
    text = re.sub(r'(\w) (ti|ft|tt|ffi|ffl|fi|fl) (\w)', r'\1\2\3', text)
    # Standalone ligature fragment followed by a word (word-boundary anchor)
    text = re.sub(r'\b(ti|tt|ffi|ffl)\s+([a-z])', r'\1\2', text)

    # Pass 3b: join PREFIX + merged-suffix splits where fitz absorbed the
    # ligature into the suffix but left a space before it.
    # e.g. "Dura tion" → "Duration", "Instruc tions" → "Instructions",
    #      "Communica tions" → "Communications", "obliga tion" → "obligation"
    # The suffix list covers the most common 'ti'-ligature-affected endings.
    _TI_SUFFIXES = (
        r"tions?|tives?|tively|tings?|tional|tionally|"
        r"tified|tifying|tifiable|tification|tifications|"
        r"tities|tity|tion\b"
    )
    text = re.sub(
        rf'([A-Za-z]{{2,}}) ({_TI_SUFFIXES})',
        lambda m: m.group(1) + m.group(2),
        text,
    )
    return text


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

# Matches a left-margin line number that was merged into a content span by
# PyMuPDF when the number and content happen to be in the same text line object.
# Pattern: 1–3 digits at the very start, followed by whitespace and a letter.
# Upper bound of 3 digits (≤999) safely avoids stripping genuine numeric content
# like "12580 MT" (5 digits) or "9611 GT" (4 digits).
# The positive lookahead (?=[A-Za-z]) ensures we never strip a number that is
# itself the start of the content (e.g. "1.5 knots" would NOT match because
# the next char after "1" is "." not a space; "12580 metric" won't match —
# 5 digits exceeds \d{1,3}).
_LN_PREFIX_RE = re.compile(r'^\d{1,3}\s+(?=[A-Za-z])')


def _is_clause_header(text: str) -> bool:
    return bool(_CLAUSE_RE.match(text.strip()))


# ─────────────────────────────────────────────────────────────────────────────
# DOCX run-level formatting — strikethrough preservation
# ─────────────────────────────────────────────────────────────────────────────

def _paragraph_text_with_strikethrough(para) -> str:
    """
    Return the text of a python-docx Paragraph with strikethrough runs
    wrapped in ~~double-tilde~~ markdown notation.

    Checks both <w:strike> (single) and <w:dstrike> (double) elements on each
    run's rPr, matching the behaviour of process_vessel_new.py.  Plain runs
    are returned as-is so the result is drop-in compatible with para.text.
    """
    W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

    result_parts: List[str] = []
    strike_buf:   List[str] = []
    in_strike = False

    for run in para.runs:
        text = run.text
        if not text:
            continue

        is_struck = False
        rPr = getattr(run._element, "rPr", None)
        if rPr is not None:
            is_struck = (
                rPr.find(f"{{{W_NS}}}strike")  is not None or
                rPr.find(f"{{{W_NS}}}dstrike") is not None
            )

        if is_struck:
            if not in_strike:
                in_strike = True
            strike_buf.append(text)
        else:
            if in_strike:
                result_parts.append(f"~~{''.join(strike_buf)}~~")
                strike_buf = []
                in_strike = False
            result_parts.append(text)

    # Flush any trailing strikethrough
    if strike_buf:
        result_parts.append(f"~~{''.join(strike_buf)}~~")

    return "".join(result_parts)


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
            from docx.text.paragraph import Paragraph
            para = Paragraph(block, doc)
            # Use run-level extraction to preserve ~~strikethrough~~ markers.
            # Fall back to para.text for paragraphs without run structure.
            rich_text = _paragraph_text_with_strikethrough(para)
            text = rich_text.strip()
            if not text:
                continue

            # For heading detection, compare against plain text (no ~~ noise)
            plain_text = para.text.strip()
            style_name = (para.style.name or "").lower()
            is_heading = (
                "heading" in style_name
                or _is_clause_header(plain_text)
                or _is_docx_subsection_label(plain_text)
            )

            if is_heading:
                _flush()
                # Headings are stored as plain text — strike markers on a
                # heading title would be confusing and hurt AI enrichment.
                current_title = plain_text
                current_lines = []
            else:
                current_lines.append(text)

        elif tag == "tbl":
            # Extract table cells preserving strikethrough formatting
            from docx.table import Table
            try:
                tbl = Table(block, doc)
                rows = []
                for row in tbl.rows:
                    cells = []
                    for cell in row.cells:
                        # Join paragraphs within each cell, preserving strike
                        cell_text = " ".join(
                            _paragraph_text_with_strikethrough(p).strip()
                            for p in cell.paragraphs
                            if p.text.strip()
                        )
                        cells.append(cell_text)
                    if any(cells):
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


def _clause_number(text: str) -> Optional[int]:
    """
    If `text` is a numbered clause header like "14. Hire Payment" or
    "14 — Hire Payment", return the clause number as an int.
    Returns None for non-numbered headers (PART II, ANNEX A, bold section
    labels, etc.) so those can always create a new chunk.
    """
    m = re.match(r"^\s*(\d{1,3})\s*[.\-—]", text.strip())
    return int(m.group(1)) if m else None


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
           - Monotonic clause guard: if a numbered clause header has a number
             LOWER than the highest clause number seen so far, it is a list
             item within the current clause body, not a new top-level clause.
             e.g. "2. the Vessel shall..." appearing inside clause 9 is a list
             item, not the re-appearance of clause 2.
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
    max_clause_seen: int = 0   # monotonic guard for numbered clauses

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

        # Monotonic clause guard: reject numbered headers whose number is
        # lower than the highest clause number seen so far — they are list
        # items inside the current clause, not new top-level clauses.
        if is_header and _is_clause_header(txt):
            num = _clause_number(txt)
            if num is not None:
                if num <= max_clause_seen:
                    # Back-reference: treat as body text, not a new header
                    is_header = False
                else:
                    max_clause_seen = num

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
# Left-margin line-number detection & stripping
# ─────────────────────────────────────────────────────────────────────────────

def _detect_margin_line_numbers(elements: List[Dict]) -> Optional[Dict]:
    """
    Detect sequential left-margin line numbers in charter-party style PDFs.

    Many BIMCO standard forms (and other maritime contracts) print a column of
    sequential integers (1, 2, 3 …) in the left margin of every page for legal
    cross-referencing.  PyMuPDF captures these as ordinary text elements,
    polluting extracted chunks with noise like "13 Deadweight 12580 MT".

    Detection algorithm (all conditions must be satisfied):
      1. At least 10 pure-integer (1–3 digit) elements exist in the document.
      2. Those integers cluster at a consistent x-position (modal 15-pt bin
         contains ≥10 members).
      3. The cluster's x-position falls in the left 25 % of the text area.
      4. The clustered integers span a range of at least 15 (e.g. 1–15+),
         ruling out incidental small numbers like page counts or table values.
      5. At least 50 % of the integers in the covered range are present
         (density check — rules out sparse data columns).
      6. When the cluster is sorted by vertical position (y), the numbers are
         mostly non-decreasing (≤ 20 % inversions), confirming top-to-bottom
         sequential ordering.

    Returns a dict with:
        'strip_ids'  — set of id(element) for pure-numeric margin elements to remove
        'x_cluster'  — approximate x-coordinate of the detected line-number column
    Returns None if no line-number column is detected.
    """
    from collections import Counter

    if not elements:
        return None

    # Step 1: candidate elements — bare 1-3 digit integers only
    numeric_els = [
        e for e in elements
        if re.fullmatch(r'\d{1,3}', e['text'].strip())
    ]
    if len(numeric_els) < 10:
        return None

    # Step 2: find modal x-position bin (15 pt resolution)
    BIN = 15.0
    x_bins: Counter = Counter(int(e['x'] / BIN) for e in numeric_els)
    modal_bin, modal_count = x_bins.most_common(1)[0]
    if modal_count < 10:
        return None

    cluster = [e for e in numeric_els if int(e['x'] / BIN) == modal_bin]
    cluster_x = modal_bin * BIN

    # Step 3: cluster must sit in the left quarter of the overall text area
    all_x = [e['x'] for e in elements]
    x_min, x_max = min(all_x), max(all_x)
    text_width = x_max - x_min
    if text_width > 0 and (cluster_x - x_min) / text_width > 0.25:
        return None

    # Step 4: numbers must cover a meaningful range
    nums = sorted(int(e['text'].strip()) for e in cluster)
    span = nums[-1] - nums[0]
    if span < 15:
        return None

    # Step 5: density — at least 50 % of integers in the range are present
    density = len(set(nums)) / (span + 1)
    if density < 0.50:
        return None

    # Step 6: monotonicity — when read top-to-bottom the numbers should
    # be non-decreasing with at most 20 % inversions
    cluster_by_y = sorted(cluster, key=lambda e: e['y'])
    seq = [int(e['text'].strip()) for e in cluster_by_y]
    if len(seq) > 1:
        inversions = sum(1 for i in range(len(seq) - 1) if seq[i] > seq[i + 1])
        if inversions / (len(seq) - 1) > 0.20:
            return None

    logger.info(
        "[extractor] Left-margin line numbers detected: "
        "x≈%.0fpt, range %d–%d, %d elements to strip",
        cluster_x, nums[0], nums[-1], len(cluster),
    )
    return {
        'strip_ids': {id(e) for e in cluster},
        'x_cluster': cluster_x,
    }


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
    - CRITICAL — cross-page clause joining: elements are accumulated across
      ALL pages with a global y-offset rather than being chunked per-page.
      This ensures that clause bodies which span multiple pages are kept
      together under their heading and not split into titleless orphan chunks.
      (e.g. NYPE 2015 clause 1 "Duration/Trip Description" has its header on
      page 1 and body text that continues onto page 2.)
    - Two-column pages are handled by placing the left column's elements before
      the right column's elements in global y-order (right column base offset =
      y_global + page_height), so column reading order is preserved globally.
    - A single `_column_to_chunks` call processes all pages' elements as one
      continuous document, maintaining header context across page boundaries.
    """
    import fitz

    MIN_FONT = 7.0   # pt — skip cargo diagram / slot-plan label text

    all_elements: List[Dict] = []   # global accumulator across all pages
    all_plain_lines: List[str] = []  # fallback accumulator

    with fitz.open(stream=raw_bytes, filetype="pdf") as pdf:
        y_global: float = 0.0   # running y-offset into global coordinate space

        for page in pdf:
            page_height = page.rect.height
            page_width  = page.rect.width
            elements: List[Dict] = []

            # ── Per-page strikethrough detection ──────────────────────────────
            # PyMuPDF does not expose strikethrough as a span flag.  Instead,
            # struck text is marked by a thin filled rectangle that passes
            # through the MIDDLE of a text line (25–80 % of text height).
            # Baseline underlines (≥ 80 %) and layout separator lines are excluded.
            #
            # Key insight: a real strikethrough band passes THROUGH text, so its
            # y-midpoint falls inside a text span's [y0, y1] vertical range.
            # Layout separators fall in whitespace BETWEEN paragraphs, so their
            # y-midpoint does not coincide with any text span.
            #
            # First pass: collect every text span's vertical bounds on this page.
            _span_ybounds: List[tuple] = []
            for _blk in page.get_text("dict")["blocks"]:
                if _blk.get("type") != 0:
                    continue
                for _ln in _blk.get("lines", []):
                    for _sp in _ln.get("spans", []):
                        _bb = _sp.get("bbox")
                        if _bb and _bb[3] > _bb[1]:
                            _span_ybounds.append((_bb[1], _bb[3]))

            strike_bands: List[tuple] = []   # (x0, x1, y_mid)
            for d in page.get_drawings():
                r = d.get("rect")
                if r is None:
                    continue
                band_h = r.y1 - r.y0
                band_w = r.x1 - r.x0
                # Must be a thin horizontal bar of meaningful width
                if band_h > 3.0 or band_w < 10.0:
                    continue
                # Reject bands that don't pass through any text span's vertical
                # range — those are layout separator lines, not strikethroughs.
                by_mid = (r.y0 + r.y1) / 2
                if not any(y0 <= by_mid <= y1 for y0, y1 in _span_ybounds):
                    continue
                strike_bands.append((r.x0, r.x1, by_mid))

            def _span_is_struck(bbox) -> bool:
                """True if any strike band passes through the middle of this span."""
                sx0, sy0, sx1, sy1 = bbox
                span_h = sy1 - sy0
                if span_h <= 0:
                    return False
                for bx0, bx1, by_mid in strike_bands:
                    # Horizontal overlap: band must cover at least half the span
                    overlap_w = min(sx1, bx1) - max(sx0, bx0)
                    if overlap_w < (sx1 - sx0) * 0.4:
                        continue
                    # Vertical position: band y must be in the middle 25–80% of
                    # the text height (strikethrough zone, not baseline underline)
                    rel = (by_mid - sy0) / span_h
                    if 0.25 <= rel <= 0.80:
                        return True
                return False
            # ──────────────────────────────────────────────────────────────────

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

                    # Build text with per-span strikethrough markers.
                    # Consecutive struck spans are GROUPED before calling
                    # _clean_pdf_text() so that ligature fragments like
                    # "par" + "ti" + "es" are merged into "parties" inside a
                    # single ~~…~~ block rather than ~~par~~ ~~ti~~ ~~es~~.
                    parts: List[str] = []
                    struck_buf: List[str] = []

                    def _flush_struck() -> None:
                        if not struck_buf:
                            return
                        merged = _clean_pdf_text(" ".join(struck_buf)).strip()
                        if merged:
                            parts.append(f"~~{merged}~~")
                        struck_buf.clear()

                    for s in text_spans:
                        raw = s["text"]
                        if not raw.strip():
                            continue
                        if _span_is_struck(s["bbox"]):
                            struck_buf.append(raw)
                        else:
                            _flush_struck()
                            parts.append(_clean_pdf_text(raw))
                    _flush_struck()

                    combined = " ".join(parts).strip()
                    if not combined:
                        continue

                    # Bold = flag bit 4 (value 16) set on ALL content spans
                    is_bold = all(bool(s["flags"] & 16) for s in text_spans)
                    x_pos = min(s["bbox"][0] for s in text_spans)
                    y_pos = min(s["bbox"][1] for s in text_spans)

                    elements.append({
                        "text": combined,
                        "size": max_size,
                        "is_bold": is_bold,
                        "x": x_pos,
                        "y": y_pos,   # page-local y; globalised below
                    })
                    all_plain_lines.append(combined)

            if not elements:
                y_global += page_height + 100
                continue

            # Per-page column detection (layout may differ between pages)
            col_split = _detect_column_split(elements, page_width)

            if col_split is not None:
                # Two-column page: sort each column by (y, x), then
                # place the entire left column before the right in global order.
                left_els  = sorted(
                    [e for e in elements if e["x"] <  col_split],
                    key=lambda e: (e["y"], e["x"]),
                )
                right_els = sorted(
                    [e for e in elements if e["x"] >= col_split],
                    key=lambda e: (e["y"], e["x"]),
                )
                # Left column: y_global + local_y (naturally before right)
                for e in left_els:
                    all_elements.append({**e, "y": y_global + e["y"]})
                # Right column: y_global + page_height + local_y
                # (ensures right-col elements sort AFTER left-col elements)
                right_base = y_global + page_height + 10
                for e in right_els:
                    all_elements.append({**e, "y": right_base + e["y"]})
            else:
                # Single-column page: simply offset local y into global space
                for e in elements:
                    all_elements.append({**e, "y": y_global + e["y"]})

            # Advance global offset past this page (+ small inter-page gap)
            y_global += page_height * 2 + 200

    # ── Left-margin line-number stripping ─────────────────────────────────────
    # Charter-party PDFs (BIMCO and similar) print sequential integers in a
    # narrow left-margin column for legal cross-referencing.  Strip them before
    # chunking so they don't pollute chunk bodies.
    #
    # Two-pass strategy:
    #   Pass 1 — remove elements whose entire text is a bare line number
    #             (the common case: number and content are in separate PyMuPDF
    #             blocks and therefore separate elements in all_elements).
    #   Pass 2 — for elements where PyMuPDF happened to merge a line number and
    #             its content into one span, strip the leading "N " prefix using
    #             _LN_PREFIX_RE (safe: requires ≤3 digits + space + letter, so
    #             genuine numeric content like "12580 MT" is never touched).
    ln_info = _detect_margin_line_numbers(all_elements)
    if ln_info:
        # Pass 1: drop pure-numeric margin elements
        all_elements = [e for e in all_elements if id(e) not in ln_info['strip_ids']]
        # Pass 2: strip inline prefixes from any merged spans
        for e in all_elements:
            e['text'] = _LN_PREFIX_RE.sub('', e['text'])
    # ──────────────────────────────────────────────────────────────────────────

    # Single global pass — header context is maintained across page boundaries
    all_chunks = _column_to_chunks(all_elements) if all_elements else []

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
# PDF extraction — raw mode (full-text, AI-first)
# ─────────────────────────────────────────────────────────────────────────────

# Document categories where heuristic font-based chunking causes more harm
# than good.  For these, we extract the entire document as a single blob (or
# one chunk per page for very long docs) and let the AI enrichment pass do all
# the semantic splitting.  This is the right call when:
#   - The PDF uses bold/larger text for data labels (e.g. "Flag", "GT/NT")
#     rather than section headers, confusing the header detector.
#   - The document is a fixture recap where numbered items like "1. Vessel /
#     Owners" have dozens of sub-items that belong in the same chunk.
#   - The document is a narrative addendum where every paragraph is connected.
_RAW_EXTRACTION_CATEGORIES = {
    "fixture_recap",
    "addendum",
    "delivery_details",
    "vessel_specifications",
    "vessel_owners_details",
    "speed_consumption",
}

# Word count threshold below which the whole document is returned as ONE chunk
# rather than split by page.  7-page fixture recaps are ~3 000 words — well
# within gpt-4o-mini's 128k-token context.
_RAW_SINGLE_CHUNK_THRESHOLD = 6_000  # words


def _presplit_on_clauses(full_text: str) -> List[Dict]:
    """
    Split a large text blob on numbered top-level clause headers.

    Matches lines like:
        "1. Vessel / Owners"
        "14. C/P Details"
        "1.1 Owners confirm ..."   ← sub-clause: kept inside parent chunk

    Only TOP-level integers (no dot after the digit group) trigger a new
    chunk boundary, so sub-clauses like "1.1", "1.2" stay within their
    parent section.

    Returns a list of {"title": str | None, "body": str} dicts where each
    dict corresponds to one numbered clause (or a preamble if text precedes
    clause 1).  Chunks are further split at MAX_CHUNK_WORDS if a single
    clause is very long.
    """
    # Matches "  1. " or "1. " at the start of a line — top-level only,
    # NOT "1.1" (has a second digit group after the dot).
    TOP_CLAUSE_RE = re.compile(
        r"^(\d{1,2})\.\s+(.+)$",
        re.MULTILINE,
    )

    lines = full_text.splitlines()
    chunks: List[Dict] = []
    current_title: Optional[str] = None
    current_lines: List[str] = []

    def _flush():
        body = "\n".join(current_lines).strip()
        if body:
            chunks.append({"title": current_title, "body": body})

    for line in lines:
        stripped = line.strip()
        m = TOP_CLAUSE_RE.match(stripped)
        if m:
            _flush()
            current_title = stripped
            current_lines = []
        else:
            current_lines.append(line)

    _flush()

    # If no clause markers were found, return None so caller falls back
    if not any(c["title"] for c in chunks):
        return []

    return _split_into_chunks(chunks)


def _extract_pdf_raw(raw_bytes: bytes) -> List[Dict]:
    """
    Extract the full text of a PDF using pdfplumber, then split it into
    per-clause chunks without relying on font metadata.

    Strategy:
    1. Extract text page-by-page (pdfplumber handles text-layer PDFs well).
    2. Try to split on numbered top-level clauses ("1. Vessel / Owners",
       "14. C/P Details" …).  This produces correctly-sized chunks and
       preserves clause context that font-based detection misses.
    3. If no numbered structure is found, return the whole document as one
       or a few page-batched chunks for AI enrichment to handle.

    All extracted content is passed intact — no lines are discarded.
    The AI enrichment pass then assigns titles and may further refine splits.
    """
    import io
    import pdfplumber

    page_texts: List[str] = []
    with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                text = _clean_pdf_text(text).strip()
                if text:
                    page_texts.append(text)

    if not page_texts:
        return []

    full_text = "\n\n".join(page_texts)

    # Try numbered-clause splitting first
    clause_chunks = _presplit_on_clauses(full_text)
    if clause_chunks:
        logger.info(
            f"[extractor] Raw PDF: presplit into {len(clause_chunks)} clause chunks"
        )
        return clause_chunks

    # No numbered structure — return as one or a few word-budget batches
    # so AI enrichment still sees the full context
    total_words = len(full_text.split())
    logger.info(
        f"[extractor] Raw PDF: no clause markers found, "
        f"returning {total_words} words as page batches"
    )

    if total_words <= _RAW_SINGLE_CHUNK_THRESHOLD:
        return [{"title": None, "body": full_text}]

    chunks: List[Dict] = []
    batch_lines: List[str] = []
    batch_words = 0

    for page_text in page_texts:
        page_words = len(page_text.split())
        if batch_words + page_words > _RAW_SINGLE_CHUNK_THRESHOLD and batch_lines:
            chunks.append({"title": None, "body": "\n\n".join(batch_lines)})
            batch_lines = []
            batch_words = 0
        batch_lines.append(page_text)
        batch_words += page_words

    if batch_lines:
        chunks.append({"title": None, "body": "\n\n".join(batch_lines)})

    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# PDF extraction — public entry point
# ─────────────────────────────────────────────────────────────────────────────

def extract_pdf(file_obj: IO[bytes], document_category: Optional[str] = None) -> List[Dict]:
    """
    Extract chunks from a .pdf file.

    When document_category is one of _RAW_EXTRACTION_CATEGORIES, uses
    _extract_pdf_raw (full-text, minimal pre-chunking) so that AI enrichment
    receives the complete document context and can split it correctly.

    Otherwise dispatch order:
      1. PyMuPDF  — font-aware header detection (bold / larger text)
      2. pdfplumber — clause-regex only (if PyMuPDF not installed)
      3. Fixed-size word chunks (if neither library produces structure)
    """
    raw_bytes = file_obj.read()

    # Use raw extraction for document types where font-heuristics misfire
    if document_category in _RAW_EXTRACTION_CATEGORIES:
        logger.info(
            f"[extractor] Using raw (AI-first) PDF extraction "
            f"for category '{document_category}'"
        )
        try:
            chunks = _extract_pdf_raw(raw_bytes)
            if chunks:
                return chunks
            logger.warning(
                "[extractor] Raw extraction returned no content — "
                "falling back to font-based extraction"
            )
        except ImportError:
            logger.warning(
                "[extractor] pdfplumber not available for raw extraction — "
                "falling back to font-based extraction"
            )

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
# XLSX extraction — helpers
# ─────────────────────────────────────────────────────────────────────────────

# Footer row labels (lower-cased, with colon) — terminate the data section.
_XLSX_FOOTER_KEYS = frozenset({
    'date:', 'vessel:', "vessel's name:", 'master:', "master's name:",
    'chief mate:', "ch. mate's name:", 'chief officer:', 'voyage no.:',
    'date of inventory:',
})
_XLSX_REMARKS_KEYS = frozenset({'remarks:', 'remarks'})
_XLSX_PAGE_RE = re.compile(r'^page\s+\d+\s*(of|/)\s*\d+', re.IGNORECASE)


def _xlsx_get_raw_grid(ws):
    """
    Return a 1-indexed 2D list of cell values WITHOUT merge expansion, plus
    (max_row, max_col). grid[row][col], both 1-based; grid[0] is unused.
    """
    max_row = ws.max_row or 0
    max_col = ws.max_column or 0
    if not max_row or not max_col:
        return [], 0, 0
    grid = [[None] * (max_col + 1) for _ in range(max_row + 1)]
    for row in ws.iter_rows():
        for cell in row:
            grid[cell.row][cell.column] = cell.value
    return grid, max_row, max_col


def _xlsx_expand_merged(ws, raw_grid, max_row, max_col):
    """
    Return a copy of raw_grid with every merged-cell region filled with the
    anchor cell's value.  Used for composite header building and section-label
    detection (where fully-merged rows have one value repeated across all cols).
    """
    import copy
    grid = copy.deepcopy(raw_grid)
    for mr in ws.merged_cells.ranges:
        anchor = grid[mr.min_row][mr.min_col]
        for r in range(mr.min_row, mr.max_row + 1):
            for c in range(mr.min_col, mr.max_col + 1):
                grid[r][c] = anchor
    return grid


def _xlsx_fmt(v) -> str:
    """Format a cell value to a clean, newline-free string."""
    if v is None:
        return ""
    from datetime import datetime as _dt
    if isinstance(v, _dt):
        return v.strftime("%Y-%m-%d")
    return str(v).strip().replace('\n', ' ')


def _xlsx_find_header_rows(raw_grid, max_row, max_col):
    """
    Use the RAW (unexpanded) grid to locate the main header row.

    Scans rows 4–14 for the first row that has:
    - ≥4 non-None string cells AND ≥3 DISTINCT values
      (rules out merged single-value title rows like "Inventory of …")

    Then checks whether the immediately following row is a sub-header:
    - 2 ≤ cells < main row cells AND ≥2 distinct values

    Returns (main_row_idx, sub_row_idx_or_None).
    """
    for r in range(4, min(max_row + 1, 15)):
        row = raw_grid[r][1:max_col + 1]
        str_cells = [v for v in row if v is not None and isinstance(v, str) and v.strip()]
        if len(str_cells) >= 4 and len(set(str_cells)) >= 3:
            sub_row = None
            if r + 1 <= max_row:
                nxt = raw_grid[r + 1][1:max_col + 1]
                nxt_str = [v for v in nxt if v is not None and isinstance(v, str) and v.strip()]
                if 2 <= len(nxt_str) < len(str_cells) and len(set(nxt_str)) >= 2:
                    sub_row = r + 1
            return r, sub_row
    return None, None


def _xlsx_build_headers(exp_grid, main_row, sub_row, max_col) -> dict:
    """
    Build {col_idx: header_name} using the EXPANDED grid so that
    merge-spanned column values are available in all relevant columns.

    When a sub-row value differs from the main-row value → "Main (Sub)".
    E.g. main="Total quantity", sub="Good" → "Total quantity (Good)".
    """
    headers: dict = {}
    for c in range(1, max_col + 1):
        mv = exp_grid[main_row][c] if main_row else None
        sv = exp_grid[sub_row][c] if sub_row else None
        ms = str(mv).strip().replace('\n', ' ') if mv is not None else None
        ss = str(sv).strip().replace('\n', ' ') if sv is not None else None
        if ms and ss and ms != ss:
            headers[c] = f"{ms} ({ss})"
        elif ms:
            headers[c] = ms
        elif ss:
            headers[c] = ss
    return headers


def _xlsx_is_section_label(grid_row, max_col) -> bool:
    """
    True when the row represents an internal section/subsection header.

    Handles both:
    - A single non-None cell in column 1 (unmerged section label)
    - A fully-merged row where all non-None values are the same short string
      (e.g. the A8:P8 merged "Lashings" label row)

    Excludes footer keys, remarks labels, and "Page N of N" references.
    """
    non_none = [grid_row[c] for c in range(1, max_col + 1) if grid_row[c] is not None]
    if not non_none:
        return False
    distinct = set(str(v).strip() for v in non_none)
    if len(distinct) != 1:
        return False
    val_str = next(iter(distinct))
    if not val_str:
        return False
    lower = val_str.lower()
    if lower in _XLSX_FOOTER_KEYS:
        return False
    if lower.rstrip(':') in _XLSX_REMARKS_KEYS:
        return False
    if _XLSX_PAGE_RE.match(val_str):
        return False
    if len(val_str) > 80:
        return False
    return True


def _xlsx_get_vessel_name(wb) -> Optional[str]:
    """
    Scan all sheets for a "Vessel:" or "Vessel's name:" label row and return
    the first subsequent non-label cell value on the same row.
    """
    vessel_keys = {"vessel:", "vessel's name:"}
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        raw_grid, max_row, max_col = _xlsx_get_raw_grid(ws)
        if max_row == 0:
            continue
        for r in range(1, max_row + 1):
            row = raw_grid[r]
            for c in range(1, max_col + 1):
                v = row[c]
                if v is None:
                    continue
                if str(v).strip().lower() in vessel_keys:
                    for nc in range(c + 1, max_col + 1):
                        nv = row[nc]
                        if nv is None:
                            continue
                        nv_str = str(nv).strip()
                        if nv_str and nv_str.lower() not in vessel_keys and nv_str.lower() not in _XLSX_FOOTER_KEYS:
                            return nv_str
    return None


def _xlsx_extract_front_page(
    sheet_name: str, exp_grid: list, max_row: int, max_col: int,
    vessel_name: Optional[str], chapter: Optional[str],
) -> Optional[Dict]:
    """
    Extract the Front Page tab as a labelled prose dump.
    Merged cell values are de-duplicated within each row.
    """
    lines: List[str] = []
    for r in range(1, max_row + 1):
        seen: set = set()
        vals: List[str] = []
        for c in range(1, max_col + 1):
            v = exp_grid[r][c]
            if v is None:
                continue
            vs = _xlsx_fmt(v)
            if vs and vs not in seen:
                seen.add(vs)
                vals.append(vs)
        if vals:
            lines.append("  ".join(vals))
    body = "\n".join(lines).strip()
    if not body:
        return None
    parts = [p for p in [vessel_name, chapter, sheet_name] if p]
    return {"title": " - ".join(parts) if parts else sheet_name, "body": body}


def _xlsx_extract_regular_sheet(
    sheet_name: str, raw_grid: list, exp_grid: list,
    max_row: int, max_col: int,
    vessel_name: Optional[str], chapter: Optional[str],
) -> List[Dict]:
    """
    Extract a regular data sheet into one chunk.

    Title: [Vessel Name] - [Chapter] - [Subchapter (tab name)]
    Body:
      [Section: X]                   ← subsection headers (fully-merged rows)
      Header: value  |  Header: value  ← data rows with composite column names
      ...
      Remarks: ...                   ← remarks block (if present)
      ---
      Date: ... | Vessel: ... | Master: ... | Chief Mate: ...
    """
    main_row, sub_row = _xlsx_find_header_rows(raw_grid, max_row, max_col)
    if main_row is None:
        # Fallback: dump all non-empty rows
        lines: List[str] = []
        for r in range(1, max_row + 1):
            seen: set = set()
            vals: List[str] = []
            for c in range(1, max_col + 1):
                v = exp_grid[r][c]
                if v is None:
                    continue
                vs = _xlsx_fmt(v)
                if vs and vs not in seen:
                    seen.add(vs)
                    vals.append(vs)
            if vals:
                lines.append("  ".join(vals))
        body = "\n".join(lines).strip()
        parts = [p for p in [vessel_name, chapter, sheet_name] if p]
        return [{"title": " - ".join(parts), "body": body}] if body else []

    headers = _xlsx_build_headers(exp_grid, main_row, sub_row, max_col)
    if not headers:
        return []

    data_start = (sub_row or main_row) + 1
    body_lines: List[str] = []
    footer_parts: List[str] = []
    remarks_lines: List[str] = []
    in_remarks = False

    for r in range(data_start, max_row + 1):
        row = exp_grid[r]

        if all(row[c] is None for c in range(1, max_col + 1)):
            continue

        # First non-None value in this row
        first_val: Optional[str] = None
        first_col: Optional[int] = None
        for c in range(1, max_col + 1):
            if row[c] is not None:
                first_val = str(row[c]).strip()
                first_col = c
                break
        if first_val is None:
            continue

        # ── Footer row ────────────────────────────────────────────────────────
        if first_val.lower() in _XLSX_FOOTER_KEYS:
            in_remarks = False
            kvs = [first_val]
            for c in range(first_col + 1, max_col + 1):
                nv = row[c]
                if nv is None:
                    continue
                nv_str = str(nv).strip()
                if nv_str and nv_str.lower() not in _XLSX_FOOTER_KEYS and nv_str.lower() != first_val.lower():
                    kvs.append(_xlsx_fmt(nv))
                    break
            footer_parts.append(" ".join(kvs))
            continue

        # ── Remarks row ───────────────────────────────────────────────────────
        if first_val.lower().rstrip(':') in _XLSX_REMARKS_KEYS:
            in_remarks = True
            inline: List[str] = []
            for c in range(first_col + 1, max_col + 1):
                nv = row[c]
                if nv is not None:
                    vs = _xlsx_fmt(nv)
                    if vs:
                        inline.append(vs)
            remarks_lines.append(
                "Remarks: " + " | ".join(inline) if inline else "Remarks:"
            )
            continue

        if in_remarks:
            seen2: set = set()
            vals2: List[str] = []
            for c in range(1, max_col + 1):
                v = row[c]
                if v is None:
                    continue
                vs = _xlsx_fmt(v)
                if vs and vs not in seen2:
                    seen2.add(vs)
                    vals2.append(vs)
            if vals2:
                remarks_lines.append("  ".join(vals2))
            continue

        # ── Section label (fully merged or single-cell row) ───────────────────
        if _xlsx_is_section_label(row, max_col):
            body_lines.append(f"\n[Section: {first_val}]")
            continue

        # ── Data row → composite header: value pairs ──────────────────────────
        pairs: List[str] = []
        for c in range(1, max_col + 1):
            v = row[c]
            if v is None:
                continue
            vs = _xlsx_fmt(v)
            if vs and vs != '-':
                pairs.append(f"{headers.get(c, f'Col{c}')}: {vs}")
        if pairs:
            body_lines.append("  |  ".join(pairs))

    # ── Assemble ──────────────────────────────────────────────────────────────
    body_parts: List[str] = []
    if body_lines:
        body_parts.append("\n".join(body_lines).strip("\n"))
    if remarks_lines:
        body_parts.append("\n".join(remarks_lines))
    if footer_parts:
        body_parts.append("---\n" + " | ".join(footer_parts))

    body = "\n\n".join(body_parts).strip()
    if not body:
        return []

    title_parts = [p for p in [vessel_name, chapter, sheet_name] if p]
    return [{"title": " - ".join(title_parts), "body": body}]


# ─────────────────────────────────────────────────────────────────────────────
# XLSX extraction — public entry point
# ─────────────────────────────────────────────────────────────────────────────

def extract_xlsx(file_obj: IO[bytes]) -> List[Dict]:
    """
    Extract chunks from an .xlsx/.xls maritime inventory or equipment file.

    Structure detected per sheet:
    - Front Page tab: dumped as labelled prose (vessel info, master, instructions).
    - Regular tabs:   one chunk each.
        Title: [Vessel Name] - [Chapter] - [Subchapter (tab name)]
        Body:  [Section: X] labels  +  structured data rows (Header: value |…)
               +  Remarks block  +  '---' footer line

    Composite column headers are built by merging a two-row header band:
    e.g. "Total quantity" / "Good" → "Total quantity (Good)".
    Fully-merged section label rows (e.g. A8:P8 "Lashings") are preserved
    as [Section: Lashings] markers in the body.

    Falls back to xlrd for legacy .xls files if openpyxl cannot load the file.
    """
    import io as _io

    raw = file_obj.read()

    try:
        import openpyxl
        wb = openpyxl.load_workbook(_io.BytesIO(raw), data_only=True)
    except Exception as e:
        try:
            return _extract_xlsx_xlrd_fallback(raw)
        except ImportError:
            raise ImportError(
                "No spreadsheet library found. Run: pip install openpyxl"
            ) from e

    vessel_name = _xlsx_get_vessel_name(wb)
    chunks: List[Dict] = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        raw_grid, max_row, max_col = _xlsx_get_raw_grid(ws)
        if max_row == 0 or max_col == 0:
            continue

        exp_grid = _xlsx_expand_merged(ws, raw_grid, max_row, max_col)

        # Chapter title: first non-None value in row 1 (expansion covers merged title)
        chapter: Optional[str] = None
        for c in range(1, max_col + 1):
            if exp_grid[1][c] is not None:
                chapter = _xlsx_fmt(exp_grid[1][c])
                break

        norm = sheet_name.lower().replace(" ", "").replace("_", "")
        if norm in ("frontpage", "coverpage", "titlepage"):
            chunk = _xlsx_extract_front_page(
                sheet_name, exp_grid, max_row, max_col, vessel_name, chapter
            )
            if chunk:
                chunks.append(chunk)
        else:
            chunks.extend(
                _xlsx_extract_regular_sheet(
                    sheet_name, raw_grid, exp_grid, max_row, max_col,
                    vessel_name, chapter,
                )
            )

    if not chunks:
        chunks = [{"title": None, "body": "No data found in spreadsheet."}]

    return _split_into_chunks(chunks)


def _extract_xlsx_xlrd_fallback(raw: bytes) -> List[Dict]:
    """
    Basic .xls extraction via xlrd (legacy format, no merged-cell awareness).
    Returns one chunk per sheet as pipe-separated rows.
    """
    import xlrd
    wb = xlrd.open_workbook(file_contents=raw)
    chunks: List[Dict] = []
    for sheet_name in wb.sheet_names():
        ws = wb.sheet_by_name(sheet_name)
        lines: List[str] = []
        for rx in range(ws.nrows):
            row_vals = [
                str(ws.cell(rx, cx).value).strip()
                for cx in range(ws.ncols)
                if str(ws.cell(rx, cx).value).strip()
            ]
            if row_vals:
                lines.append("  |  ".join(row_vals))
        body = "\n".join(lines).strip()
        if body:
            chunks.append({"title": sheet_name, "body": body})
    return _split_into_chunks(chunks or [{"title": None, "body": "No data found in spreadsheet."}])


# ─────────────────────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────────────────────

def extract(
    file_obj: IO[bytes],
    filename: str,
    document_category: Optional[str] = None,
) -> List[Dict]:
    """
    Route to the correct extractor based on file extension.

    Args:
        file_obj:           Readable binary stream of the uploaded file.
        filename:           Original filename (used for extension detection).
        document_category:  Dossier section slug (e.g. "fixture_recap").
                            When provided, PDF extraction uses the appropriate
                            strategy for that document type — raw full-text
                            extraction for narrative/spec docs so that AI
                            enrichment receives complete context.

    Returns:
        List of {"title": str|None, "body": str} dicts, ready for DB insertion.
    Raises:
        ValueError: unsupported file type
        ImportError: missing optional dependency (pdfplumber)
    """
    ext = filename.rsplit(".", 1)[-1].lower()
    logger.info(
        f"[extractor] Extracting {filename} (type={ext}, category={document_category})"
    )

    if ext == "docx":
        return extract_docx(file_obj)
    elif ext == "pdf":
        return extract_pdf(file_obj, document_category=document_category)
    elif ext in ("xlsx", "xls"):
        return extract_xlsx(file_obj)
    else:
        raise ValueError(f"Unsupported file type: .{ext}")
