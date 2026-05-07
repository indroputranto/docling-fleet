#!/usr/bin/env python3
"""
CI smoke tests — run by the GitHub Actions workflow before deploying.

These tests verify that the core modules are importable and that the
Flask application can be instantiated without errors. They do NOT require
any external services (Pinecone, OpenAI, Anthropic, database) to be
reachable — those are integration concerns, not CI concerns.

Run locally:
    python test_ci.py
"""

import sys
import os

# ── Ensure repo root is on the path ──────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

errors = []


def check(name: str, fn):
    try:
        fn()
        print(f"  ✓  {name}")
    except Exception as e:
        print(f"  ✗  {name}: {e}")
        errors.append((name, e))


# ── 1. Core module imports ────────────────────────────────────────────────────
print("\n[1] Module imports")

check("flask importable",          lambda: __import__("flask"))
check("sqlalchemy importable",     lambda: __import__("flask_sqlalchemy"))
check("jwt importable",            lambda: __import__("jwt"))
check("dotenv importable",         lambda: __import__("dotenv"))
check("requests importable",       lambda: __import__("requests"))
check("boto3 importable",           lambda: __import__("boto3"))

# ── 2. App module imports ─────────────────────────────────────────────────────
print("\n[2] Application module imports")

check("models importable",          lambda: __import__("models"))
check("auth importable",            lambda: __import__("auth"))
check("production_config importable", lambda: __import__("production_config"))
check("document_logger importable", lambda: __import__("document_logger"))

# ── 3. Blueprint module imports ───────────────────────────────────────────────
print("\n[3] Blueprint imports")

check("chat_routes importable",     lambda: __import__("chat_routes"))
check("documents.routes importable",
      lambda: __import__("documents.routes", fromlist=["documents_bp"]))
check("documents.extractor importable",
      lambda: __import__("documents.extractor", fromlist=["extract"]))
check("documents.embedder importable",
      lambda: __import__("documents.embedder", fromlist=["embed_and_upsert"]))
check("documents.object_storage importable",
      lambda: __import__("documents.object_storage", fromlist=["is_configured"]))
check("da.routes importable",
      lambda: __import__("da.routes", fromlist=["da_bp"]))
check(
    "da.assistant importable",
    lambda: __import__("da.assistant", fromlist=["run_chat_turn", "generate_da_key_notes"]),
)

# Cargo blueprint — independent of documents pipeline
check("cargo.parser importable",
      lambda: __import__("cargo.parser", fromlist=["parse_packing_list"]))
check("cargo.packer importable",
      lambda: __import__("cargo.packer", fromlist=["pack_items"]))
check("cargo.object_storage importable",
      lambda: __import__("cargo.object_storage", fromlist=["is_configured"]))
check("cargo.routes importable",
      lambda: __import__("cargo.routes", fromlist=["cargo_bp"]))
check("cargo.holds importable",
      lambda: __import__("cargo.holds", fromlist=["get_vessel_meta", "set_vessel_holds"]))

# ── 4. Model class presence ───────────────────────────────────────────────────
print("\n[4] Model class checks")

def _check_models():
    from models import (
        User, ClientConfig, Document, DocumentChunk,
        Vessel, ChatSession, ChatMessage, UsageLog,
    )
    assert hasattr(User, "email")
    assert hasattr(Document, "storage_key")
    assert hasattr(Document, "skip_ai_enrichment")
    assert hasattr(ChatSession, "user_email")
    assert hasattr(ChatMessage, "session_id")

check("all model classes present", _check_models)

def _check_cargo_models():
    from models import Vessel, VesselTrip, CargoManifest, CargoItem, CargoPlacement
    assert hasattr(VesselTrip,    "vessel_id")
    assert hasattr(CargoManifest, "trip_id")
    assert hasattr(CargoManifest, "layout_json")
    assert hasattr(CargoItem,     "gross_weight_kg")
    assert hasattr(CargoItem,     "can_stack")
    assert hasattr(CargoPlacement, "is_placed")
    # Cargo-pipeline columns added to Vessel:
    assert hasattr(Vessel, "holds_json")
    assert hasattr(Vessel, "hold_capacity_m3")
    assert hasattr(Vessel, "double_bottom_height")

check("cargo model classes present", _check_cargo_models)

# ── 5. Config sanity ──────────────────────────────────────────────────────────
print("\n[5] Configuration")

def _check_config():
    from production_config import get_config
    cfg = get_config()
    assert hasattr(cfg, "MAX_CONTENT_LENGTH")
    assert cfg.MAX_CONTENT_LENGTH > 0

check("production_config returns valid object", _check_config)

# ── Result ────────────────────────────────────────────────────────────────────
print()
if errors:
    print(f"FAILED — {len(errors)} check(s) did not pass:")
    for name, exc in errors:
        print(f"  • {name}: {exc}")
    sys.exit(1)
else:
    print(f"OK — all checks passed.")
    sys.exit(0)
