#!/usr/bin/env python3
"""
Vessel-hold geometry resolver for the cargo pipeline.

Source-of-truth precedence for any vessel's hold data:

  1. **DB column** ``Vessel.holds_json``
     The JSON-serialised visualizer payload.  Populated either by an explicit
     edit (cargo "Edit Holds" UI / API) or by an auto-cache step the first
     time we successfully parse legacy filesystem JSON for the same vessel.
     This is what makes cargo work on Vercel.

  2. **Legacy filesystem JSON** ``output/vessels/<slug>/<slug>_data.json``
     The original docling output of the vessel-spec extraction pipeline.
     Read on demand and **cached back to the DB** on first hit.  After that
     first read, the DB column wins.  Filesystem JSONs are gitignored and
     vercelignored, so this path only triggers in local-dev environments
     where the legacy artifacts still exist on disk.

This module owns the regex parsing that previously lived inline in
``app.py:cargo_vessels_api``.  ``app.py`` now delegates to ``list_vessel_metas``
and the cargo blueprint delegates to ``get_vessel_meta``.

The visualizer-shaped dict returned by every helper has this schema::

    {
      "id":                   "<slug>",            # filesystem slug
      "name":                 "<display name>",
      "loa":                  float,               # length overall (m)
      "breadth":              float,               # m
      "depth":                float,               # m
      "draft":                float | None,        # summer draft (m)
      "holds_count":          int,
      "hold_capacity_m3":     float | None,
      "has_tween":            bool,
      "double_bottom_height": float,               # default 1.5
      "holds": [                                   # list of hold dicts
         {"id": int, "length": float, "breadth": float, "height": float,
          "lower_height": float, "upper_height": float, "has_tween": bool,
          "estimated": bool, "tween_estimated": bool},
         ...
      ],
    }
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

from models import db, Vessel

logger = logging.getLogger(__name__)


# Filesystem layout of the legacy vessel-spec extractor — same path used by
# the original cargo_vessels_api in app.py.
_VESSEL_FS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "output", "vessels",
)


# ─────────────────────────────────────────────────────────────────────────────
# Slug helpers
# ─────────────────────────────────────────────────────────────────────────────

def slugify_vessel_name(name: str) -> str:
    """Match the legacy output/vessels/<slug>/ naming convention."""
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def display_name_from_slug(slug: str) -> str:
    return (slug or "").replace("_", " ").title()


# ─────────────────────────────────────────────────────────────────────────────
# Regex parsing of legacy filesystem JSONs (extracted from app.py)
# ─────────────────────────────────────────────────────────────────────────────

def _fnum(s: Any) -> Optional[float]:
    if not s:
        return None
    s = str(s).strip().replace(",", ".")
    try:
        return float(re.sub(r"[^\d.]", "", s.split()[0]))
    except Exception:
        return None


def _find(pattern: str, text: str, flags=re.IGNORECASE) -> Optional[str]:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def _apply_tween(h: dict,
                 hid: int,
                 per_hold_tween: dict,
                 global_lower_h: Optional[float],
                 global_upper_h: Optional[float],
                 has_tween: bool) -> None:
    """Populate lower_height / upper_height / has_tween on a hold dict in place."""
    h_h = h.get("height", 9.0)
    td = per_hold_tween.get(hid)
    if td and td["lower"] and td["upper"]:
        h["lower_height"] = td["lower"]
        h["upper_height"] = td["upper"]
        h["has_tween"]    = True
    elif global_lower_h and global_upper_h:
        h["lower_height"] = global_lower_h
        h["upper_height"] = global_upper_h
        h["has_tween"]    = True
    elif has_tween:
        h["lower_height"] = round(h_h * 0.45, 2)
        h["upper_height"] = round(h_h * 0.55, 2)
        h["has_tween"]    = True
        h["tween_estimated"] = True
    else:
        h["has_tween"]    = False
        h["lower_height"] = h_h
        h["upper_height"] = 0


def parse_vessel_payload_from_json_data(data: dict, slug: str) -> Optional[dict]:
    """
    Build the visualizer-shaped vessel dict from one parsed _data.json blob.
    Returns None when the JSON doesn't expose enough info (no LOA).
    """
    content = "\n".join(
        data.get("chapters", {}).get("1_vessel_details", {}).get("content", [])
    )

    # ── Principal dimensions ────────────────────────────────────────────────
    loa     = _fnum(_find(r"Length (?:over ?all|overall)[:\s]+(?:abt\.?\s*)?([\d.,]+)\s*m", content))
    breadth = _fnum(_find(r"Breadth (?:moulded|over ?all|[\(]?overall[\s/]+moulded[\)]?)[:\s]+(?:abt\.?\s*)?([\d.,]+)\s*m", content))
    if not breadth:
        breadth = _fnum(_find(r"Breadth[:\s]+(?:abt\.?\s*)?([\d.,]+)\s*m", content))
    depth   = _fnum(_find(r"Depth (?:moulded|to main deck|Moulded)[:\s]+(?:abt\.?\s*)?([\d.,]+)\s*m", content))
    draft   = _fnum(_find(r"(?:Summer [Dd]raught|Summer draft|Max\.?\s*draft\s*\(SSW\)|max\.?\s*draft)[^:\n]*?[:\s]+(?:abt\.?\s*)?([\d.,]+)\s*m", content))

    if not loa:
        return None

    holds_n = int(_find(r"(?:Number of |No\. of )?[Hh]olds/[Hh]atches[:\s]+(\d+)", content) or "1")
    cap_raw = _find(r"Hold [Cc]apacity[:\s]+(?:abt\.?\s*|[Aa]bout\s*)?([\d,]+)\s*m", content)
    hold_cap = None
    if cap_raw:
        c = _fnum(cap_raw)
        if c and c > 100:
            hold_cap = c
        elif c:
            try:
                hold_cap = float(cap_raw.replace(",", ""))
            except Exception:
                pass

    has_tween = bool(re.search(
        r"\btween\s+deck\b|T/D\s+(?:pontoon|dim|height|area|surface)|"
        r"above\s*T/D|below\s*T/D|Hold below/above|tweendeck",
        content, re.I,
    ))

    global_lower_h = None
    global_upper_h = None
    m = re.search(r"Tanktop/Tweendeck[^:\n]*:\s*([\d.]+)\s*/\s*([\d.]+)", content, re.I)
    if m:
        global_lower_h = _fnum(m.group(1))
        global_upper_h = _fnum(m.group(2))
    if not global_lower_h:
        m1 = re.search(r"--\s*Tank\s*top\s*:\s*([\d.]+)\s*m\s*$", content, re.I | re.M)
        m2 = re.search(r"--\s*Tween\s*deck\s*:\s*([\d.]+)\s*m\s*$", content, re.I | re.M)
        if m1 and m2:
            global_lower_h = _fnum(m1.group(1))
            global_upper_h = _fnum(m2.group(1))

    hold_dims: dict[int, dict] = {}
    for blk in re.finditer(
        r"#(\d+)\s*(?:Hold\s*[&+]\s*)?T/D\s*Dimensions?\s*:(.*?)(?=#\d|\Z)",
        content, re.S | re.I,
    ):
        hid = int(blk.group(1))
        dm = re.search(r"([\d.,]+)\s*x\s*([\d.,]+)", blk.group(2))
        if dm:
            hold_dims[hid] = {"length": _fnum(dm.group(1)), "breadth": _fnum(dm.group(2))}

    hold_heights: dict[int, float] = {}
    m = re.search(
        r"#1\s*/\s*2\s*/\s*3\s*hold height\s*:\s*([\d.]+)\s*/\s*([\d.]+)\s*/\s*([\d.]+)",
        content, re.I,
    )
    if m:
        for i, v_str in enumerate([m.group(1), m.group(2), m.group(3)], 1):
            hold_heights[i] = _fnum(v_str)
    if not hold_heights:
        m = re.search(r"#?1\s*/\s*2\s*hold height\s*:\s*([\d.]+)\s*/\s*([\d.]+)", content, re.I)
        if m:
            hold_heights[1] = _fnum(m.group(1))
            hold_heights[2] = _fnum(m.group(2))
    if not hold_heights:
        m = re.search(r"[Hh]old height[:\s]+([\d.]+)\s*m", content)
        if m:
            hold_heights[1] = _fnum(m.group(1))

    per_hold_tween: dict[int, dict] = {}
    bab = re.search(r"Hold below/above T/D.*?:(.*?)(?=\n[A-Z#]|\Z)", content, re.S | re.I)
    if bab:
        block = bab.group(1)
        for hm in re.finditer(
            r"#(\d+)\s*:\s*([\d.]+(?:\s*/\s*[\d.]+(?:\s*or\s*[\d.]+\s*/\s*[\d.]+)*)?)",
            block,
        ):
            hid = int(hm.group(1))
            pairs = re.findall(r"([\d.]+)\s*/\s*([\d.]+)", hm.group(2))
            if pairs:
                target = hold_heights.get(hid)
                if target:
                    best = min(pairs, key=lambda p: abs(_fnum(p[0]) + _fnum(p[1]) - target))
                else:
                    best = pairs[0]
                per_hold_tween[hid] = {"lower": _fnum(best[0]), "upper": _fnum(best[1])}

    holds: list[dict] = []
    if hold_dims:
        for hid in sorted(hold_dims.keys()):
            h = dict(hold_dims[hid])
            h["id"] = hid
            h_h = (
                hold_heights.get(hid)
                or _fnum(_find(r"[Hh]old height[:\s]+([\d.]+)\s*m", content))
                or (depth or 9.0)
            )
            h["height"] = h_h
            _apply_tween(h, hid, per_hold_tween, global_lower_h, global_upper_h, has_tween)
            holds.append(h)
    else:
        raw = _find(
            r"Hold (?:dimension|dim)[s\s]*(?:\([LlBbHh\s/x]+\))?[:\s]+(?:abt\.?\s*)?([\d.,]+\s*x\s*[\d.,]+(?:\s*x\s*[\d.,]+)?)\s*m",
            content,
        )
        if raw:
            parts = [_fnum(p) for p in re.split(r"\s*x\s*", raw) if _fnum(p)]
            h_h = parts[2] if len(parts) > 2 else (hold_heights.get(1) or depth or 9.0)
            h = {
                "id": 1,
                "length": parts[0] if parts else None,
                "breadth": parts[1] if len(parts) > 1 else None,
                "height": h_h,
            }
            _apply_tween(h, 1, per_hold_tween, global_lower_h, global_upper_h, has_tween)
            holds.append(h)
        else:
            ld = _find(r"Lower Hold Dimensions?[:\s]+([\d.,]+)\s*x\s*([\d.,]+)", content)
            if ld:
                pts = [_fnum(p) for p in re.split(r"\s*x\s*", ld)]
                h_h = hold_heights.get(1) or depth or 9.0
                h = {
                    "id": 1,
                    "length": pts[0],
                    "breadth": pts[1] if len(pts) > 1 else breadth,
                    "height": h_h,
                }
                _apply_tween(h, 1, per_hold_tween, global_lower_h, global_upper_h, has_tween)
                holds.append(h)

    if not holds:
        h_h = hold_heights.get(1) or (depth or round(loa * 0.09, 1))
        h = {
            "id": 1,
            "length": round(loa * 0.60, 1),
            "breadth": round((breadth or loa * 0.13) * 0.85, 1),
            "height": h_h,
            "estimated": True,
        }
        _apply_tween(h, 1, per_hold_tween, global_lower_h, global_upper_h, has_tween)
        holds.append(h)

    if holds_n > 1 and len(holds) < holds_n and len(holds) == 1:
        base = holds[0]
        seg_L = round(base["length"] / holds_n * 0.90, 1)
        holds = [dict(base, id=i + 1, length=seg_L) for i in range(holds_n)]

    return {
        "id":                   slug,
        "name":                 display_name_from_slug(slug),
        "loa":                  loa,
        "breadth":              breadth or round(loa * 0.13, 1),
        "depth":                depth or round(loa * 0.09, 1),
        "draft":                draft,
        "holds_count":          holds_n,
        "hold_capacity_m3":     hold_cap,
        "has_tween":            has_tween,
        "double_bottom_height": 1.5,
        "holds":                holds,
    }


def parse_vessel_payload_from_filesystem(slug: str) -> Optional[dict]:
    """Read & parse output/vessels/<slug>/<slug>_data.json. Returns None on miss."""
    path = os.path.join(_VESSEL_FS_DIR, slug, f"{slug}_data.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception as exc:
        logger.warning(f"[cargo.holds] could not read {path}: {exc}")
        return None
    return parse_vessel_payload_from_json_data(data, slug)


def list_filesystem_slugs() -> list[str]:
    """Return all slugs that have a parseable filesystem _data.json file."""
    if not os.path.isdir(_VESSEL_FS_DIR):
        return []
    out = []
    for name in sorted(os.listdir(_VESSEL_FS_DIR)):
        if os.path.isfile(os.path.join(_VESSEL_FS_DIR, name, f"{name}_data.json")):
            out.append(name)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# DB-backed reads/writes
# ─────────────────────────────────────────────────────────────────────────────

def serialize_db_payload(vessel: Vessel) -> Optional[dict]:
    """
    Build a visualizer-shaped dict purely from the Vessel DB row's columns.
    Returns None if the row has no holds_json (caller falls back).
    """
    if not vessel or not vessel.holds_json:
        return None
    try:
        payload = json.loads(vessel.holds_json)
    except Exception as exc:
        logger.warning(
            f"[cargo.holds] could not deserialize holds_json for vessel "
            f"#{vessel.id} ({vessel.name}): {exc}"
        )
        return None

    # The stored JSON is the full visualizer payload — but we always rewrite
    # id/name from the live row so renames don't require a re-cache.
    payload["id"]   = slugify_vessel_name(vessel.name)
    payload["name"] = vessel.name

    # Surface column-level overrides (capacity, db height) when set.
    if vessel.hold_capacity_m3 is not None:
        payload["hold_capacity_m3"] = vessel.hold_capacity_m3
    if vessel.double_bottom_height is not None:
        payload["double_bottom_height"] = vessel.double_bottom_height

    return payload


def cache_payload_to_db(vessel: Vessel, payload: dict) -> None:
    """Persist a visualizer payload to the Vessel DB row (commits)."""
    if not vessel or not payload:
        return
    vessel.holds_json = json.dumps(payload)
    if payload.get("hold_capacity_m3") is not None:
        vessel.hold_capacity_m3 = payload["hold_capacity_m3"]
    if payload.get("double_bottom_height") is not None:
        vessel.double_bottom_height = payload["double_bottom_height"]
    db.session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_vessel_meta(vessel: Vessel) -> Optional[dict]:
    """
    Return the full visualizer payload for *vessel*, hitting these sources
    in order:

      1. ``Vessel.holds_json`` (DB)
      2. Filesystem ``output/vessels/<slug>/<slug>_data.json``
         (with auto-cache to DB on success)

    Returns None when both sources come up empty.
    """
    payload = serialize_db_payload(vessel)
    if payload:
        return payload

    slug = slugify_vessel_name(vessel.name)
    fs_payload = parse_vessel_payload_from_filesystem(slug)
    if fs_payload:
        try:
            cache_payload_to_db(vessel, fs_payload)
            logger.info(
                f"[cargo.holds] cached filesystem payload to DB for vessel "
                f"#{vessel.id} ({vessel.name})"
            )
        except Exception as exc:
            db.session.rollback()
            logger.warning(f"[cargo.holds] cache write failed: {exc}")
        return fs_payload

    return None


def get_holds(vessel: Vessel) -> list[dict]:
    """Convenience wrapper — returns just the holds list, [] when missing."""
    payload = get_vessel_meta(vessel)
    return (payload or {}).get("holds", []) or []


def set_vessel_holds(
    vessel: Vessel,
    holds: list[dict],
    *,
    hold_capacity_m3: Optional[float] = None,
    double_bottom_height: Optional[float] = None,
    loa: Optional[float] = None,
    breadth: Optional[float] = None,
    depth: Optional[float] = None,
    draft: Optional[float] = None,
) -> dict:
    """
    Manually set / overwrite a vessel's hold geometry in the DB.

    All hold dims are validated as positive numbers; missing tween heights
    default to 0 / hold-height; missing has_tween falls back to the presence
    of a non-zero upper_height.

    Returns the freshly-stored visualizer payload.

    Raises ValueError when *holds* contains an invalid entry.
    """
    if not isinstance(holds, list) or not holds:
        raise ValueError("holds must be a non-empty list of hold dicts")

    cleaned = []
    for i, raw in enumerate(holds):
        if not isinstance(raw, dict):
            raise ValueError(f"hold #{i} is not a dict")
        L = float(raw.get("length") or 0)
        B = float(raw.get("breadth") or 0)
        H = float(raw.get("height") or 0)
        if L <= 0 or B <= 0 or H <= 0:
            raise ValueError(
                f"hold #{i}: length/breadth/height must be > 0 "
                f"(got {L}/{B}/{H})"
            )
        upper = float(raw.get("upper_height") or 0)
        lower = float(raw.get("lower_height") or 0)
        has_tween = bool(raw.get("has_tween", upper > 0))
        if has_tween:
            if lower <= 0:
                lower = max(H - upper, H * 0.45)
            if upper <= 0:
                upper = max(H - lower, H * 0.55)
        else:
            lower = H
            upper = 0
        cleaned.append({
            "id":              int(raw.get("id") or (i + 1)),
            "length":          L,
            "breadth":         B,
            "height":          H,
            "has_tween":       has_tween,
            "lower_height":    lower,
            "upper_height":    upper,
            "estimated":       bool(raw.get("estimated", False)),
            "tween_estimated": bool(raw.get("tween_estimated", False)),
        })

    # Pull existing payload (if any) so we preserve unrelated fields like
    # loa/breadth/depth that the visualizer needs for its hull rendering.
    base = serialize_db_payload(vessel) or {}

    payload = {
        "id":                   slugify_vessel_name(vessel.name),
        "name":                 vessel.name,
        "loa":                  loa     if loa     is not None else base.get("loa"),
        "breadth":              breadth if breadth is not None else base.get("breadth"),
        "depth":                depth   if depth   is not None else base.get("depth"),
        "draft":                draft   if draft   is not None else base.get("draft"),
        "holds_count":          len(cleaned),
        "hold_capacity_m3":     (hold_capacity_m3
                                 if hold_capacity_m3 is not None
                                 else base.get("hold_capacity_m3")),
        "has_tween":            any(h["has_tween"] for h in cleaned),
        "double_bottom_height": (double_bottom_height
                                 if double_bottom_height is not None
                                 else base.get("double_bottom_height", 1.5)),
        "holds":                cleaned,
    }

    # Sensible numeric defaults when neither caller nor cached data
    # supplied the principal dimensions.
    if not payload["loa"]:
        payload["loa"] = round(max(h["length"] for h in cleaned) * 1.6, 1)
    if not payload["breadth"]:
        payload["breadth"] = round(max(h["breadth"] for h in cleaned) * 1.05, 1)
    if not payload["depth"]:
        payload["depth"] = round(max(h["height"] for h in cleaned) * 1.1, 1)

    cache_payload_to_db(vessel, payload)
    logger.info(
        f"[cargo.holds] stored {len(cleaned)} holds for vessel "
        f"#{vessel.id} ({vessel.name})"
    )
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# Listing for the visualizer sidebar
# ─────────────────────────────────────────────────────────────────────────────

def _auto_register_filesystem_vessels(client_id: str) -> int:
    """
    For every filesystem slug that has no matching DB Vessel row in this
    client, create one (with the parsed payload cached in holds_json).

    This is the migration step that bridges the legacy filesystem-only
    visualizer with the new DB-backed cargo flow.  It runs idempotently
    on every list_vessel_metas() call and exits early when there's nothing
    new to register.
    """
    fs_slugs = list_filesystem_slugs()
    if not fs_slugs:
        return 0

    db_vessels = Vessel.query.filter_by(client_id=client_id).all()
    db_slugs = {slugify_vessel_name(v.name) for v in db_vessels}

    new_slugs = [s for s in fs_slugs if s not in db_slugs]
    if not new_slugs:
        return 0

    created = 0
    for slug in new_slugs:
        payload = parse_vessel_payload_from_filesystem(slug)
        if not payload:
            continue
        v = Vessel(
            client_id=client_id,
            name=display_name_from_slug(slug),
            holds_json=json.dumps(payload),
            hold_capacity_m3=payload.get("hold_capacity_m3"),
            double_bottom_height=payload.get("double_bottom_height", 1.5),
        )
        db.session.add(v)
        created += 1

    if created:
        db.session.commit()
        logger.info(
            f"[cargo.holds] auto-registered {created} filesystem vessel(s) "
            f"into DB for client '{client_id}'"
        )
    return created


def list_vessel_metas(client_id: str) -> list[dict]:
    """
    Return all visualizer-shaped vessel dicts for *client_id*, sourced from
    the DB and (transparently) the legacy filesystem cache.

    Side-effects:
      * Filesystem-only vessels not yet in the DB are auto-registered on
        first call so the cargo upload flow works without manual seeding.
      * Duplicate Vessel rows that slugify to the same name are collapsed
        into one entry, preferring the row with hold data populated.
        (The documents pipeline often creates several Vessel rows per
        physical vessel because of name spellings — "MV X", "M.V. X",
        "X" — which all slugify identically; without deduping the cargo
        sidebar shows the same ship 3-4 times.)
    """
    try:
        _auto_register_filesystem_vessels(client_id)
    except Exception as exc:
        db.session.rollback()
        logger.warning(f"[cargo.holds] auto-register skipped: {exc}")

    by_slug: dict[str, dict] = {}
    for v in (Vessel.query
              .filter_by(client_id=client_id)
              .order_by(Vessel.name.asc())
              .all()):
        payload = get_vessel_meta(v)
        if not payload:
            continue
        slug = payload["id"]
        existing = by_slug.get(slug)
        if existing is None:
            by_slug[slug] = payload
            continue
        # Prefer the row that has holds; if both have holds, keep the
        # earlier one (alphabetical name sort).
        existing_has = bool(existing.get("holds"))
        new_has      = bool(payload.get("holds"))
        if new_has and not existing_has:
            by_slug[slug] = payload

    return list(by_slug.values())


def find_vessel_by_slug(client_id: str, slug: str) -> Optional[Vessel]:
    """
    Return the Vessel DB row whose slugified name matches *slug*.

    When multiple rows share the same slug (common when the documents
    pipeline has created variants like "MV Aurora", "M.V. Aurora",
    "Aurora"), prefer the row whose ``holds_json`` is populated so the
    cargo flow lands on the row that actually has hold geometry.
    """
    matches = [
        v for v in Vessel.query.filter_by(client_id=client_id).all()
        if slugify_vessel_name(v.name) == slug
    ]
    if not matches:
        return None
    with_holds = [v for v in matches if v.holds_json]
    if with_holds:
        return with_holds[0]
    # No row in the duplicate cluster has cached holds — try filesystem
    # one more time, cache to the first match, and return it.
    fs_payload = parse_vessel_payload_from_filesystem(slug)
    if fs_payload:
        try:
            cache_payload_to_db(matches[0], fs_payload)
            logger.info(
                f"[cargo.holds] late-cached filesystem payload to vessel "
                f"#{matches[0].id} ({matches[0].name}) for slug '{slug}'"
            )
        except Exception:
            db.session.rollback()
    return matches[0]
