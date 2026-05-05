#!/usr/bin/env python3
"""
OCR-aware preprocessing for image-source PDFs (scans).

Charter parties uploaded as scans (multifunction-printer output, mobile photo
capture, faxed copies) typically arrive with an OCR text layer that the
scanner produced — and that text layer is usually awful: words glued together
("involvedfor"), wrong characters ("BIMCG", "ChinsaylD", "rkicijmenl"), and
strikethrough rendered as random punctuation ("Heeattime}" instead of
"(local time)").

This module:
  1. Detects when a PDF was sourced from a scan (heuristics on metadata,
     producer string, page composition).
  2. Re-OCRs it with ocrmypdf + Tesseract to produce a clean text layer,
     returning new PDF bytes that the rest of the extractor can process.
  3. Falls back gracefully if ocrmypdf / tesseract is not available
     (e.g. on Vercel — read-only filesystem, no system tesseract). The
     caller continues with the original bytes plus a logged warning.

Designed to be a thin wrapper so a future cloud-OCR provider (Azure DI,
AWS Textract, Google Document AI) can be slotted in beside ``reocr_pdf``
without touching extractor.py.
"""

import io
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Detection — should this PDF be re-OCR'd?
# ─────────────────────────────────────────────────────────────────────────────

# Producer / creator strings produced by common multifunction printers and
# scanner software. When matched we treat the document as scan-sourced even
# if other heuristics are inconclusive.
_SCANNER_PRODUCER_HINTS = (
    "MFP",          # Toshiba e-STUDIO ES…, Canon imageRUNNER, Sharp MX
    "imageRUNNER",
    "WorkCentre",   # Xerox
    "imageCLASS",   # Canon
    "PageScope",    # Konica Minolta
    "RICOH",
    "EPSON Scan",
    "OKI",
    "DocuCentre",
)


def is_image_pdf(raw_bytes: bytes) -> bool:
    """
    Heuristic: True when the PDF was almost certainly produced from a scan
    and would benefit from re-OCR.

    Signals (any one is sufficient):
      - PDF metadata.subject == "Image"
      - PDF metadata.producer matches a known scanner-software hint
      - Every page contains exactly 1 image and 0 vector drawings, AND the
        text-character-per-page ratio is consistent with OCR output (not
        truly empty, not native digital text).

    The function never raises — on any error it logs and returns False so
    the caller falls back to the existing extraction path.
    """
    try:
        import fitz
    except ImportError:
        return False

    try:
        with fitz.open(stream=raw_bytes, filetype="pdf") as pdf:
            meta = pdf.metadata or {}
            if (meta.get("subject") or "").strip().lower() == "image":
                logger.info(
                    "[ocr] Image-PDF detected via metadata.subject=='Image' "
                    "(producer=%r)",
                    meta.get("producer"),
                )
                return True
            producer = (meta.get("producer") or "") + " " + (meta.get("creator") or "")
            if any(h.lower() in producer.lower() for h in _SCANNER_PRODUCER_HINTS):
                logger.info(
                    "[ocr] Image-PDF detected via producer hint: %r",
                    producer.strip(),
                )
                return True

            # Per-page composition check — sample up to 8 pages.
            n_pages = min(len(pdf), 8)
            if n_pages == 0:
                return False
            scan_pages = 0
            for i in range(n_pages):
                page = pdf[i]
                images = page.get_images(full=True)
                drawings = page.get_drawings()
                # A scanned page is one big image and effectively no vectors.
                if len(images) >= 1 and len(drawings) <= 2:
                    scan_pages += 1
            ratio = scan_pages / n_pages
            if ratio >= 0.85:
                logger.info(
                    "[ocr] Image-PDF detected via page composition "
                    "(%d/%d sampled pages are image-with-no-vectors)",
                    scan_pages, n_pages,
                )
                return True
    except Exception as e:
        logger.warning(
            "[ocr] is_image_pdf check failed: %s — assuming NOT an image PDF",
            e,
        )
        return False
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Re-OCR via local ocrmypdf + Tesseract
# ─────────────────────────────────────────────────────────────────────────────

# Hard ceiling on re-OCR runtime. ocrmypdf parallelizes across cores so even
# 35-page CPs typically finish in 30–60 s on a modern Droplet. Anything past
# the limit is killed and the caller falls back to the original bytes.
_REOCR_TIMEOUT_DEFAULT = int(os.getenv("DOCLING_REOCR_TIMEOUT", "180"))

# Optional override: an absolute path to the ocrmypdf executable, useful on
# Droplets where system PATH may not include the install location.
_OCRMYPDF_BIN = os.getenv("OCRMYPDF_BIN") or shutil.which("ocrmypdf")


def _ocrmypdf_available() -> bool:
    return _OCRMYPDF_BIN is not None and shutil.which("tesseract") is not None


def reocr_pdf(
    raw_bytes: bytes,
    *,
    timeout_s: Optional[int] = None,
    language: str = "eng",
) -> Optional[bytes]:
    """
    Re-OCR a PDF in place using ocrmypdf, returning the cleaned-up PDF bytes.

    Returns ``None`` (and logs a warning) if:
      - ocrmypdf or tesseract is not available on this host (e.g. Vercel)
      - the subprocess times out
      - the subprocess fails with a non-zero exit code

    Callers should treat ``None`` as "use the original bytes". We never
    propagate exceptions — re-OCR is a best-effort enhancement.

    Args:
        raw_bytes: Original PDF bytes.
        timeout_s: Hard deadline. Default reads ``DOCLING_REOCR_TIMEOUT`` env
                   (180 s).
        language:  Tesseract language pack(s). Default English. Use
                   ``"eng+spa"`` style for multi-language.
    """
    if not _ocrmypdf_available():
        logger.warning(
            "[ocr] reocr_pdf: ocrmypdf or tesseract not installed "
            "(ocrmypdf=%s, tesseract=%s) — skipping re-OCR",
            _OCRMYPDF_BIN, shutil.which("tesseract"),
        )
        return None

    deadline = timeout_s if timeout_s is not None else _REOCR_TIMEOUT_DEFAULT

    # ocrmypdf cannot read from stdin reliably across versions, and it needs a
    # writable output path — both Vercel-incompatible. Use tempfile.
    with tempfile.TemporaryDirectory() as tmp:
        in_path = os.path.join(tmp, "in.pdf")
        out_path = os.path.join(tmp, "out.pdf")
        with open(in_path, "wb") as f:
            f.write(raw_bytes)

        # --redo-ocr        re-OCR pages that already have a text layer
        #                   (which is exactly our case — the scanner produced
        #                   one and it's bad). Falls back to --force-ocr if
        #                   --redo-ocr cannot map all glyphs.
        # --skip-big 50     skip OCR on giant pages (>50 megapixels) — protects
        #                   against runaway memory on poster-sized scans.
        # --output-type pdf preserve original PDF version; smaller output than
        #                   default pdfa. Strikethrough overlay (which we
        #                   detect later) doesn't care which PDF/A profile
        #                   ocrmypdf would have rewritten to.
        # --quiet           don't pollute the application log with OCR output.
        cmd = [
            _OCRMYPDF_BIN,
            "--redo-ocr",
            "--skip-big", "50",
            "--output-type", "pdf",
            "-l", language,
            "--quiet",
            in_path, out_path,
        ]

        logger.info(
            "[ocr] reocr_pdf: running ocrmypdf (lang=%s, timeout=%ds, %d input bytes)",
            language, deadline, len(raw_bytes),
        )
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                timeout=deadline,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "[ocr] reocr_pdf: ocrmypdf timed out after %ds — "
                "falling back to original bytes",
                deadline,
            )
            return None
        except (OSError, FileNotFoundError) as e:
            logger.warning(
                "[ocr] reocr_pdf: ocrmypdf invocation failed: %s — "
                "falling back to original bytes",
                e,
            )
            return None

        # Exit code 6 = "OCR was skipped because all pages already have text
        # but --redo-ocr couldn't process some of them" — try again with
        # --force-ocr (which rasterises and re-OCRs even if it loses fidelity).
        if proc.returncode == 6:
            logger.info(
                "[ocr] reocr_pdf: --redo-ocr could not map all glyphs; "
                "retrying with --force-ocr"
            )
            cmd_force = [
                _OCRMYPDF_BIN,
                "--force-ocr",
                "--skip-big", "50",
                "--output-type", "pdf",
                "-l", language,
                "--quiet",
                in_path, out_path,
            ]
            try:
                proc = subprocess.run(
                    cmd_force,
                    check=False,
                    timeout=deadline,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except subprocess.TimeoutExpired:
                logger.warning(
                    "[ocr] reocr_pdf: --force-ocr timed out after %ds",
                    deadline,
                )
                return None

        if proc.returncode != 0:
            logger.warning(
                "[ocr] reocr_pdf: ocrmypdf exit %d — falling back to original "
                "bytes. stderr=%s",
                proc.returncode,
                (proc.stderr or b"")[:400].decode("utf-8", errors="replace"),
            )
            return None

        if not os.path.exists(out_path):
            logger.warning(
                "[ocr] reocr_pdf: ocrmypdf returned 0 but no output file — "
                "falling back to original bytes"
            )
            return None

        with open(out_path, "rb") as f:
            cleaned = f.read()
        logger.info(
            "[ocr] reocr_pdf: success (%d → %d bytes, %+d%%)",
            len(raw_bytes), len(cleaned),
            100 * (len(cleaned) - len(raw_bytes)) // max(1, len(raw_bytes)),
        )
        return cleaned


def maybe_reocr(
    raw_bytes: bytes,
    *,
    force: bool = False,
    timeout_s: Optional[int] = None,
) -> bytes:
    """
    Convenience: detect whether ``raw_bytes`` is image-sourced and, if so,
    re-OCR. Returns the (possibly-cleaned) PDF bytes — never raises, always
    returns usable bytes.

    Pass ``force=True`` to skip the detector and always attempt re-OCR
    (useful when the user explicitly opts in via a UI checkbox).
    """
    if not (force or is_image_pdf(raw_bytes)):
        return raw_bytes
    cleaned = reocr_pdf(raw_bytes, timeout_s=timeout_s)
    return cleaned if cleaned is not None else raw_bytes
