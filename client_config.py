#!/usr/bin/env python3
"""
Client Configuration System
Phase 1: Hardcoded config loaded from file/env.
Phase 2: This module will query a database instead — the interface stays the same.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config Schema (reference)
# ---------------------------------------------------------------------------
# Each client config is a plain dict with the following fields:
#
#   client_id         str   Unique slug, used in routing and Pinecone namespace
#   name              str   Human-readable client/product name
#   pinecone_index    str   Pinecone index name
#   pinecone_namespace str  Pinecone namespace (empty string = default namespace)
#   embedding_model   str   OpenAI embedding model (must match what was used at upload time)
#   llm_model         str   Anthropic model string
#   system_prompt     str   Full system prompt for the chatbot
#   max_context_chunks int  How many Pinecone results to retrieve per query
#   max_history       int   How many past conversation turns to send to the LLM
#   theme             dict  Branding: colors, logo, display name
#   active            bool  Whether this client config is enabled
# ---------------------------------------------------------------------------

def _load_system_prompt() -> str:
    """Load system prompt from prompt.md if it exists, otherwise use a default."""
    prompt_path = os.path.join(os.path.dirname(__file__), "prompt.md")
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return (
        "You are a helpful assistant. Answer questions accurately and concisely "
        "based on the provided context. If the answer is not in the context, say so."
    )


# ---------------------------------------------------------------------------
# Client registry
# Phase 1: plain dict. Phase 2: replace get_client_config() with a DB query.
# ---------------------------------------------------------------------------

_CLIENT_REGISTRY: dict[str, dict] = {
    "default": {
        "client_id": "default",
        "name": "Vessel Documentation Assistant",
        "pinecone_index": os.getenv("PINECONE_INDEX", "vessel-embeddings"),
        "pinecone_namespace": os.getenv("PINECONE_NAMESPACE", ""),
        "embedding_model": "text-embedding-3-small",
        "llm_model": os.getenv("LLM_MODEL", "claude-opus-4-6"),
        "system_prompt": _load_system_prompt(),
        "max_context_chunks": int(os.getenv("MAX_CONTEXT_CHUNKS", "8")),
        "max_history": int(os.getenv("MAX_HISTORY_TURNS", "10")),
        "welcome_message": "Hello! I'm your vessel documentation assistant. Ask me anything about charter parties, trading limits, vessel specifications, or cargo restrictions.",
        "suggested_questions": [
            "What are the trading exclusions for this vessel?",
            "Show me the speed and consumption table.",
            "What cargo types are excluded?",
            "What are the delivery figures?",
        ],
        "theme": {
            "primary_color":    "#1a1a2e",
            "secondary_color":  "#16213e",
            "accent_color":     "#0f3460",
            "text_color":       "#ffffff",
            "font_family":      "Inter, sans-serif",
            "logo_url":         None,
            "company_name":     "Vessel Assistant",
            "chatbot_name":     "Vessel AI",
            "default_theme":    "dark",
            "show_mode_toggle": True,
        },
        "active": True,
    },
    # -----------------------------------------------------------------------
    # Add more clients here in Phase 1 as needed.
    # Example:
    #
    # "acme_shipping": {
    #     "client_id": "acme_shipping",
    #     "name": "ACME Shipping Assistant",
    #     "pinecone_index": "vessel-embeddings",
    #     "pinecone_namespace": "acme",
    #     "embedding_model": "text-embedding-3-small",
    #     "llm_model": "claude-sonnet-4-6",
    #     "system_prompt": "You are ACME's shipping assistant...",
    #     "max_context_chunks": 8,
    #     "max_history": 10,
    #     "theme": {
    #         "primary_color": "#003366",
    #         "secondary_color": "#005599",
    #         "accent_color": "#0077cc",
    #         "text_color": "#ffffff",
    #         "font_family": "Inter, sans-serif",
    #         "logo_url": "https://acme.com/logo.png",
    #         "company_name": "ACME Shipping",
    #         "chatbot_name": "ACME AI",
    #     },
    #     "active": True,
    # },
    # -----------------------------------------------------------------------
}


def get_client_config(client_id: str = "default") -> dict | None:
    """
    Retrieve a client config by ID.
    Phase 2: tries the database first, falls back to the hardcoded registry
    so existing behaviour is preserved if the DB is empty or unavailable.
    Returns None if the client doesn't exist or is inactive.
    """
    # --- DB lookup (Phase 2) -------------------------------------------------
    try:
        from models import ClientConfig
        row = ClientConfig.query.filter_by(
            client_id=client_id, active=True
        ).first()
        if row:
            return row.to_config_dict()
    except Exception:
        # DB not initialised yet (e.g. first boot) — fall through to registry
        pass

    # --- Hardcoded fallback (Phase 1) ----------------------------------------
    config = _CLIENT_REGISTRY.get(client_id)
    if config is None:
        return None
    if not config.get("active", True):
        return None
    return config


def get_public_config(client_id: str = "default") -> dict | None:
    """
    Return only the fields safe to expose to the frontend (no API keys, etc.).
    Used by the GET /api/config endpoint.
    """
    config = get_client_config(client_id)
    if config is None:
        return None
    return {
        "client_id":          config["client_id"],
        "name":               config["name"],
        "chatbot_name":       config["theme"]["chatbot_name"],
        "company_name":       config["theme"]["company_name"],
        "welcome_message":    config.get("welcome_message", ""),
        "suggested_questions": config.get("suggested_questions", []),
        "theme":              config["theme"],
    }


def list_client_ids() -> list[str]:
    """Return all active client IDs. Useful for admin tooling."""
    return [cid for cid, cfg in _CLIENT_REGISTRY.items() if cfg.get("active", True)]
