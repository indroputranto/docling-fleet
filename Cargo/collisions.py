#!/usr/bin/env python3
"""
Collision and fit validation for manually-placed cargo items.

The auto-packer (cargo/packer.py) handles fit + collision implicitly
during its extreme-point search.  When the user drags an item to a new
position in the 3D visualizer, we need a standalone routine to answer:
"can this item live at (hold, level, x, y, z, rotation) without
overlapping anything else and without poking out of the hold?"

Coordinate system (mirrors cargo/packer.py)
-------------------------------------------
Per-bin origin at the corner closest to (forward, down, starboard):
    x = forward  (along ship length, +X = bow)        — bin.length
    y = up       (vertical)                           — bin.height
    z = athwart  (across beam, +Z = port)             — bin.width

A bin is one hold OR one level of a tween-decked hold:
    bin.length = hold.length
    bin.width  = hold.breadth
    bin.height = hold.lower_height (for level="lower"),
                 hold.upper_height (for level="tween"),
                 hold.height       (for non-tween holds, level=None)

An item's stored position (x, y, z) is its lower-near-starboard CORNER,
so its AABB is:
    [x, x + l] × [y, y + h] × [z, z + w]
where (l, w, h) are the dimensions AFTER rotation.

Rotation
--------
Only 0° or 90° around the vertical (Y) axis is supported, matching the
auto-packer.  At 0°, l = item.length_m, w = item.width_m.  At 90°,
they swap.  Height is never rotated.

Usage
-----
    ok, reason = validate_placement(
        item=item_dict,
        hold=hold_dict,
        level="lower",
        x_m=2.0, y_m=0.0, z_m=1.5,
        rotation_deg=0,
        others=[other_placement_dicts...],
    )
"""

from __future__ import annotations

from typing import Optional

EPS = 1e-6

# Reasonable defaults — match the packer's defaults.  Callers (the move
# API) should pass clearance_m=0.0 for manual moves so the user can place
# items hard against each other if they want; the packer enforces
# clearance only on auto-pack.
DEFAULT_LATERAL_CLEARANCE_M = 0.0
DEFAULT_STACK_CLEARANCE_M   = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Geometry helpers
# ─────────────────────────────────────────────────────────────────────────────

def rotated_dims(item: dict, rotation_deg: int) -> tuple[float, float, float]:
    """Return (l, w, h) given the item's raw dimensions and a horizontal rotation.

    `l` is the X-axis (forward) span, `w` is the Z-axis (athwart) span,
    `h` is the Y-axis (vertical) span.  Height is never rotated.
    """
    L = float(item.get("length_m") or 0.0)
    W = float(item.get("width_m") or 0.0)
    H = float(item.get("height_m") or 0.0)
    rot = int(rotation_deg or 0) % 360
    if rot == 90 or rot == 270:
        return (W, L, H)
    return (L, W, H)


def level_height(hold: dict, level: Optional[str]) -> Optional[float]:
    """Return the usable vertical span for the requested level, or None
    if the level isn't available on this hold."""
    has_tween = bool(hold.get("has_tween"))
    lower_h   = float(hold.get("lower_height") or 0.0)
    upper_h   = float(hold.get("upper_height") or 0.0)
    full_h    = float(hold.get("height") or 0.0)

    if not has_tween or level in (None, ""):
        # Non-tween holds OR explicit "no level" → use the full hold height.
        if has_tween and lower_h > 0 and upper_h > 0:
            # Caller passed level=None to a tween-decked hold.  Default to
            # the lower level so we don't silently let items float through
            # the tween deck.
            return lower_h
        return full_h if full_h > 0 else None
    if level == "lower":
        return lower_h if lower_h > 0 else None
    if level == "tween":
        return upper_h if upper_h > 0 else None
    return None


def boxes_overlap(
    a_x: float, a_y: float, a_z: float,
    a_l: float, a_w: float, a_h: float,
    b_x: float, b_y: float, b_z: float,
    b_l: float, b_w: float, b_h: float,
    lateral_clearance: float = 0.0,
    stack_clearance:   float = 0.0,
) -> bool:
    """AABB overlap check.  Returns True if the two boxes intersect.

    Mirrors cargo/packer.py:_box_overlaps but takes explicit dims so it
    can be called against either a PlacedItem or a raw placement dict.
    """
    return not (
        a_x + a_l + lateral_clearance <= b_x + EPS or
        b_x + b_l + lateral_clearance <= a_x + EPS or
        a_y + a_h + stack_clearance   <= b_y + EPS or
        b_y + b_h + stack_clearance   <= a_y + EPS or
        a_z + a_w + lateral_clearance <= b_z + EPS or
        b_z + b_w + lateral_clearance <= a_z + EPS
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def validate_placement(
    *,
    item: dict,
    hold: dict,
    level: Optional[str],
    x_m: float,
    y_m: float,
    z_m: float,
    rotation_deg: int,
    others: list[dict],
    lateral_clearance_m: float = DEFAULT_LATERAL_CLEARANCE_M,
    stack_clearance_m:   float = DEFAULT_STACK_CLEARANCE_M,
    ignore_placement_id: Optional[int] = None,
) -> tuple[bool, Optional[str]]:
    """Validate a manual placement.

    Parameters
    ----------
    item : dict
        Must carry length_m, width_m, height_m.  Optional:
        can_rotate_horizontal (default True).
    hold : dict
        Must carry length, breadth, height.  Optional: has_tween,
        lower_height, upper_height.
    level : str or None
        "lower" / "tween" for tween-decked holds, None for single-level.
    x_m, y_m, z_m : float
        Lower-near-starboard corner of the item in bin coordinates.
    rotation_deg : int
        0 or 90 (around the vertical axis).
    others : list[dict]
        OTHER placements to check against — each must include
        hold_id, level, x_m, y_m, z_m, l, w, h (and an optional
        ``id`` for the ignore filter).  Only those matching THIS
        hold + level are actually compared.
    ignore_placement_id : int, optional
        Skip the entry in `others` whose ``id`` equals this — used when
        validating a move of an already-placed item (don't collide
        against your old position).

    Returns
    -------
    (ok: bool, reason: str | None)
        On success: (True, None).  On failure: (False, reason) where
        reason is a short human-readable string suitable for surfacing
        in the UI.
    """
    # ── Rotation feasibility ──────────────────────────────────────────────
    rot = int(rotation_deg or 0) % 360
    if rot not in (0, 90, 180, 270):
        return False, f"unsupported rotation {rotation_deg}° (only 0° and 90° allowed)"
    if rot in (180, 270):
        return False, "180°/270° rotations are not supported"
    if rot == 90 and not bool(item.get("can_rotate_horizontal", True)):
        return False, "this item cannot be rotated horizontally"

    l, w, h = rotated_dims(item, rot)
    if l <= 0 or w <= 0 or h <= 0:
        return False, "item has zero or missing dimensions"

    # ── Hold geometry ─────────────────────────────────────────────────────
    bin_length = float(hold.get("length") or 0.0)
    bin_width  = float(hold.get("breadth") or 0.0)
    bin_height = level_height(hold, level)

    if bin_length <= 0 or bin_width <= 0:
        return False, "hold has zero or missing dimensions"
    if bin_height is None or bin_height <= 0:
        return False, f"level {level!r} not available on this hold"

    # ── Bounds check ──────────────────────────────────────────────────────
    if x_m < -EPS or y_m < -EPS or z_m < -EPS:
        return False, (
            f"placement is out of bounds (negative coordinate "
            f"x={x_m:.3f} y={y_m:.3f} z={z_m:.3f})"
        )
    if x_m + l > bin_length + EPS:
        return False, (
            f"item exceeds hold length "
            f"({x_m + l:.2f} > {bin_length:.2f} m along the ship)"
        )
    if z_m + w > bin_width + EPS:
        return False, (
            f"item exceeds hold breadth "
            f"({z_m + w:.2f} > {bin_width:.2f} m across the beam)"
        )
    if y_m + h > bin_height + EPS:
        return False, (
            f"item exceeds hold height for level "
            f"{level or 'main'!r} ({y_m + h:.2f} > {bin_height:.2f} m)"
        )

    # ── Collision check against other placements in the SAME bin ─────────
    target_hold_id = hold.get("id")
    for other in others:
        if ignore_placement_id is not None and other.get("id") == ignore_placement_id:
            continue
        if other.get("hold_id") != target_hold_id:
            continue
        # Different level inside the same hold = different bin = no collision
        if (other.get("level") or None) != (level or None):
            continue

        ox = float(other.get("x_m") or 0.0)
        oy = float(other.get("y_m") or 0.0)
        oz = float(other.get("z_m") or 0.0)
        ol = float(other.get("l")   or other.get("length_m") or 0.0)
        ow = float(other.get("w")   or other.get("width_m")  or 0.0)
        oh = float(other.get("h")   or other.get("height_m") or 0.0)

        if ol <= 0 or ow <= 0 or oh <= 0:
            # Other placement has no usable footprint → skip rather than
            # falsely report a collision.
            continue

        if boxes_overlap(
            x_m, y_m, z_m, l, w, h,
            ox, oy, oz, ol, ow, oh,
            lateral_clearance=lateral_clearance_m,
            stack_clearance=stack_clearance_m,
        ):
            other_label = other.get("item_id") or other.get("id") or "?"
            return False, f"overlaps item {other_label}"

    return True, None


# ─────────────────────────────────────────────────────────────────────────────
# Balance recomputation
# ─────────────────────────────────────────────────────────────────────────────

def recompute_balance_score(
    weight_per_hold: dict[int | str, float],
    weight_target_per_hold: dict[int | str, float],
) -> float:
    """Mirror of cargo.packer._compute_balance_score, exposed publicly.

    The packer's helper is private; we duplicate it here so the API can
    recompute the balance on the fly after a manual move (or for a
    preview, without touching the DB) without reaching into the packer
    module's private namespace.
    """
    if not weight_target_per_hold:
        return 100.0
    targets = {str(k): float(v) for k, v in weight_target_per_hold.items()}
    actuals = {str(k): float(v) for k, v in weight_per_hold.items()}
    max_target = max(targets.values()) or 1.0
    deviations = []
    for hid, t in targets.items():
        a = actuals.get(hid, 0.0)
        deviations.append(abs(a - t) / max_target)
    avg_dev = sum(deviations) / max(len(deviations), 1)
    return max(0.0, round(100.0 * (1.0 - avg_dev), 2))
