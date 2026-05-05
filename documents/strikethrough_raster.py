#!/usr/bin/env python3
"""
Pixel-level strikethrough detection for image-source PDFs (scans).

The existing strikethrough machinery in ``extractor.py`` keys off vector
drawing operators that PyMuPDF returns from ``page.get_drawings()``. That
works for digital PDFs (BIMCO SmartCon, native NYPE forms) where struck-out
text is rendered as a horizontal line drawing. It does NOT work for scanned
charter parties: the original strike marks are baked into the page image, so
``get_drawings()`` returns ``[]``.

This module renders each page as a raster, finds horizontal pixel runs that
look like strike marks, and returns them as ``(x0, x1, y_mid)`` tuples in
PDF coordinate space — the same shape as ``_fitz_collect_strike_bands``,
so the rest of the strikethrough pipeline (per-glyph geometry test, segment
emission, ~~marker~~ wrapping) keeps working unchanged.

We deliberately over-supply candidate bands and let the downstream geometry
filter (``_fitz_horizontal_rule_is_strikethrough_in_box``) decide which ones
actually cross the middle of a glyph row. That filter already rejects
underlines (band sits below baseline) and over-bars (band sits above the
glyph), so a few extra candidates cost nothing — they just don't match.

Dependencies: opencv-python-headless, numpy. Both available wheel-only on
Linux/macOS, no system packages required.
"""

import logging
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)


# Render DPI for strikethrough detection. 200 dpi balances accuracy
# (1pt → ~2.78px, so a 1pt strike rule is ~3px tall — recognisable) with
# memory (an A4 page at 200 dpi is ~16 megapixels = 64 MB RGB).
_RENDER_DPI = 200
_PT_PER_INCH = 72.0


def _can_run() -> bool:
    """Return True iff opencv + numpy + PyMuPDF are importable."""
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
        import fitz  # noqa: F401
        return True
    except ImportError as e:
        logger.warning(
            "[strike-raster] dependency missing (%s) — skipping raster "
            "strikethrough detection", e,
        )
        return False


def detect_raster_strike_bands(
    page,
    *,
    render_dpi: int = _RENDER_DPI,
) -> List[Tuple[float, float, float]]:
    """
    Detect candidate strike-rule bands on a scanned/raster PDF page.

    Args:
        page:        A ``fitz.Page`` (PyMuPDF).
        render_dpi:  Rendering resolution. 200 dpi is a good default; lower
                     values miss thin strike rules, higher values eat memory.

    Returns:
        List of ``(x0_pt, x1_pt, y_mid_pt)`` in PDF point coordinates,
        compatible with ``_fitz_collect_strike_bands`` so the same downstream
        strikethrough machinery applies. Returns ``[]`` on any failure
        (missing deps, render error, etc.) so the caller never breaks.
    """
    if not _can_run():
        return []

    import cv2
    import numpy as np
    import fitz

    try:
        # Render the page at the chosen DPI. PyMuPDF's get_pixmap takes a
        # transformation matrix; we want the page rendered as if printed at
        # render_dpi. The Y axis flips later in the coordinate-mapping step.
        zoom = render_dpi / _PT_PER_INCH
        mat = fitz.Matrix(zoom, zoom)
        # Render in grayscale (csGRAY). RGB would triple memory for no gain.
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY, alpha=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width
        )
    except Exception as e:
        logger.warning(
            "[strike-raster] render failed for page %d: %s",
            getattr(page, "number", -1), e,
        )
        return []

    # Binary-threshold so strike rules are foreground (255). Otsu adapts to
    # the page's actual ink density (some scans are dim, some are dark).
    # We invert so ink → 1, background → 0 for morphology.
    _, bw = cv2.threshold(
        img, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU
    )

    # Horizontal kernel: a strike rule is a thin run that's much wider than
    # it is tall. We morphologically OPEN with a long horizontal kernel —
    # which keeps long horizontal features (rules, strikes, table borders)
    # and erases everything else (text, noise).
    #
    # Kernel width 60 px @ 200 dpi ≈ 22 pt — must span at least 4–5 narrow
    # characters. Anything shorter is almost always a logo crossbar, a "5"
    # / "F" / "E" glyph stroke, or a single-char descender — never a real
    # strike-through edit on negotiated contract text. Raising the floor
    # from 30 to 60 px eliminated the bulk of false positives in
    # spot-testing on the Morgenstond CP without missing any real strikes
    # (which always cover at least one full word).
    kernel_w = max(16, int(0.30 * render_dpi))   # ~60px @200dpi
    kernel_h = 1
    horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_w, kernel_h))
    horiz = cv2.morphologyEx(bw, cv2.MORPH_OPEN, horiz_kernel)

    # Connected-component analysis — each blob is a candidate horizontal rule.
    n_labels, _, stats, _ = cv2.connectedComponentsWithStats(horiz, connectivity=8)

    bands: List[Tuple[float, float, float]] = []
    page_h_pt = page.rect.height
    page_w_pt = page.rect.width
    pix_to_pt_x = page_w_pt / pix.width
    pix_to_pt_y = page_h_pt / pix.height

    for lbl in range(1, n_labels):  # 0 is background
        x_px, y_px, w_px, h_px, area = stats[lbl]

        # Geometry filters — what does a strike look like at this DPI?
        if w_px < kernel_w:
            # Shorter than the structuring element → noise, not a rule.
            continue
        if h_px > 0.05 * render_dpi:
            # Taller than ~3.6 pt → probably a divider / decorative box edge.
            # Real strike rules are 0.3–1.0 pt tall.
            continue
        if w_px / max(1, h_px) < 8:
            # Aspect ratio: strike rules are at least 8x as wide as tall.
            # Anything less square-ish is a glyph fragment (e.g. the bar of
            # the letter T on a really aggressive open-morphology pass).
            continue
        # Density check — the rectangle's filled area should be close to its
        # bounding box. A 30×2 strike rule has area ≈ 60 px; the bbox is 60.
        # A noisy diagonal noise pattern would have a much lower fill ratio.
        if area / (w_px * h_px) < 0.55:
            continue

        # Map pixel coords back to PDF points. PyMuPDF page coordinates go
        # top-down (origin at top-left), same as image coordinates, so no
        # Y-flip is needed when both come from the same page.
        x0_pt = x_px * pix_to_pt_x
        x1_pt = (x_px + w_px) * pix_to_pt_x
        y_mid_pt = (y_px + h_px / 2.0) * pix_to_pt_y

        # Drop bands in the printed footer band (matches the position-based
        # footer drop in _extract_pdf_fitz_presplit). Anything below 90 % of
        # page height is page-furniture and never crosses real clause text.
        if y_mid_pt >= 0.90 * page_h_pt:
            continue
        # Drop bands in the very top header zone (vendor logos, watermarks).
        if y_mid_pt <= 0.04 * page_h_pt:
            continue

        bands.append((x0_pt, x1_pt, y_mid_pt))

    if bands:
        logger.debug(
            "[strike-raster] page %d: %d candidate strike bands "
            "(rendered at %d dpi)",
            getattr(page, "number", -1), len(bands), render_dpi,
        )
    return bands


def merge_strike_bands(
    *band_lists: List[Tuple[float, float, float]],
) -> List[Tuple[float, float, float]]:
    """
    Concatenate strike-band lists from multiple sources (vector + raster).

    The downstream geometry filter is idempotent — feeding the same band
    twice yields the same struck/non-struck classification — so we don't
    need to dedupe. Kept as a named helper so the call site documents
    intent and a future smarter merge (e.g. cluster nearby bands) has a
    place to live.
    """
    out: List[Tuple[float, float, float]] = []
    for lst in band_lists:
        if lst:
            out.extend(lst)
    return out
