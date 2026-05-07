#!/usr/bin/env python3
"""
Multi-bin 3D packer for vessel cargo holds.

Problem
-------
Given a vessel's holds (each potentially split into a lower hold and a
tween-deck level) and a list of cargo items with dimensions + weights,
find a placement for each item that:

1. fits within hold bounds (length × breadth × height per level),
2. does not collide with any other placed item,
3. is supported (rests on the tank top, the tween deck, or another
   stackable item),
4. respects weight ceilings (per-hold and vessel-total),
5. distributes weight across holds so the vessel stays in trim.

Items that cannot be placed under these constraints are returned in an
``unplaced`` list with a diagnostic reason — they're the overflow that
the user has to either reassign to another vessel/trip or accept will
ride a later voyage.

Algorithm
---------
Extreme-point heuristic adapted from Crainic et al. (2008) "Extreme
Point-Based Heuristics for Three-Dimensional Bin Packing".  For each
item (sorted by volume desc, weight desc), the packer enumerates every
(bin, extreme_point, rotation) triple, scores them, and picks the best.

Scoring (lower = better):
    y * 10                    prefer low placements (fills bottom first)
  + x * 1                     prefer fore-end (deterministic ordering)
  + z * 0.1                   tie-breaker
  + balance_penalty * 1000    crushing penalty if this push the hold
                              above its proportional weight target

The balance penalty makes the algorithm prefer spreading mass across
all available holds rather than topping off the first one before
starting the next — the "vessel stays level" requirement.

Coordinate system per bin (origin at one corner):
    x = forward (along length, +X = bow)
    y = up      (vertical, +Y = up)
    z = athwart (across beam, +Z = port)

Output is JSON-serializable so it can be cached on
CargoManifest.layout_json and rendered by the visualizer.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

EPS = 1e-6


# ─────────────────────────────────────────────────────────────────────────────
# Tuning constants (caller-overridable)
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_BALANCE_TOLERANCE = 0.30
"""Hold weight is allowed to exceed its proportional target by this much
before the balance penalty kicks in.  0.30 = 30% over is still 'fine'."""

DEFAULT_DWAT_USABLE_FRACTION = 0.95
"""When deriving per-hold weight ceilings from DWAT, leave 5% margin for
bunkers / stores / ballast."""

DEFAULT_FALLBACK_DENSITY_KG_PER_M3 = 700.0
"""When DWAT isn't supplied, fall back to a typical break-bulk density
(~700 kg/m³) to compute per-hold weight ceilings."""

MIN_FOOTPRINT_SUPPORT_FRACTION = 0.0
"""Minimum fraction of an item's bottom face that must rest on a stackable
surface.  0.0 = any contact qualifies (permissive, MVP).  Tighten to e.g.
0.6 once the basic flow is validated."""

DEFAULT_CLEARANCE_M = 0.10
"""Lateral gap (m) the packer leaves between adjacent items for lashing,
dunnage, and worker access.  0.10 ≈ 10 cm = typical break-bulk practice."""

DEFAULT_STACK_CLEARANCE_M = 0.02
"""Vertical dunnage gap (m) between vertically stacked items — typically
much smaller than lateral clearance because dunnage is just thin timber."""

DEFAULT_X_SPREAD_ZONES = 5
"""Number of equal-width zones along each bin's length axis used to
encourage even distribution.  Higher = finer spread; lower = more
clustering tolerance."""


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Bin:
    """One packable level (a whole hold, or one level of a tween-decked hold)."""
    bin_id: str               # "1", "1_lower", "2_tween" …
    hold_id: int
    level: Optional[str]      # "lower" | "tween" | None for non-tweendeck holds
    length: float             # along ship (x)
    width: float              # athwart (z)
    height: float             # vertical (y)
    max_weight_kg: float

    placements: list["PlacedItem"] = field(default_factory=list)
    extreme_points: list[tuple[float, float, float]] = \
        field(default_factory=lambda: [(0.0, 0.0, 0.0)])

    @property
    def volume(self) -> float:
        return self.length * self.width * self.height

    @property
    def current_weight(self) -> float:
        return sum(p.weight_kg for p in self.placements)

    @property
    def used_volume(self) -> float:
        return sum(p.l * p.w * p.h for p in self.placements)

    @property
    def fill_pct(self) -> float:
        return 100.0 * self.used_volume / max(self.volume, EPS)


@dataclass
class PlacedItem:
    """One item placed inside a Bin."""
    item_position: int        # original index in the items list (==CargoItem.position)
    item_id: str              # PL / NR. PLIST identifier
    bin_id: str
    hold_id: int
    level: Optional[str]
    x: float
    y: float
    z: float
    l: float                  # length after rotation (along x)
    w: float                  # width  after rotation (along z)
    h: float                  # height (along y) — never rotated
    rotation_deg: int         # 0 or 90
    weight_kg: float
    can_stack: bool
    is_pinned: bool = False   # True ⇒ user-pinned, packer won't relocate

    def to_dict(self) -> dict:
        return {
            "item_position": self.item_position,
            "item_id":       self.item_id,
            "bin_id":        self.bin_id,
            "hold_id":       self.hold_id,
            "level":         self.level,
            "x":             round(self.x, 4),
            "y":             round(self.y, 4),
            "z":             round(self.z, 4),
            "l":             round(self.l, 4),
            "w":             round(self.w, 4),
            "h":             round(self.h, 4),
            "rotation_deg":  self.rotation_deg,
            "weight_kg":     self.weight_kg,
            "is_pinned":     bool(self.is_pinned),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Bin construction
# ─────────────────────────────────────────────────────────────────────────────

def build_bins(
    holds: list[dict],
    vessel_dwat_kg: Optional[float] = None,
) -> list[Bin]:
    """
    Convert the cargo-visualizer's hold dicts (as returned by
    /cargo/api/vessels) into packable Bin objects.

    Each hold becomes 1 bin if it has no tween deck, or 2 bins
    (lower + tween) if it does.

    Per-bin max_weight_kg is computed as a proportional share of either:
      * 95% of the vessel's DWAT  (when known), or
      * a fallback density (700 kg/m³) × bin volume   (when DWAT missing)
    The caller can post-mutate `bin.max_weight_kg` to encode hand-tuned
    SWL values from a vessel's loading manual.
    """
    bins: list[Bin] = []

    for h in holds:
        hid = int(h.get("id", len(bins) + 1))
        L = float(h.get("length") or 0.0)
        W = float(h.get("breadth") or 0.0)
        H = float(h.get("height") or 0.0)

        if not (L > 0 and W > 0 and H > 0):
            logger.warning(f"Skipping hold {hid}: zero/missing dimensions {L}x{W}x{H}")
            continue

        has_tween    = bool(h.get("has_tween"))
        lower_h      = float(h.get("lower_height") or 0.0)
        upper_h      = float(h.get("upper_height") or 0.0)

        if has_tween and lower_h > 0 and upper_h > 0:
            bins.append(Bin(
                bin_id=f"{hid}_lower", hold_id=hid, level="lower",
                length=L, width=W, height=lower_h, max_weight_kg=0.0,
            ))
            bins.append(Bin(
                bin_id=f"{hid}_tween", hold_id=hid, level="tween",
                length=L, width=W, height=upper_h, max_weight_kg=0.0,
            ))
        else:
            bins.append(Bin(
                bin_id=str(hid), hold_id=hid, level=None,
                length=L, width=W, height=H, max_weight_kg=0.0,
            ))

    # Proportional weight ceilings
    total_volume = sum(b.volume for b in bins)
    if vessel_dwat_kg and vessel_dwat_kg > 0:
        usable = vessel_dwat_kg * DEFAULT_DWAT_USABLE_FRACTION
        for b in bins:
            b.max_weight_kg = usable * (b.volume / max(total_volume, EPS))
    else:
        for b in bins:
            b.max_weight_kg = b.volume * DEFAULT_FALLBACK_DENSITY_KG_PER_M3

    return bins


# ─────────────────────────────────────────────────────────────────────────────
# Geometric helpers
# ─────────────────────────────────────────────────────────────────────────────

def _box_overlaps(p: PlacedItem,
                  x: float, y: float, z: float,
                  l: float, w: float, h: float,
                  lateral_clearance: float = 0.0,
                  stack_clearance: float = 0.0) -> bool:
    """
    True when boxes overlap OR are within the configured clearance gap.

    Lateral gap (X / Z axes) reserves space for lashings and worker access;
    stack gap (Y axis) reserves room for thin dunnage between vertically
    adjacent items.  Both default to zero for unit-test simplicity.
    """
    return not (
        x + l + lateral_clearance <= p.x + EPS or
        p.x + p.l + lateral_clearance <= x + EPS or
        y + h + stack_clearance   <= p.y + EPS or
        p.y + p.h + stack_clearance <= y + EPS or
        z + w + lateral_clearance <= p.z + EPS or
        p.z + p.w + lateral_clearance <= z + EPS
    )


def _footprint_overlap_area(p: PlacedItem,
                            x: float, z: float,
                            l: float, w: float) -> float:
    """Overlap area on the XZ plane between a candidate footprint and p's top."""
    ox = max(0.0, min(p.x + p.l, x + l) - max(p.x, x))
    oz = max(0.0, min(p.z + p.w, z + w) - max(p.z, z))
    return ox * oz


def _is_supported(bin_: Bin,
                  x: float, y: float, z: float,
                  l: float, w: float) -> bool:
    """
    True when the item's bottom face has either:
      • y == 0 (resting on tank top / tween deck), or
      • adequate contact with the top face(s) of one or more placed items.
    """
    if y < EPS:
        return True
    needed_area = (l * w) * MIN_FOOTPRINT_SUPPORT_FRACTION
    contact_area = 0.0
    for p in bin_.placements:
        if abs(p.y + p.h - y) > EPS:
            continue
        contact_area += _footprint_overlap_area(p, x, z, l, w)
        if contact_area > needed_area + EPS and contact_area > EPS:
            return True
    return contact_area > EPS  # any contact counts when MIN_FOOTPRINT_SUPPORT_FRACTION = 0


def _all_supports_can_stack(bin_: Bin,
                            x: float, y: float, z: float,
                            l: float, w: float) -> bool:
    """Every placed item directly under the candidate must permit stacking."""
    if y < EPS:
        return True
    for p in bin_.placements:
        if abs(p.y + p.h - y) > EPS:
            continue
        if _footprint_overlap_area(p, x, z, l, w) > EPS:
            if not p.can_stack:
                return False
    return True


def _fits(bin_: Bin,
          x: float, y: float, z: float,
          l: float, w: float, h: float,
          lateral_clearance: float = 0.0,
          stack_clearance: float = 0.0) -> bool:
    """Within bin bounds and not colliding (incl. clearance) with placed items?"""
    if x < -EPS or y < -EPS or z < -EPS:
        return False
    if (x + l > bin_.length + EPS or
        y + h > bin_.height + EPS or
        z + w > bin_.width  + EPS):
        return False
    for p in bin_.placements:
        if _box_overlaps(p, x, y, z, l, w, h,
                         lateral_clearance=lateral_clearance,
                         stack_clearance=stack_clearance):
            return False
    return True


def _orientations(item: dict) -> Iterable[tuple[float, float, float, int]]:
    """Yield (l, w, h, rotation_deg) — only horizontal rotation per spec."""
    L = float(item["length_m"])
    W = float(item["width_m"])
    H = float(item["height_m"])
    yield (L, W, H, 0)
    if item.get("can_rotate_horizontal", True) and abs(L - W) > EPS:
        yield (W, L, H, 90)


# ─────────────────────────────────────────────────────────────────────────────
# Pinned-placement seeding
# ─────────────────────────────────────────────────────────────────────────────

def _seed_pinned_placements(
    bins: list[Bin],
    pinned_placements: list[dict],
    clearance_m: float,
    stack_clearance_m: float,
) -> tuple[list[PlacedItem], set[int]]:
    """Inject user-pinned placements into the bin set as fixed obstacles.

    Each pinned placement dict must carry: ``hold_id``, ``level``,
    ``x``, ``y``, ``z``, ``l``, ``w``, ``h``, ``rotation_deg``,
    ``weight_kg``, ``can_stack``, ``item_position``, ``item_id``.

    Side-effects on `bins`:
      • A PlacedItem is appended to the matching bin's placements list
        (so subsequent collision / weight / support checks treat it as
        present).
      • The bin's extreme_points list is updated: the (0, 0, 0) default
        is dropped if it's now inside the pinned AABB, and three new
        EPs are generated from each pinned placement's far corners
        (with the configured clearance gaps) so the regular search
        loop can place items adjacent to pins.

    Returns (seeded_items, pinned_positions) where pinned_positions
    is the set of item_position values that should be skipped by the
    main pack loop.
    """
    by_bin_key: dict[tuple[int, Optional[str]], Bin] = {
        (b.hold_id, b.level): b for b in bins
    }
    seeded: list[PlacedItem] = []
    pinned_positions: set[int] = set()

    for p in pinned_placements or []:
        hold_id = int(p.get("hold_id"))
        level   = p.get("level")
        # Tolerate a None level on a tween-decked hold by preferring the
        # lower-level bin; mirrors the level_height fallback in collisions.py.
        bin_ = by_bin_key.get((hold_id, level))
        if bin_ is None:
            # Try the other level if the requested one isn't present.
            for (hid, lvl), b in by_bin_key.items():
                if hid == hold_id and (level is None or lvl is None):
                    bin_ = b
                    break
        if bin_ is None:
            logger.warning(
                f"[packer] Pinned placement for item_pos="
                f"{p.get('item_position')} references unknown hold "
                f"{hold_id} / level {level!r} — skipping pin"
            )
            continue

        x = float(p.get("x") or 0.0)
        y = float(p.get("y") or 0.0)
        z = float(p.get("z") or 0.0)
        l = float(p.get("l") or 0.0)
        w = float(p.get("w") or 0.0)
        h = float(p.get("h") or 0.0)

        if l <= 0 or w <= 0 or h <= 0:
            logger.warning(
                f"[packer] Pinned placement item_pos="
                f"{p.get('item_position')} has zero/missing dims "
                f"({l}×{w}×{h}) — skipping pin"
            )
            continue

        placed = PlacedItem(
            item_position=int(p.get("item_position", -1)),
            item_id=str(p.get("item_id", "")),
            bin_id=bin_.bin_id,
            hold_id=bin_.hold_id,
            level=bin_.level,
            x=x, y=y, z=z, l=l, w=w, h=h,
            rotation_deg=int(p.get("rotation_deg") or 0),
            weight_kg=float(p.get("weight_kg") or 0.0),
            can_stack=bool(p.get("can_stack", True)),
            is_pinned=True,
        )
        bin_.placements.append(placed)
        seeded.append(placed)
        if placed.item_position >= 0:
            pinned_positions.add(placed.item_position)

        # Drop the default (0, 0, 0) EP if the pin now occupies it.
        bin_.extreme_points = [
            ep for ep in bin_.extreme_points
            if not (ep[0] >= x - EPS and ep[0] <  x + l - EPS and
                    ep[1] >= y - EPS and ep[1] <  y + h - EPS and
                    ep[2] >= z - EPS and ep[2] <  z + w - EPS)
        ]
        # Generate three new EPs at the pin's far faces (with clearance).
        new_eps = [
            (x + l + clearance_m,        y,                              z),
            (x,                          y + h + stack_clearance_m,      z),
            (x,                          y,                              z + w + clearance_m),
        ]
        for ep in new_eps:
            ex, ey, ez = ep
            if (ex < bin_.length - EPS and
                ey < bin_.height - EPS and
                ez < bin_.width  - EPS and
                ep not in bin_.extreme_points):
                bin_.extreme_points.append(ep)
        # Also seed (0, 0, 0) if it wasn't dropped — keeps near-corner
        # placements available even when one pin sits in the middle.
        if (0.0, 0.0, 0.0) not in bin_.extreme_points:
            # Only re-add if it isn't inside ANY current placement.
            inside_any = any(
                (0.0 >= q.x - EPS and 0.0 <  q.x + q.l - EPS and
                 0.0 >= q.y - EPS and 0.0 <  q.y + q.h - EPS and
                 0.0 >= q.z - EPS and 0.0 <  q.z + q.w - EPS)
                for q in bin_.placements
            )
            if not inside_any:
                bin_.extreme_points.append((0.0, 0.0, 0.0))
        bin_.extreme_points.sort(key=lambda e: (e[1], e[0], e[2]))

    return seeded, pinned_positions


# ─────────────────────────────────────────────────────────────────────────────
# Main pack routine
# ─────────────────────────────────────────────────────────────────────────────

def pack_items(
    holds: list[dict],
    items: list[dict],
    vessel_dwat_kg: Optional[float] = None,
    balance_tolerance: float = DEFAULT_BALANCE_TOLERANCE,
    clearance_m: float = DEFAULT_CLEARANCE_M,
    stack_clearance_m: float = DEFAULT_STACK_CLEARANCE_M,
    x_spread_zones: int = DEFAULT_X_SPREAD_ZONES,
    pinned_placements: Optional[list[dict]] = None,
) -> dict:
    """
    Pack *items* into *holds* and return a JSON-serializable layout.

    Parameters
    ----------
    holds : list of hold dicts (as produced by /cargo/api/vessels)
        Required keys per hold: id, length, breadth, height.
        Optional keys: has_tween, lower_height, upper_height.
    items : list of item dicts (as produced by cargo.parser)
        Required keys: position, item_id, length_m, width_m, height_m,
                       gross_weight_kg.
        Optional keys: can_stack (default True),
                       can_rotate_horizontal (default True).
    vessel_dwat_kg : optional vessel deadweight in kilograms.
        When provided, per-hold weight ceilings are derived from it.
    balance_tolerance : how much each hold may exceed its proportional
        weight target before the balance-penalty term activates.
    clearance_m : lateral gap (X/Z axes) reserved between adjacent items
        for lashing, dunnage, and worker access.  Default 0.10 m (10 cm).
    stack_clearance_m : vertical gap (Y axis) between vertically stacked
        items for thin dunnage.  Default 0.02 m (2 cm).
    x_spread_zones : number of zones along each bin's length used to
        encourage spread.  Higher values produce more even distribution
        across the hold rather than corner-piling.
    pinned_placements : optional list of fixed placements the packer must
        respect.  Each dict carries hold_id, level, x/y/z (corner),
        l/w/h (rotated dims), rotation_deg, weight_kg, can_stack,
        item_position, item_id.  Items whose ``position`` matches a
        pinned placement are skipped by the search loop and the
        pinned placement is included in the returned ``placements``
        with ``is_pinned=True``.  Used by the manual-move UI.

    Returns
    -------
    dict with keys:
        placements:        list of PlacedItem.to_dict() — one per packed item
        unplaced:          list of {item_position, item_id, reason}
        bins:              list of bin descriptors (id, dims, capacity, fill, weight)
        weight_per_hold:   {hold_id: total_kg}
        fill_pct_per_hold: {hold_id: percent}
        balance_score:     0–100 (higher = more even weight distribution)
        total_weight_kg:   sum of placed-item weights
        total_volume_m3:   sum of placed-item volumes
    """
    bins = build_bins(holds, vessel_dwat_kg=vessel_dwat_kg)
    if not bins:
        return _empty_result(items, "No usable holds defined for this vessel")

    # Seed any user-pinned placements as fixed obstacles BEFORE the search
    # loop — their weight + footprint count toward the bin state and they
    # appear in the final `placements` output unchanged.
    pinned_seeded, pinned_positions = _seed_pinned_placements(
        bins,
        pinned_placements or [],
        clearance_m=clearance_m,
        stack_clearance_m=stack_clearance_m,
    )

    total_weight = sum(float(it.get("gross_weight_kg") or 0.0) for it in items)
    total_volume = sum(float(b.volume) for b in bins)

    targets = {
        b.bin_id: total_weight * (b.volume / max(total_volume, EPS))
        for b in bins
    }

    # Per-bin volume occupied per X-zone — used to push new items into
    # the least-filled zone instead of corner-piling.
    n_zones = max(1, int(x_spread_zones))
    zone_volume: dict[str, list[float]] = {b.bin_id: [0.0] * n_zones for b in bins}

    def _zone_of(b: Bin, x: float, l: float) -> int:
        center = x + l / 2.0
        idx = int(n_zones * center / max(b.length, EPS))
        return max(0, min(idx, n_zones - 1))

    # Sort items: largest volume first, heaviest tie-break — heavy/large
    # pieces have fewer feasible placements so we want them down first.
    items_sorted = sorted(
        items,
        key=lambda it: (
            -float(it.get("volume_m3")
                   or it["length_m"] * it["width_m"] * it["height_m"]),
            -float(it.get("gross_weight_kg") or 0.0),
        ),
    )

    # The pinned placements already live in their bins as PlacedItems —
    # bring them into the result list too so the output contains every
    # placed item.  The search loop below only handles unpinned items.
    placements: list[PlacedItem] = list(pinned_seeded)
    unplaced: list[dict] = []

    # Initialise zone-volume tracking for any pinned placements so the
    # spread heuristic accounts for space they already occupy.
    for p in pinned_seeded:
        b = next((bb for bb in bins if bb.bin_id == p.bin_id), None)
        if b is not None:
            zone_volume[b.bin_id][_zone_of(b, p.x, p.l)] += p.l * p.w * p.h

    for item in items_sorted:
        if int(item.get("position", -1)) in pinned_positions:
            # User has pinned this item already — leave it untouched.
            continue
        weight = float(item.get("gross_weight_kg") or 0.0)
        L = float(item["length_m"])
        W = float(item["width_m"])
        H = float(item["height_m"])
        max_dim = max(L, W)

        candidates = []
        rejected_reasons: set[str] = set()

        for b in bins:
            # Up-front feasibility:
            if H > b.height + EPS:
                rejected_reasons.add("too tall for any hold")
                continue
            # No orientation of this item fits the bin's length × width footprint?
            longest, shortest = max(L, W), min(L, W)
            bin_long, bin_short = max(b.length, b.width), min(b.length, b.width)
            if longest > bin_long + EPS or shortest > bin_short + EPS:
                rejected_reasons.add("longest side exceeds hold dimensions")
                continue
            # Weight ceiling
            if b.current_weight + weight > b.max_weight_kg + EPS:
                rejected_reasons.add("exceeds remaining weight in available holds")
                continue

            for l, w, h, rot in _orientations(item):
                if l > b.length + EPS or w > b.width + EPS or h > b.height + EPS:
                    continue
                for ep in b.extreme_points:
                    x, y, z = ep
                    if not _fits(b, x, y, z, l, w, h,
                                 lateral_clearance=clearance_m,
                                 stack_clearance=stack_clearance_m):
                        continue
                    if not _is_supported(b, x, y, z, l, w):
                        continue
                    if not _all_supports_can_stack(b, x, y, z, l, w):
                        continue

                    # ── Score components ──────────────────────────────────────
                    # (1) Gravity — prefer placements low in the bin
                    gravity = y * 10.0

                    # (2) X-spread — push items into the LEAST-filled zone
                    #     along the bin's length so they distribute evenly
                    #     instead of corner-piling.  Quadratic on volume so
                    #     once a zone gets some items, the next placement
                    #     strongly prefers a different zone.
                    zone_idx = _zone_of(b, x, l)
                    zone_load = zone_volume[b.bin_id][zone_idx]
                    spread_pen = (zone_load ** 1.5) * 8.0

                    # (3) Z-centering — gentle pull toward the bin's
                    #     athwart centerline so cargo stays close to the
                    #     longitudinal axis (better for transverse stability).
                    z_dev = abs((z + w / 2) - b.width / 2) / max(b.width, EPS)
                    z_pen = z_dev * 4.0

                    # (4) Weight balance across holds (existing)
                    weight_after = b.current_weight + weight
                    target = targets.get(b.bin_id, 0.0)
                    if target > 0 and weight_after > target * (1.0 + balance_tolerance):
                        excess = (weight_after - target) / target
                        balance_pen = excess * 1000.0
                    else:
                        balance_pen = 0.0

                    score = gravity + spread_pen + z_pen + balance_pen
                    candidates.append((score, b, x, y, z, l, w, h, rot))

        if not candidates:
            reason = (
                ", ".join(sorted(rejected_reasons))
                if rejected_reasons else
                "no extreme point in any hold accepts this item"
            )
            unplaced.append({
                "item_position": item["position"],
                "item_id":       item["item_id"],
                "reason":        reason,
            })
            continue

        candidates.sort(key=lambda c: c[0])
        _, b, x, y, z, l, w, h, rot = candidates[0]

        p = PlacedItem(
            item_position=item["position"],
            item_id=item["item_id"],
            bin_id=b.bin_id,
            hold_id=b.hold_id,
            level=b.level,
            x=x, y=y, z=z,
            l=l, w=w, h=h,
            rotation_deg=rot,
            weight_kg=weight,
            can_stack=bool(item.get("can_stack", True)),
        )
        b.placements.append(p)
        placements.append(p)

        # Update X-zone occupancy for spread tracking
        zone_volume[b.bin_id][_zone_of(b, x, l)] += l * w * h

        # Update the bin's extreme points.  New EPs are bumped by the
        # configured clearance gap so subsequent items naturally start
        # with the lashing/dunnage spacing built in — no need for a
        # post-pass to widen things.
        consumed = (x, y, z)
        new_eps = [
            (x + l + clearance_m,        y,                              z),
            (x,                          y + h + stack_clearance_m,      z),
            (x,                          y,                              z + w + clearance_m),
        ]
        b.extreme_points = [
            ep for ep in b.extreme_points if ep != consumed
        ]
        for ep in new_eps:
            ex, ey, ez = ep
            if (ex < b.length - EPS and
                ey < b.height - EPS and
                ez < b.width  - EPS and
                ep not in b.extreme_points):
                b.extreme_points.append(ep)
        # Keep EPs sorted by (y, x, z) so subsequent items prefer low/fore
        b.extreme_points.sort(key=lambda e: (e[1], e[0], e[2]))

    # ── Aggregates ──────────────────────────────────────────────────────────
    weight_per_hold: dict[int, float] = {}
    for p in placements:
        weight_per_hold[p.hold_id] = weight_per_hold.get(p.hold_id, 0.0) + p.weight_kg

    fill_pct_per_hold: dict[int, float] = {}
    weight_target_per_hold: dict[int, float] = {}
    for b in bins:
        fill_pct_per_hold[b.hold_id] = (
            fill_pct_per_hold.get(b.hold_id, 0.0) + b.fill_pct
        )
        weight_target_per_hold[b.hold_id] = (
            weight_target_per_hold.get(b.hold_id, 0.0) + targets[b.bin_id]
        )
    # When a hold has 2 bins (tween-decked), fill_pct was double-counted
    # — replace with per-hold average from the two halves.
    bins_per_hold: dict[int, int] = {}
    for b in bins:
        bins_per_hold[b.hold_id] = bins_per_hold.get(b.hold_id, 0) + 1
    for hid, count in bins_per_hold.items():
        if count > 1:
            fill_pct_per_hold[hid] = fill_pct_per_hold[hid] / count

    balance_score = _compute_balance_score(weight_per_hold, weight_target_per_hold)

    bin_descs = [
        {
            "bin_id":        b.bin_id,
            "hold_id":       b.hold_id,
            "level":         b.level,
            "length":        round(b.length, 3),
            "width":         round(b.width, 3),
            "height":        round(b.height, 3),
            "volume_m3":     round(b.volume, 3),
            "max_weight_kg": round(b.max_weight_kg, 1),
            "fill_pct":      round(b.fill_pct, 2),
            "current_weight_kg": round(b.current_weight, 1),
        }
        for b in bins
    ]

    return {
        "placements":         [p.to_dict() for p in placements],
        "unplaced":           unplaced,
        "bins":               bin_descs,
        "weight_per_hold":    {str(k): round(v, 1) for k, v in weight_per_hold.items()},
        "weight_target_per_hold":
                              {str(k): round(v, 1) for k, v in weight_target_per_hold.items()},
        "fill_pct_per_hold":  {str(k): round(v, 2) for k, v in fill_pct_per_hold.items()},
        "balance_score":      round(balance_score, 2),
        "total_weight_kg":    round(sum(p.weight_kg for p in placements), 1),
        "total_volume_m3":    round(sum(p.l * p.w * p.h for p in placements), 3),
        "placed_count":       len(placements),
        "unplaced_count":     len(unplaced),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _compute_balance_score(actual: dict[int, float],
                           target: dict[int, float]) -> float:
    """
    100 = each hold's actual weight matches its proportional target exactly.
    Drops with the average absolute deviation from target, normalized by
    the largest target.  Floor at 0.
    """
    if not target:
        return 100.0
    max_target = max(target.values()) or 1.0
    deviations = []
    for hid, t in target.items():
        a = actual.get(hid, 0.0)
        deviations.append(abs(a - t) / max_target)
    avg_dev = sum(deviations) / len(deviations)
    return max(0.0, round(100.0 * (1.0 - avg_dev), 2))


def _empty_result(items: list[dict], reason: str) -> dict:
    """Return a result that puts every item in `unplaced` with one reason."""
    return {
        "placements":         [],
        "unplaced": [
            {
                "item_position": it["position"],
                "item_id":       it["item_id"],
                "reason":        reason,
            }
            for it in items
        ],
        "bins":               [],
        "weight_per_hold":    {},
        "weight_target_per_hold": {},
        "fill_pct_per_hold":  {},
        "balance_score":      0.0,
        "total_weight_kg":    0.0,
        "total_volume_m3":    0.0,
        "placed_count":       0,
        "unplaced_count":     len(items),
    }
