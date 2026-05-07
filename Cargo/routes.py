#!/usr/bin/env python3
"""
Cargo Blueprint — packing-list upload, preview/edit, packing, and visualizer API.

Access: same cms_token cookie auth used by the chat / documents pipeline,
but cargo logic is otherwise self-contained — this module DOES NOT import
from the documents/ pipeline.

Routes:
  POST   /cargo/api/vessels/<vessel_slug>/manifests/upload
                                          → accept xlsx/xls, parse, save as draft,
                                            redirect to preview
  GET    /cargo/manifests/<id>/preview    → editable items + trip metadata (HTML)
  POST   /cargo/api/manifests/<id>/items  → bulk-update item attributes
  POST   /cargo/api/manifests/<id>/save   → commit + archive prior, run packer,
                                            redirect to /cargo
  POST   /cargo/api/manifests/<id>/repack → re-run packer with current items
  DELETE /cargo/api/manifests/<id>        → discard manifest
  GET    /cargo/api/vessels/<vessel_slug>/manifest
                                          → active manifest + layout for the
                                            visualizer
  GET    /cargo/api/manifests/<id>        → full manifest detail (items + layout)

Vessel resolution:
  The visualizer's vessel sidebar lists filesystem-keyed vessel slugs (from
  the legacy output/vessels/ directory).  Cargo manifests live in the DB
  and link to a Vessel row; we resolve the slug → Vessel by matching on
  the slugified name, auto-creating the Vessel row when the user first
  uploads cargo for a vessel that exists on disk but not yet in the DB.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
from datetime import datetime, date, timezone
from typing import Any, Optional

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    jsonify, abort, flash, current_app,
)
from werkzeug.utils import secure_filename

from models import (
    db,
    Vessel,
    VesselTrip,
    CargoManifest,
    CargoItem,
    CargoPlacement,
)
from cargo.parser import parse_packing_list, ParseResult
from cargo.packer import pack_items
from cargo import object_storage as spaces
from cargo import holds as holds_resolver

# Stable color palette assigned to manifests in upload-order so the user
# can visually distinguish items from different packing lists in both the
# 3D visualizer and the right sidebar.  Mirrors the JS palette the
# visualizer falls back to when no color is provided.
MANIFEST_COLOR_PALETTE: list[str] = [
    "#3b82f6",  # blue
    "#10b981",  # emerald
    "#f59e0b",  # amber
    "#ec4899",  # pink
    "#8b5cf6",  # violet
    "#ef4444",  # red
    "#14b8a6",  # teal
    "#f97316",  # orange
    "#a855f7",  # purple
    "#22c55e",  # green
    "#06b6d4",  # cyan
    "#eab308",  # yellow
]


def _manifest_color(index: int) -> str:
    """Stable color for a manifest based on its position in the active list."""
    return MANIFEST_COLOR_PALETTE[index % len(MANIFEST_COLOR_PALETTE)]


def _active_manifests_for_vessel(client_id: str, vessel: Vessel) -> list[CargoManifest]:
    """All active manifests for *vessel*, sorted by upload time (deterministic
    color assignment)."""
    return (
        CargoManifest.query
        .filter_by(client_id=client_id, vessel_id=vessel.id, status="active")
        .order_by(CargoManifest.uploaded_at.asc(), CargoManifest.id.asc())
        .all()
    )

logger = logging.getLogger(__name__)

cargo_bp = Blueprint(
    "cargo",
    __name__,
    template_folder="templates",
)

ALLOWED_EXTENSIONS = {"xlsx", "xlsm", "xls"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024   # 25 MB — packing lists rarely exceed a few MB


# ─────────────────────────────────────────────────────────────────────────────
# Auth (mirrors app.py's _check_chat_cookie — duplicated to avoid coupling)
# ─────────────────────────────────────────────────────────────────────────────

def _check_chat_cookie() -> Optional[dict]:
    """Validate the cms_token cookie.  Returns decoded payload or None."""
    from auth import _decode_token
    import jwt as _jwt

    token = request.cookies.get("cms_token")
    if not token:
        return None
    try:
        return _decode_token(token)
    except _jwt.PyJWTError:
        return None


def _require_auth():
    """Abort with JSON 401 when the request is unauthenticated."""
    payload = _check_chat_cookie()
    if not payload:
        abort(401, description="Authentication required")
    return payload


def _client_id_from_request() -> str:
    """Reuse app.get_client_id_from_request via late import to avoid cycles."""
    from app import get_client_id_from_request
    return get_client_id_from_request()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# Slugify lives in the resolver — re-export for clarity / template use
_slugify_vessel_name = holds_resolver.slugify_vessel_name


def _resolve_vessel(client_id: str, vessel_slug: str) -> Vessel:
    """
    Resolve a filesystem-style vessel slug to a Vessel DB row, auto-creating
    one if the slug exists in the legacy output/vessels/ directory but no
    DB Vessel row exists yet.

    When multiple Vessel rows share the same slug (the documents pipeline
    can create several variants — "MV Aurora", "M.V. Aurora", "Aurora" —
    that all slugify to the same string), this routine prefers the row
    whose ``holds_json`` is populated.  That avoids attaching a manifest
    to a "shell" Vessel that has no hold geometry while a sibling does.
    """
    # Re-use the resolver's slug-aware lookup (same dedupe + holds preference).
    v = holds_resolver.find_vessel_by_slug(client_id, vessel_slug)
    if v:
        return v

    # Auto-create from filesystem slug (best-effort display name).  The
    # auto-register step in list_vessel_metas() usually beats us to it,
    # but this handles the case where the user uploads cargo before
    # ever loading /cargo for the first time.
    display_name = holds_resolver.display_name_from_slug(vessel_slug)
    v = Vessel(
        client_id=client_id,
        name=display_name,
    )
    db.session.add(v)
    db.session.flush()
    logger.info(
        f"[cargo] Auto-created Vessel row for slug '{vessel_slug}' → id={v.id}"
    )

    # If filesystem JSON exists for this slug, populate holds immediately
    # so the user doesn't have to run Edit Holds before Save & Pack.
    fs_payload = holds_resolver.parse_vessel_payload_from_filesystem(vessel_slug)
    if fs_payload:
        try:
            holds_resolver.cache_payload_to_db(v, fs_payload)
            logger.info(
                f"[cargo] Pre-populated holds for new vessel #{v.id} "
                f"from filesystem JSON"
            )
        except Exception as exc:
            db.session.rollback()
            logger.warning(f"[cargo] holds pre-population failed: {exc}")
    return v


def _load_holds_for_vessel(client_id: str, vessel_slug: str) -> list[dict]:
    """
    Look up hold geometry for *vessel_slug* via the cargo.holds resolver.

    Source-of-truth is Vessel.holds_json in the DB; falls back to legacy
    filesystem JSON in dev environments and caches that result back to
    the DB on first hit.  Returns [] only when BOTH the DB and the
    filesystem are silent — in which case the caller surfaces the
    "set holds first" error.

    Robustness: when a vessel cluster has duplicate rows, find_vessel_by_slug
    already prefers the holds-bearing row.  As a last resort we also try
    raw filesystem parsing using the slug, even if the matching DB Vessel
    came back empty.  This catches the edge case where holds_json got
    cleared / corrupted but the filesystem JSON is still on disk.
    """
    vessel = holds_resolver.find_vessel_by_slug(client_id, vessel_slug)
    if vessel:
        holds = holds_resolver.get_holds(vessel)
        if holds:
            return holds

    # Last-resort raw filesystem parse — bypass the Vessel row entirely.
    fs_payload = holds_resolver.parse_vessel_payload_from_filesystem(vessel_slug)
    if fs_payload and fs_payload.get("holds"):
        if vessel:
            try:
                holds_resolver.cache_payload_to_db(vessel, fs_payload)
            except Exception:
                db.session.rollback()
        return fs_payload["holds"]

    return []


def _vessel_dwat_kg_for(client_id: str, vessel: Vessel) -> Optional[float]:
    """Best-effort DWAT lookup. Returns kilograms or None."""
    if not vessel or not vessel.dwat:
        return None
    try:
        # vessel.dwat is a free-form string, e.g. "12,500 t" or "8400"
        s = str(vessel.dwat).lower().replace(",", "")
        m = re.search(r"([\d.]+)", s)
        if not m:
            return None
        val = float(m.group(1))
        # Heuristic: if the value parses < 1000, assume tonnes; otherwise kg
        if val < 100_000:
            return val * 1000.0   # tonnes → kg
        return val
    except Exception:
        return None


def _parse_iso_date(s: Any) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s)).date()
    except Exception:
        return None


def _safe_filename(name: str) -> str:
    return secure_filename(name) or "packing_list.xlsx"


# ─────────────────────────────────────────────────────────────────────────────
# Upload
# ─────────────────────────────────────────────────────────────────────────────

@cargo_bp.route(
    "/cargo/api/vessels/<vessel_slug>/manifests/upload",
    methods=["POST"],
)
def upload_manifest(vessel_slug: str):
    """
    Receive a packing-list xlsx/xls, parse it, persist a `draft` manifest
    plus its CargoItem rows, and redirect the browser to the preview page.

    The packer is NOT run here — that happens on /save after the user has
    reviewed the items and trip metadata.
    """
    payload = _require_auth()
    client_id = _client_id_from_request()
    user_email = (payload or {}).get("email")

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    upload = request.files["file"]
    if not upload or not upload.filename:
        return jsonify({"error": "No file selected"}), 400

    filename = _safe_filename(upload.filename)
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({
            "error": f"Unsupported file type '.{ext}'. Use .xlsx, .xlsm, or .xls."
        }), 400

    # Read into memory and size-check
    raw = upload.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        return jsonify({
            "error": f"File exceeds {MAX_UPLOAD_BYTES // (1024*1024)} MB limit",
        }), 413
    if not raw:
        return jsonify({"error": "Empty file"}), 400

    # ── Resolve vessel ──────────────────────────────────────────────────────
    try:
        vessel = _resolve_vessel(client_id, vessel_slug)
    except Exception as exc:
        return jsonify({"error": f"Vessel resolution failed: {exc}"}), 400

    # ── Parse ───────────────────────────────────────────────────────────────
    try:
        parsed: ParseResult = parse_packing_list(raw, filename=filename)
    except Exception as exc:
        logger.exception(f"[cargo] parse failed for {filename}")
        return jsonify({"error": f"Could not parse packing list: {exc}"}), 422

    if not parsed.items:
        return jsonify({
            "error": "No cargo items detected in this file. "
                     "Check that it has a recognizable header row "
                     "(item ID + dimensions + weight)."
        }), 422

    # ── Optional: persist file to DO Spaces (best-effort) ───────────────────
    storage_key = None
    if spaces.is_configured():
        try:
            storage_key = spaces.build_storage_key(client_id, vessel.id, filename)
            spaces.upload_file(
                io.BytesIO(raw),
                storage_key,
                content_type=("application/vnd.openxmlformats-officedocument."
                              "spreadsheetml.sheet" if ext.startswith("xlsx")
                              else "application/vnd.ms-excel"),
                length=len(raw),
            )
        except Exception as exc:
            logger.warning(f"[cargo] DO Spaces upload skipped: {exc}")
            storage_key = None

    # ── Optional trip association from form ────────────────────────────────
    trip_id_raw = request.form.get("trip_id")
    trip_id = None
    if trip_id_raw and trip_id_raw.isdigit():
        trip_id = int(trip_id_raw)

    voyage_label    = request.form.get("voyage_label") or None
    departure_port  = request.form.get("departure_port") or None
    arrival_port    = request.form.get("arrival_port") or None
    departure_date  = _parse_iso_date(request.form.get("departure_date"))

    # ── Persist manifest (draft) ────────────────────────────────────────────
    total_w = sum(float(it.get("gross_weight_kg") or 0.0) for it in parsed.items)
    total_v = sum(float(it.get("volume_m3") or 0.0) for it in parsed.items)

    manifest = CargoManifest(
        client_id=client_id,
        vessel_id=vessel.id,
        trip_id=trip_id,
        filename=filename,
        file_type=parsed.file_type,
        storage_key=storage_key,
        status="draft",
        voyage_label=voyage_label,
        departure_port=departure_port,
        arrival_port=arrival_port,
        departure_date=departure_date,
        total_items=len(parsed.items),
        total_weight_kg=total_w,
        total_volume_m3=total_v,
        uploaded_by=user_email,
    )
    db.session.add(manifest)
    db.session.flush()  # populate manifest.id

    for it in parsed.items:
        db.session.add(CargoItem(
            manifest_id=manifest.id,
            position=int(it["position"]),
            item_id=str(it["item_id"]),
            description=it.get("description"),
            packing_type=it.get("packing_type"),
            length_m=float(it["length_m"]),
            width_m=float(it["width_m"]),
            height_m=float(it["height_m"]),
            volume_m3=float(it.get("volume_m3") or 0.0),
            net_weight_kg=it.get("net_weight_kg"),
            gross_weight_kg=float(it.get("gross_weight_kg") or 0.0),
            imo_flag=bool(it.get("imo_flag")),
            can_stack=bool(it.get("can_stack", True)),
            can_rotate_horizontal=bool(it.get("can_rotate_horizontal", True)),
            raw_row_json=it.get("raw_row_json"),
        ))

    db.session.commit()

    logger.info(
        f"[cargo] Manifest #{manifest.id} created with {len(parsed.items)} items "
        f"for vessel {vessel.name} (slug={vessel_slug}, client={client_id})"
    )

    # The frontend expects JSON with the redirect URL
    return jsonify({
        "manifest_id": manifest.id,
        "preview_url": url_for("cargo.preview_manifest", manifest_id=manifest.id),
        "items_count": len(parsed.items),
        "skipped":     len(parsed.skipped),
    }), 201


# ─────────────────────────────────────────────────────────────────────────────
# Preview / Edit
# ─────────────────────────────────────────────────────────────────────────────

@cargo_bp.route("/cargo/manifests/<int:manifest_id>/preview", methods=["GET"])
def preview_manifest(manifest_id: int):
    """Render the editable preview screen for a draft manifest."""
    if not _check_chat_cookie():
        return redirect(url_for("chat_login", next=request.path))

    client_id = _client_id_from_request()
    manifest = CargoManifest.query.filter_by(
        id=manifest_id, client_id=client_id
    ).first_or_404()

    items = list(manifest.items)
    vessel = manifest.vessel
    trips = (
        VesselTrip.query
        .filter_by(client_id=client_id, vessel_id=vessel.id)
        .order_by(VesselTrip.created_at.desc())
        .all()
    )

    return render_template(
        "cargo/preview.html",
        manifest=manifest,
        items=items,
        vessel=vessel,
        trips=trips,
        client_id=client_id,
    )


@cargo_bp.route("/cargo/api/manifests/<int:manifest_id>/items", methods=["POST"])
def update_items(manifest_id: int):
    """
    Bulk-update CargoItem attributes from the preview screen.

    Body (JSON):
        {
          "items": [
            {"id": 12, "length_m": 3.5, "width_m": 0.6, ...},
            ...
          ],
          "manifest": {"voyage_label": "...", "departure_port": "...",
                       "departure_date": "2026-02-13", "trip_id": 4}
        }

    Only fields present in each item dict are updated; everything else is
    left alone.  Allowed item fields: length_m, width_m, height_m,
    volume_m3, net_weight_kg, gross_weight_kg, can_stack,
    can_rotate_horizontal, imo_flag, description, packing_type.
    """
    _require_auth()
    client_id = _client_id_from_request()
    manifest = CargoManifest.query.filter_by(
        id=manifest_id, client_id=client_id
    ).first_or_404()

    data = request.get_json(silent=True) or {}
    items_payload = data.get("items") or []

    allowed_item_fields = {
        "length_m", "width_m", "height_m", "volume_m3",
        "net_weight_kg", "gross_weight_kg", "imo_flag",
        "can_stack", "can_rotate_horizontal", "description", "packing_type",
        "color_hex",
    }

    items_by_id = {it.id: it for it in manifest.items}
    updated_count = 0

    for entry in items_payload:
        iid = entry.get("id")
        if iid is None or iid not in items_by_id:
            continue
        it = items_by_id[iid]
        for k, v in entry.items():
            if k not in allowed_item_fields:
                continue
            if k in ("imo_flag", "can_stack", "can_rotate_horizontal"):
                setattr(it, k, bool(v))
            elif k in ("description", "packing_type", "color_hex"):
                setattr(it, k, str(v) if v is not None else None)
            else:
                try:
                    setattr(it, k, float(v))
                except (TypeError, ValueError):
                    continue
        # Recompute volume if dims changed and volume not explicitly set
        if "volume_m3" not in entry:
            it.volume_m3 = it.length_m * it.width_m * it.height_m
        updated_count += 1

    # Optional manifest-level fields
    mpl = data.get("manifest") or {}
    if "voyage_label" in mpl:
        manifest.voyage_label = mpl.get("voyage_label") or None
    if "departure_port" in mpl:
        manifest.departure_port = mpl.get("departure_port") or None
    if "arrival_port" in mpl:
        manifest.arrival_port = mpl.get("arrival_port") or None
    if "departure_date" in mpl:
        manifest.departure_date = _parse_iso_date(mpl.get("departure_date"))
    if "trip_id" in mpl:
        v = mpl.get("trip_id")
        manifest.trip_id = int(v) if v not in (None, "", "null") else None

    # Refresh aggregates
    manifest.total_weight_kg = sum(it.gross_weight_kg or 0.0 for it in manifest.items)
    manifest.total_volume_m3 = sum(it.volume_m3 or 0.0 for it in manifest.items)

    db.session.commit()
    return jsonify({"ok": True, "updated": updated_count})


# ─────────────────────────────────────────────────────────────────────────────
# Save (commit + run packer)
# ─────────────────────────────────────────────────────────────────────────────

def _run_joint_packer_for_vessel(
    client_id: str,
    vessel: Vessel,
    holds: list[dict],
    vessel_dwat_kg: Optional[float],
    balance_tolerance: float = 0.30,
) -> tuple[dict, list[CargoManifest]]:
    """
    Run the packer over the **union of items from every active manifest**
    for the vessel.  This is the multi-packing-list case: a single trip
    can carry cargo declared across multiple packing lists, so the
    physical 3D layout has to consider them together — otherwise items
    from different manifests would overlap each other in the holds.

    Side-effects (all atomic in one DB commit by the caller):
      * Each active manifest's CargoPlacement rows are rebuilt with its
        share of the joint result.
      * Each active manifest's `layout_json` is updated to a slice of the
        joint layout containing only its own placements / unplaced.
      * Each manifest gets the joint balance_score (it's a vessel-wide
        property), refreshed placed/unplaced counts, and packed_at.

    Returns (combined_layout, active_manifests).  The combined layout
    carries placements + unplaced from ALL manifests, with each entry
    decorated with `manifest_id` and `color_hex` so the visualizer can
    color-code without an extra lookup.
    """
    active_manifests = _active_manifests_for_vessel(client_id, vessel)

    # Map each item's DB id → its existing CargoPlacement so we can
    # carry pinned placements through into the joint pack as fixed
    # obstacles.  Re-queried fresh so pinning state stays consistent
    # across concurrent edits.
    placement_by_item_id: dict[int, CargoPlacement] = {}
    if active_manifests:
        existing_pl = (
            CargoPlacement.query
            .filter(CargoPlacement.manifest_id.in_([m.id for m in active_manifests]))
            .all()
        )
        for pl in existing_pl:
            placement_by_item_id[pl.item_id] = pl

    # Build the joint items list with global, unique positions so the
    # packer's PlacedItem indexes are unambiguous across manifests.
    # In parallel, build the pinned_placements list keyed to those same
    # global positions so the packer can seed them as obstacles.
    global_items: list[dict] = []
    item_meta: list[tuple[CargoItem, CargoManifest, str]] = []
    color_by_manifest: dict[int, str] = {}
    pinned_placements: list[dict] = []

    from cargo.collisions import rotated_dims  # local import: avoids cycle on module load

    for idx, m in enumerate(active_manifests):
        color = _manifest_color(idx)
        color_by_manifest[m.id] = color
        for it in m.items:
            pos = len(global_items)
            global_items.append({
                "position":              pos,
                "item_id":               it.item_id,
                "length_m":              it.length_m,
                "width_m":               it.width_m,
                "height_m":              it.height_m,
                "volume_m3":             it.volume_m3,
                "gross_weight_kg":       it.gross_weight_kg or 0.0,
                "can_stack":             bool(it.can_stack),
                "can_rotate_horizontal": bool(it.can_rotate_horizontal),
            })
            item_meta.append((it, m, color))

            # Carry pinned overrides through as packer obstacles.
            existing = placement_by_item_id.get(it.id)
            if existing and getattr(existing, "is_pinned", False) and existing.is_placed:
                rl, rw, rh = rotated_dims(
                    {"length_m": it.length_m,
                     "width_m":  it.width_m,
                     "height_m": it.height_m},
                    int(existing.rotation_deg or 0),
                )
                pinned_placements.append({
                    "item_position":  pos,
                    "item_id":        it.item_id,
                    "hold_id":        existing.hold_id,
                    "level":          existing.level,
                    "x":              float(existing.x_m or 0.0),
                    "y":              float(existing.y_m or 0.0),
                    "z":              float(existing.z_m or 0.0),
                    "l":              rl,
                    "w":              rw,
                    "h":              rh,
                    "rotation_deg":   int(existing.rotation_deg or 0),
                    "weight_kg":      float(it.gross_weight_kg or 0.0),
                    "can_stack":      bool(it.can_stack),
                })

    layout = pack_items(
        holds=holds,
        items=global_items,
        vessel_dwat_kg=vessel_dwat_kg,
        balance_tolerance=balance_tolerance,
        pinned_placements=pinned_placements,
    )

    # ── Distribute placements back per-manifest ────────────────────────
    per_m_placed: dict[int, list[dict]] = {m.id: [] for m in active_manifests}
    per_m_unplaced: dict[int, list[dict]] = {m.id: [] for m in active_manifests}

    decorated_placements: list[dict] = []
    decorated_unplaced: list[dict] = []

    for p in layout["placements"]:
        cargo_item, manifest, color = item_meta[p["item_position"]]
        local = dict(p)
        # Store the manifest-local position so the existing per-manifest
        # join logic (placement.item_id ↔ CargoItem.id) keeps working.
        local["item_position"] = cargo_item.position
        local["cargo_item_id"] = cargo_item.id
        local["manifest_id"]   = manifest.id
        local["color_hex"]     = color
        per_m_placed[manifest.id].append(local)
        decorated_placements.append(local)

    for u in layout["unplaced"]:
        cargo_item, manifest, color = item_meta[u["item_position"]]
        local = dict(u)
        local["item_position"] = cargo_item.position
        local["cargo_item_id"] = cargo_item.id
        local["manifest_id"]   = manifest.id
        local["color_hex"]     = color
        per_m_unplaced[manifest.id].append(local)
        decorated_unplaced.append(local)

    now = datetime.now(timezone.utc)

    for m in active_manifests:
        # Wipe old placement rows for this manifest and rebuild.
        CargoPlacement.query.filter_by(manifest_id=m.id).delete()
        db.session.flush()

        items_by_pos = {it.position: it for it in m.items}

        for p in per_m_placed[m.id]:
            target = items_by_pos.get(p["item_position"])
            if not target:
                continue
            db.session.add(CargoPlacement(
                manifest_id=m.id,
                item_id=target.id,
                is_placed=True,
                hold_id=p["hold_id"],
                level=p.get("level"),
                x_m=p["x"], y_m=p["y"], z_m=p["z"],
                rotation_deg=p["rotation_deg"],
                is_pinned=bool(p.get("is_pinned", False)),
            ))
        for u in per_m_unplaced[m.id]:
            target = items_by_pos.get(u["item_position"])
            if not target:
                continue
            db.session.add(CargoPlacement(
                manifest_id=m.id,
                item_id=target.id,
                is_placed=False,
                unplaced_reason=u.get("reason"),
            ))

        slice_layout = {
            "placements":             per_m_placed[m.id],
            "unplaced":               per_m_unplaced[m.id],
            "bins":                   layout["bins"],
            "weight_per_hold":        layout["weight_per_hold"],
            "weight_target_per_hold": layout["weight_target_per_hold"],
            "fill_pct_per_hold":      layout["fill_pct_per_hold"],
            "balance_score":          layout["balance_score"],
            "total_weight_kg":        round(sum(p["weight_kg"] for p in per_m_placed[m.id]), 1),
            "total_volume_m3":        round(sum(p["l"] * p["w"] * p["h"]
                                                 for p in per_m_placed[m.id]), 3),
            "placed_count":           len(per_m_placed[m.id]),
            "unplaced_count":         len(per_m_unplaced[m.id]),
            "color_hex":              color_by_manifest[m.id],
        }

        m.placed_count   = len(per_m_placed[m.id])
        m.unplaced_count = len(per_m_unplaced[m.id])
        m.balance_score  = layout["balance_score"]
        m.layout_json    = json.dumps(slice_layout)
        m.packed_at      = now

    # Decorate the combined layout for the API response
    combined = dict(layout)
    combined["placements"]      = decorated_placements
    combined["unplaced"]        = decorated_unplaced
    combined["color_by_manifest"] = {str(k): v for k, v in color_by_manifest.items()}

    return combined, active_manifests


@cargo_bp.route("/cargo/api/manifests/<int:manifest_id>/save", methods=["POST"])
def save_manifest(manifest_id: int):
    """
    Promote a draft manifest to `active` and run a JOINT pack across every
    active manifest on the same vessel.

    Multiple manifests can be active at once — a single trip may carry
    cargo declared by several packing lists (one per shipper, port, etc.),
    and they all need to fit together physically.  The joint pack
    distributes placements back to each manifest so the visualizer can
    color-code by source.
    """
    _require_auth()
    client_id = _client_id_from_request()
    manifest = CargoManifest.query.filter_by(
        id=manifest_id, client_id=client_id
    ).first_or_404()

    vessel = manifest.vessel
    vessel_slug = _slugify_vessel_name(vessel.name)
    holds = _load_holds_for_vessel(client_id, vessel_slug)

    if not holds:
        manifest.status = "draft"
        manifest.error_message = (
            f"No hold data found for vessel '{vessel.name}'. "
            "Click 'Edit Holds' to define cargo holds first, or upload the "
            "vessel specifications."
        )
        db.session.commit()
        return jsonify({
            "error": manifest.error_message,
            "manifest": manifest.to_dict(),
        }), 400

    dwat_kg = _vessel_dwat_kg_for(client_id, vessel)

    body = request.get_json(silent=True) or {}
    bt = body.get("balance_tolerance")
    try:
        balance_tolerance = float(bt) if bt is not None else 0.30
    except (TypeError, ValueError):
        balance_tolerance = 0.30

    # Promote to active BEFORE packing so the joint packer sees this
    # manifest in the active set.
    manifest.status = "active"
    manifest.error_message = None
    db.session.flush()

    try:
        layout, actives = _run_joint_packer_for_vessel(
            client_id, vessel, holds, dwat_kg,
            balance_tolerance=balance_tolerance,
        )
    except Exception as exc:
        logger.exception(f"[cargo] joint packer failed on save({manifest.id})")
        db.session.rollback()
        return jsonify({"error": f"Packing failed: {exc}"}), 500

    db.session.commit()

    logger.info(
        f"[cargo] Manifest #{manifest.id} active. Joint pack across "
        f"{len(actives)} active manifest(s) for vessel #{vessel.id}: "
        f"placed={layout['placed_count']}, unplaced={layout['unplaced_count']}, "
        f"balance={layout['balance_score']}"
    )

    return jsonify({
        "ok": True,
        "manifest_id": manifest.id,
        "redirect_url": url_for("cargo_visualizer") + f"?vessel={vessel_slug}",
        "layout": layout,
        "active_manifest_count": len(actives),
    })


@cargo_bp.route("/cargo/api/manifests/<int:manifest_id>/repack", methods=["POST"])
def repack_manifest(manifest_id: int):
    """
    Re-run the joint packer for the manifest's vessel (affects ALL active
    manifests on that vessel).  Useful after editing item flags or
    changing the balance tolerance.
    """
    _require_auth()
    client_id = _client_id_from_request()
    manifest = CargoManifest.query.filter_by(
        id=manifest_id, client_id=client_id
    ).first_or_404()

    if manifest.status not in ("active", "draft"):
        return jsonify({"error": f"Cannot repack a {manifest.status} manifest"}), 400

    vessel = manifest.vessel
    vessel_slug = _slugify_vessel_name(vessel.name)
    holds = _load_holds_for_vessel(client_id, vessel_slug)
    if not holds:
        return jsonify({"error": "No hold data available for this vessel"}), 400

    body = request.get_json(silent=True) or {}
    bt = body.get("balance_tolerance")
    try:
        balance_tolerance = float(bt) if bt is not None else 0.30
    except (TypeError, ValueError):
        balance_tolerance = 0.30

    dwat_kg = _vessel_dwat_kg_for(client_id, vessel)

    try:
        layout, _ = _run_joint_packer_for_vessel(
            client_id, vessel, holds, dwat_kg,
            balance_tolerance=balance_tolerance,
        )
    except Exception as exc:
        logger.exception(f"[cargo] joint repack failed on manifest {manifest.id}")
        db.session.rollback()
        return jsonify({"error": f"Repack failed: {exc}"}), 500

    db.session.commit()
    return jsonify({"ok": True, "layout": layout})


# ─────────────────────────────────────────────────────────────────────────────
# Manual placement: move / unpin / balance preview
# ─────────────────────────────────────────────────────────────────────────────

def _vessel_active_placements(client_id: str, vessel: Vessel) -> list[CargoPlacement]:
    """Every active CargoPlacement on a vessel — joined query so we can
    do collision checks across manifests in one fetch."""
    return (
        CargoPlacement.query
        .join(CargoManifest, CargoManifest.id == CargoPlacement.manifest_id)
        .filter(
            CargoManifest.vessel_id == vessel.id,
            CargoManifest.client_id == client_id,
            CargoManifest.status    == "active",
        )
        .all()
    )


def _placement_to_validator_dict(pl: CargoPlacement, item: CargoItem) -> dict:
    """Shape a CargoPlacement + its CargoItem into the dict shape that
    cargo.collisions.validate_placement's `others` arg expects."""
    from cargo.collisions import rotated_dims
    rl, rw, rh = rotated_dims(
        {"length_m": item.length_m, "width_m": item.width_m, "height_m": item.height_m},
        int(pl.rotation_deg or 0),
    )
    return {
        "id":      pl.id,
        "item_id": item.item_id,
        "hold_id": pl.hold_id,
        "level":   pl.level,
        "x_m":     float(pl.x_m or 0.0),
        "y_m":     float(pl.y_m or 0.0),
        "z_m":     float(pl.z_m or 0.0),
        "l":       rl,
        "w":       rw,
        "h":       rh,
    }


def _aggregate_vessel_balance(
    holds: list[dict],
    vessel_dwat_kg: Optional[float],
    placement_snapshot: list[dict],
) -> tuple[dict, dict, float]:
    """Compute (weight_per_hold, weight_target_per_hold, balance_score)
    for a hypothetical state of the vessel.

    Each entry in `placement_snapshot` must carry: ``is_placed``,
    ``hold_id``, and ``weight_kg``.  Targets follow the same volume-
    proportional rule the packer uses, so the score is comparable to
    what /save returns.
    """
    from cargo.packer import build_bins, EPS
    from cargo.collisions import recompute_balance_score

    bins = build_bins(holds, vessel_dwat_kg=vessel_dwat_kg)
    if not bins:
        return {}, {}, 0.0

    total_volume = sum(b.volume for b in bins)
    total_weight = sum(
        float(s.get("weight_kg") or 0.0)
        for s in placement_snapshot if s.get("is_placed")
    )
    target_per_bin = {
        b.bin_id: total_weight * (b.volume / max(total_volume, EPS))
        for b in bins
    }
    weight_target_per_hold: dict[int, float] = {}
    for b in bins:
        weight_target_per_hold[b.hold_id] = (
            weight_target_per_hold.get(b.hold_id, 0.0) + target_per_bin[b.bin_id]
        )
    weight_per_hold: dict[int, float] = {}
    for s in placement_snapshot:
        if not s.get("is_placed"):
            continue
        hid = s.get("hold_id")
        if hid is None:
            continue
        weight_per_hold[hid] = (
            weight_per_hold.get(hid, 0.0) + float(s.get("weight_kg") or 0.0)
        )
    balance = recompute_balance_score(weight_per_hold, weight_target_per_hold)
    return (
        {str(k): round(v, 1) for k, v in weight_per_hold.items()},
        {str(k): round(v, 1) for k, v in weight_target_per_hold.items()},
        balance,
    )


@cargo_bp.route("/cargo/api/placements/<int:placement_id>/move", methods=["POST"])
def move_placement(placement_id: int):
    """Manually move one placement to a new (hold, level, x, y, z, rotation).

    Validates fit + collision against every active placement on the
    same vessel (across manifests).  On success the placement is marked
    ``is_pinned=True`` so the next joint repack will treat it as a fixed
    obstacle and won't relocate it.

    Body (JSON):
        {
          "hold_id":      2,
          "level":        "lower",          # or "tween" or null
          "x_m":          3.5,
          "y_m":          0.0,
          "z_m":          1.2,
          "rotation_deg": 0                 # 0 or 90
        }

    Returns the updated placement plus the recomputed
    weight_per_hold / balance_score so the UI can refresh its readout
    without a separate /balance-preview round-trip.
    """
    from cargo.collisions import validate_placement

    _require_auth()
    client_id = _client_id_from_request()

    pl = (
        CargoPlacement.query
        .join(CargoManifest, CargoManifest.id == CargoPlacement.manifest_id)
        .filter(
            CargoPlacement.id   == placement_id,
            CargoManifest.client_id == client_id,
        )
        .first_or_404()
    )

    manifest = CargoManifest.query.get(pl.manifest_id)
    if not manifest or manifest.status != "active":
        return jsonify({
            "error": "Cannot move a placement on a non-active manifest"
        }), 400

    vessel = manifest.vessel
    vessel_slug = _slugify_vessel_name(vessel.name)
    holds = _load_holds_for_vessel(client_id, vessel_slug)
    if not holds:
        return jsonify({
            "error": "No hold data available for this vessel"
        }), 400

    body = request.get_json(silent=True) or {}
    try:
        new_hold_id      = int(body["hold_id"])
        new_level        = body.get("level") or None
        new_x            = float(body["x_m"])
        new_y            = float(body["y_m"])
        new_z            = float(body["z_m"])
        new_rotation_deg = int(body.get("rotation_deg", 0) or 0)
    except (KeyError, ValueError, TypeError) as exc:
        return jsonify({
            "error": f"Invalid body — required keys: "
                     f"hold_id, x_m, y_m, z_m, rotation_deg ({exc})"
        }), 400

    item = CargoItem.query.get(pl.item_id)
    if not item:
        return jsonify({"error": "Cargo item not found"}), 404

    holds_by_id = {int(h.get("id", i + 1)): h for i, h in enumerate(holds)}
    target_hold = holds_by_id.get(new_hold_id)
    if not target_hold:
        return jsonify({"error": f"Hold {new_hold_id} not found on this vessel"}), 400

    # Build the "others" list from every active placement on the vessel.
    others_pl = _vessel_active_placements(client_id, vessel)
    others_dicts: list[dict] = []
    for opl in others_pl:
        if opl.id == pl.id:
            continue
        if not opl.is_placed:
            continue
        oitem = CargoItem.query.get(opl.item_id)
        if not oitem:
            continue
        others_dicts.append(_placement_to_validator_dict(opl, oitem))

    item_dict = {
        "length_m":              item.length_m,
        "width_m":               item.width_m,
        "height_m":              item.height_m,
        "can_rotate_horizontal": bool(item.can_rotate_horizontal),
    }
    ok, reason = validate_placement(
        item=item_dict,
        hold=target_hold,
        level=new_level,
        x_m=new_x, y_m=new_y, z_m=new_z,
        rotation_deg=new_rotation_deg,
        others=others_dicts,
        ignore_placement_id=pl.id,
    )
    if not ok:
        return jsonify({"ok": False, "error": reason}), 422

    # Apply the move.
    pl.is_placed       = True
    pl.hold_id         = new_hold_id
    pl.level           = new_level
    pl.x_m             = new_x
    pl.y_m             = new_y
    pl.z_m             = new_z
    pl.rotation_deg    = new_rotation_deg
    pl.is_pinned       = True
    pl.unplaced_reason = None
    db.session.flush()

    # Recompute balance from the post-move snapshot.
    snapshot: list[dict] = []
    for opl in _vessel_active_placements(client_id, vessel):
        oitem = CargoItem.query.get(opl.item_id)
        if not oitem:
            continue
        snapshot.append({
            "is_placed":  bool(opl.is_placed),
            "hold_id":    opl.hold_id,
            "weight_kg":  float(oitem.gross_weight_kg or 0.0),
        })
    weight_per_hold, weight_target_per_hold, balance_score = _aggregate_vessel_balance(
        holds,
        _vessel_dwat_kg_for(client_id, vessel),
        snapshot,
    )

    db.session.commit()

    return jsonify({
        "ok":                       True,
        "placement":                pl.to_dict(),
        "weight_per_hold":          weight_per_hold,
        "weight_target_per_hold":   weight_target_per_hold,
        "balance_score":            balance_score,
    })


@cargo_bp.route("/cargo/api/placements/<int:placement_id>/unpin", methods=["POST"])
def unpin_placement(placement_id: int):
    """Clear the manual-override flag so the next joint repack is free to
    relocate this placement.  No /repack is triggered here — the user
    can call /repack separately when they're ready."""
    _require_auth()
    client_id = _client_id_from_request()

    pl = (
        CargoPlacement.query
        .join(CargoManifest, CargoManifest.id == CargoPlacement.manifest_id)
        .filter(
            CargoPlacement.id   == placement_id,
            CargoManifest.client_id == client_id,
        )
        .first_or_404()
    )

    pl.is_pinned = False
    db.session.commit()
    return jsonify({"ok": True, "placement": pl.to_dict()})


@cargo_bp.route(
    "/cargo/api/manifests/<int:manifest_id>/balance-preview", methods=["POST"]
)
def balance_preview(manifest_id: int):
    """Preview balance + collisions for a list of proposed manual moves
    WITHOUT persisting anything.  Used by the drag-mode UI to show a live
    balance score and "would this collide?" feedback while the user is
    still dragging.

    Body (JSON):
        {
          "placements": [
            {"placement_id": 12, "hold_id": 2, "level": "lower",
             "x_m": 3.0, "y_m": 0.0, "z_m": 1.5, "rotation_deg": 0},
            ...
          ]
        }

    Returns:
        {
          "ok":                    bool,    # all proposed moves are valid
          "balance_score":         float,
          "weight_per_hold":       {hold_id: kg},
          "weight_target_per_hold":{hold_id: kg},
          "errors":                [{"placement_id": int, "reason": str}, ...]
        }
    """
    from cargo.collisions import validate_placement, rotated_dims

    _require_auth()
    client_id = _client_id_from_request()

    manifest = CargoManifest.query.filter_by(
        id=manifest_id, client_id=client_id
    ).first_or_404()
    vessel = manifest.vessel
    vessel_slug = _slugify_vessel_name(vessel.name)
    holds = _load_holds_for_vessel(client_id, vessel_slug)
    if not holds:
        return jsonify({"error": "No hold data for this vessel"}), 400

    body     = request.get_json(silent=True) or {}
    proposed = body.get("placements") or []

    # Snapshot every active placement on the vessel along with its
    # CargoItem, then layer the proposed overrides on top.
    all_pl   = _vessel_active_placements(client_id, vessel)
    items_by_pl: dict[int, CargoItem] = {}
    snapshot_by_id: dict[int, dict]   = {}
    for pl in all_pl:
        item = CargoItem.query.get(pl.item_id)
        if not item:
            continue
        items_by_pl[pl.id] = item
        snapshot_by_id[pl.id] = {
            "id":            pl.id,
            "item_id":       item.item_id,
            "is_placed":     bool(pl.is_placed),
            "hold_id":       pl.hold_id,
            "level":         pl.level,
            "x_m":           float(pl.x_m or 0.0),
            "y_m":           float(pl.y_m or 0.0),
            "z_m":           float(pl.z_m or 0.0),
            "rotation_deg":  int(pl.rotation_deg or 0),
            "weight_kg":     float(item.gross_weight_kg or 0.0),
        }

    errors: list[dict] = []
    # Apply overrides
    for pr in proposed:
        pid = pr.get("placement_id")
        if pid not in snapshot_by_id:
            errors.append({"placement_id": pid,
                           "reason": "unknown placement on this vessel"})
            continue
        s = snapshot_by_id[pid]
        try:
            s["hold_id"]      = int(pr["hold_id"])
            s["level"]        = pr.get("level") or None
            s["x_m"]          = float(pr["x_m"])
            s["y_m"]          = float(pr["y_m"])
            s["z_m"]          = float(pr["z_m"])
            s["rotation_deg"] = int(pr.get("rotation_deg", 0) or 0)
            s["is_placed"]    = True
        except (KeyError, ValueError, TypeError) as exc:
            errors.append({"placement_id": pid,
                           "reason": f"malformed proposal ({exc})"})

    holds_by_id = {int(h.get("id", i + 1)): h for i, h in enumerate(holds)}

    # Validate each PROPOSED move against the rest of the snapshot.
    proposed_ids = {pr.get("placement_id") for pr in proposed}
    for pid in proposed_ids:
        s = snapshot_by_id.get(pid)
        if s is None:
            continue
        item = items_by_pl[pid]
        hold = holds_by_id.get(s["hold_id"])
        if not hold:
            errors.append({"placement_id": pid,
                           "reason": f"hold {s['hold_id']} not found"})
            continue
        # Build "others" from snapshot, excluding self.  Use rotated dims
        # so collision boxes match the visualizer.
        others = []
        for oid, o in snapshot_by_id.items():
            if oid == pid or not o["is_placed"]:
                continue
            oitem = items_by_pl[oid]
            ol, ow, oh = rotated_dims(
                {"length_m": oitem.length_m,
                 "width_m":  oitem.width_m,
                 "height_m": oitem.height_m},
                o["rotation_deg"],
            )
            others.append({
                "id":      oid,
                "item_id": o["item_id"],
                "hold_id": o["hold_id"],
                "level":   o["level"],
                "x_m":     o["x_m"],
                "y_m":     o["y_m"],
                "z_m":     o["z_m"],
                "l":       ol, "w": ow, "h": oh,
            })
        item_dict = {
            "length_m":              item.length_m,
            "width_m":               item.width_m,
            "height_m":              item.height_m,
            "can_rotate_horizontal": bool(item.can_rotate_horizontal),
        }
        ok, reason = validate_placement(
            item=item_dict,
            hold=hold,
            level=s["level"],
            x_m=s["x_m"], y_m=s["y_m"], z_m=s["z_m"],
            rotation_deg=s["rotation_deg"],
            others=others,
            ignore_placement_id=pid,
        )
        if not ok:
            errors.append({"placement_id": pid, "reason": reason})

    weight_per_hold, weight_target_per_hold, balance_score = _aggregate_vessel_balance(
        holds,
        _vessel_dwat_kg_for(client_id, vessel),
        list(snapshot_by_id.values()),
    )

    return jsonify({
        "ok":                     not errors,
        "balance_score":          balance_score,
        "weight_per_hold":        weight_per_hold,
        "weight_target_per_hold": weight_target_per_hold,
        "errors":                 errors,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Read APIs
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Vessel hold geometry — read / edit
# ─────────────────────────────────────────────────────────────────────────────

@cargo_bp.route("/cargo/api/vessels/<vessel_slug>/holds", methods=["GET"])
def get_vessel_holds(vessel_slug: str):
    """Return the visualizer payload for one vessel (empty when none stored)."""
    _require_auth()
    client_id = _client_id_from_request()
    vessel = holds_resolver.find_vessel_by_slug(client_id, vessel_slug)
    if not vessel:
        return jsonify({"error": "Vessel not found"}), 404
    payload = holds_resolver.get_vessel_meta(vessel) or {}
    return jsonify({
        "vessel_id":   vessel.id,
        "vessel_name": vessel.name,
        "vessel_slug": vessel_slug,
        "payload":     payload,
        "has_holds":   bool(payload.get("holds")),
    })


@cargo_bp.route("/cargo/api/vessels/<vessel_slug>/holds", methods=["POST"])
def set_vessel_holds_route(vessel_slug: str):
    """
    Manually create / overwrite the hold geometry for a vessel.

    Body (JSON):
        {
          "holds": [{id, length, breadth, height,
                     has_tween, lower_height, upper_height}, ...],
          "loa":      120.0,
          "breadth":  18.0,
          "depth":    10.0,
          "draft":    7.0,
          "hold_capacity_m3":     10500,
          "double_bottom_height": 1.5
        }

    Auto-creates the Vessel row if the slug doesn't yet have one — needed
    on Vercel where filesystem-derived rows don't pre-exist.
    """
    _require_auth()
    client_id = _client_id_from_request()
    body = request.get_json(silent=True) or {}

    holds = body.get("holds")
    if not holds or not isinstance(holds, list):
        return jsonify({"error": "Body must include a non-empty 'holds' array"}), 400

    vessel = holds_resolver.find_vessel_by_slug(client_id, vessel_slug)
    if not vessel:
        # Auto-create — same convention as the manifest upload route uses
        vessel = Vessel(
            client_id=client_id,
            name=holds_resolver.display_name_from_slug(vessel_slug),
        )
        db.session.add(vessel)
        db.session.flush()

    try:
        payload = holds_resolver.set_vessel_holds(
            vessel,
            holds,
            hold_capacity_m3=body.get("hold_capacity_m3"),
            double_bottom_height=body.get("double_bottom_height"),
            loa=body.get("loa"),
            breadth=body.get("breadth"),
            depth=body.get("depth"),
            draft=body.get("draft"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"ok": True, "payload": payload})


@cargo_bp.route("/cargo/api/vessels/<vessel_slug>/cargo", methods=["GET"])
def get_vessel_cargo(vessel_slug: str):
    """
    Return the full cargo state for a vessel: every active manifest with
    its color, items, and per-manifest placements; plus a combined layout
    that the 3D visualizer renders in one pass with each item colored by
    its source manifest.
    """
    _require_auth()
    client_id = _client_id_from_request()

    vessel = holds_resolver.find_vessel_by_slug(client_id, vessel_slug)
    if not vessel:
        return jsonify({
            "manifests": [],
            "layout": None,
            "color_by_manifest": {},
        })

    actives = _active_manifests_for_vessel(client_id, vessel)
    if not actives:
        return jsonify({
            "manifests": [],
            "layout": None,
            "color_by_manifest": {},
        })

    color_by_manifest: dict[int, str] = {}
    manifests_payload: list[dict] = []
    combined_placements: list[dict] = []
    combined_unplaced: list[dict] = []

    # Source-of-truth: live CargoPlacement rows.  After /save the cached
    # layout_json reflects the joint pack, but every subsequent manual
    # move (/move) updates only the CargoPlacement row, leaving
    # layout_json stale.  Building the visualizer payload from the live
    # rows guarantees the page reload shows what the user actually
    # persisted.
    from cargo.collisions import rotated_dims  # local import: avoid cycle

    # Holds keyed by id for quick lookup (used to compute fill_pct).
    holds_payload   = holds_resolver.get_vessel_meta(vessel) or {}
    holds_list      = holds_payload.get("holds") or []
    bins_descriptor = []
    if holds_list:
        try:
            from cargo.packer import build_bins
            built_bins = build_bins(
                holds_list,
                vessel_dwat_kg=_vessel_dwat_kg_for(client_id, vessel),
            )
            bins_descriptor = [
                {
                    "bin_id":        b.bin_id,
                    "hold_id":       b.hold_id,
                    "level":         b.level,
                    "length":        round(b.length, 3),
                    "width":         round(b.width, 3),
                    "height":        round(b.height, 3),
                    "volume_m3":     round(b.volume, 3),
                    "max_weight_kg": round(b.max_weight_kg, 1),
                }
                for b in built_bins
            ]
        except Exception as exc:
            logger.warning(f"[cargo] build_bins failed for vessel {vessel.id}: {exc}")
            built_bins = []
    else:
        built_bins = []

    for idx, m in enumerate(actives):
        color = _manifest_color(idx)
        color_by_manifest[m.id] = color

        items = [it.to_dict() for it in m.items]
        # Inject color so the sidebar can render swatches without a lookup.
        for it in items:
            it["color_hex"] = color
            it["manifest_id"] = m.id

        manifests_payload.append({
            **m.to_dict(),
            "color_hex": color,
            "items": items,
        })

        # Pull every live placement row for this manifest.  Each row joins
        # to its CargoItem for dimensions / weight / can_stack.
        items_by_id: dict[int, CargoItem] = {it.id: it for it in m.items}
        for pl in CargoPlacement.query.filter_by(manifest_id=m.id).all():
            cargo_item = items_by_id.get(pl.item_id)
            if cargo_item is None:
                continue
            base = {
                "placement_id":  pl.id,
                "manifest_id":   m.id,
                "cargo_item_id": cargo_item.id,
                "item_id":       cargo_item.item_id,
                "item_position": cargo_item.position,
                "color_hex":     color,
                "is_pinned":     bool(getattr(pl, "is_pinned", False)),
            }
            if pl.is_placed:
                rl, rw, rh = rotated_dims(
                    {"length_m": cargo_item.length_m,
                     "width_m":  cargo_item.width_m,
                     "height_m": cargo_item.height_m},
                    int(pl.rotation_deg or 0),
                )
                combined_placements.append({
                    **base,
                    "hold_id":      pl.hold_id,
                    "level":        pl.level,
                    "x":            float(pl.x_m or 0.0),
                    "y":            float(pl.y_m or 0.0),
                    "z":            float(pl.z_m or 0.0),
                    "l":            rl,
                    "w":            rw,
                    "h":            rh,
                    "rotation_deg": int(pl.rotation_deg or 0),
                    "weight_kg":    float(cargo_item.gross_weight_kg or 0.0),
                    "can_stack":    bool(cargo_item.can_stack),
                })
            else:
                combined_unplaced.append({
                    **base,
                    "reason": pl.unplaced_reason or "not placed",
                })

    # Aggregate weight + balance from the LIVE placement set (so manual
    # moves immediately reflect in the score) instead of trusting the
    # cached layout_json.
    weight_per_hold: dict[int, float] = {}
    fill_volume_per_hold: dict[int, float] = {}
    for p in combined_placements:
        hid = p.get("hold_id")
        if hid is None:
            continue
        weight_per_hold[hid] = (
            weight_per_hold.get(hid, 0.0) + float(p.get("weight_kg") or 0.0)
        )
        fill_volume_per_hold[hid] = (
            fill_volume_per_hold.get(hid, 0.0)
            + float(p.get("l") or 0.0) * float(p.get("w") or 0.0)
                                       * float(p.get("h") or 0.0)
        )

    # Targets recomputed from bin volumes: same proportional rule the packer
    # uses, so the score is comparable to what /save reported.
    weight_target_per_hold: dict[int, float] = {}
    hold_volume: dict[int, float] = {}
    if built_bins:
        total_volume = sum(b.volume for b in built_bins) or 1.0
        total_weight = sum(p.get("weight_kg") or 0.0 for p in combined_placements)
        for b in built_bins:
            tgt = total_weight * (b.volume / total_volume)
            weight_target_per_hold[b.hold_id] = (
                weight_target_per_hold.get(b.hold_id, 0.0) + tgt
            )
            hold_volume[b.hold_id] = hold_volume.get(b.hold_id, 0.0) + b.volume

    fill_pct_per_hold: dict[int, float] = {}
    for hid, vol in hold_volume.items():
        if vol > 0:
            fill_pct_per_hold[hid] = round(
                100.0 * fill_volume_per_hold.get(hid, 0.0) / vol, 2
            )

    from cargo.collisions import recompute_balance_score
    balance_score = recompute_balance_score(weight_per_hold, weight_target_per_hold)

    layout = {
        "placements":             combined_placements,
        "unplaced":               combined_unplaced,
        "bins":                   bins_descriptor,
        "weight_per_hold":        {str(k): round(v, 1)
                                   for k, v in weight_per_hold.items()},
        "weight_target_per_hold": {str(k): round(v, 1)
                                   for k, v in weight_target_per_hold.items()},
        "fill_pct_per_hold":      {str(k): v for k, v in fill_pct_per_hold.items()},
        "balance_score":          balance_score,
        "total_weight_kg":        round(sum(p.get("weight_kg") or 0.0
                                            for p in combined_placements), 1),
        "total_volume_m3":        round(sum((p.get("l") or 0)
                                            * (p.get("w") or 0)
                                            * (p.get("h") or 0)
                                            for p in combined_placements), 3),
        "placed_count":           len(combined_placements),
        "unplaced_count":         len(combined_unplaced),
    }

    return jsonify({
        "vessel_id":         vessel.id,
        "vessel_name":       vessel.name,
        "vessel_slug":       vessel_slug,
        "manifests":         manifests_payload,
        "layout":            layout,
        "color_by_manifest": {str(k): v for k, v in color_by_manifest.items()},
    })


@cargo_bp.route("/cargo/api/vessels/<vessel_slug>/manifest", methods=["GET"])
def get_active_manifest_for_vessel(vessel_slug: str):
    """
    [Legacy] Most-recently-active manifest for the visualizer.  Kept for
    backward compatibility — the new visualizer uses /cargo (multi-manifest)
    instead.  Returns None when nothing is active.
    """
    _require_auth()
    client_id = _client_id_from_request()

    vessel = holds_resolver.find_vessel_by_slug(client_id, vessel_slug)
    if not vessel:
        return jsonify({"manifest": None, "items": [], "layout": None})

    manifest = (
        CargoManifest.query
        .filter_by(client_id=client_id, vessel_id=vessel.id, status="active")
        .order_by(CargoManifest.packed_at.desc().nullslast(), CargoManifest.id.desc())
        .first()
    )
    if not manifest:
        return jsonify({"manifest": None, "items": [], "layout": None})

    items = [it.to_dict() for it in manifest.items]
    layout = json.loads(manifest.layout_json) if manifest.layout_json else None
    return jsonify({
        "manifest": manifest.to_dict(),
        "items":    items,
        "layout":   layout,
    })


@cargo_bp.route("/cargo/api/manifests/<int:manifest_id>", methods=["GET"])
def get_manifest(manifest_id: int):
    """Full manifest detail (used by the preview page's JS for hot reload)."""
    _require_auth()
    client_id = _client_id_from_request()
    manifest = CargoManifest.query.filter_by(
        id=manifest_id, client_id=client_id
    ).first_or_404()

    return jsonify({
        "manifest": manifest.to_dict(),
        "items":    [it.to_dict() for it in manifest.items],
        "layout":   json.loads(manifest.layout_json) if manifest.layout_json else None,
    })


@cargo_bp.route("/cargo/api/manifests/<int:manifest_id>", methods=["DELETE"])
def delete_manifest(manifest_id: int):
    """
    Discard a manifest (cascades to items + placements).

    If this was an active manifest and the vessel still has other active
    manifests, the joint packer is re-run so the remaining packing lists
    can take advantage of the freed space.
    """
    _require_auth()
    client_id = _client_id_from_request()
    manifest = CargoManifest.query.filter_by(
        id=manifest_id, client_id=client_id
    ).first_or_404()

    was_active     = (manifest.status == "active")
    vessel         = manifest.vessel
    vessel_slug    = _slugify_vessel_name(vessel.name) if vessel else None
    vessel_for_repack = vessel if was_active and vessel else None

    # Best-effort: remove the original file from Spaces
    if manifest.storage_key and spaces.is_configured():
        try:
            spaces.delete_file(manifest.storage_key)
        except Exception:
            pass

    db.session.delete(manifest)
    db.session.commit()

    # Re-pack survivors so the layout stays consistent.
    if vessel_for_repack:
        remaining = _active_manifests_for_vessel(client_id, vessel_for_repack)
        if remaining:
            holds = _load_holds_for_vessel(client_id, vessel_slug) if vessel_slug else []
            if holds:
                try:
                    _run_joint_packer_for_vessel(
                        client_id, vessel_for_repack, holds,
                        _vessel_dwat_kg_for(client_id, vessel_for_repack),
                    )
                    db.session.commit()
                    logger.info(
                        f"[cargo] Re-packed {len(remaining)} survivor manifest(s) "
                        f"for vessel #{vessel_for_repack.id} after delete"
                    )
                except Exception as exc:
                    db.session.rollback()
                    logger.warning(
                        f"[cargo] Survivor re-pack failed after delete: {exc}"
                    )
    return jsonify({"ok": True})
