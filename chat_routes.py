#!/usr/bin/env python3
"""
Chat API Blueprint
Handles all chatbot endpoints: query, config retrieval, and health.

RAG flow per request:
  1. Load client config (by client_id)
  2. Embed the user message with OpenAI text-embedding-3-small
  3. Query Pinecone (scoped to client's namespace) for top-k chunks
  4. Build a context block from retrieved chunks
  5. Call Anthropic (Claude) with system prompt + context + conversation history
  6. Return the reply

Phase 2 note: client_config.get_client_config() will be swapped for a DB
query — nothing else in this file needs to change.
"""

import os
import uuid
import time
import logging
from datetime import datetime, timezone, date as date_type
from flask import Blueprint, request, jsonify
from openai import OpenAI
from anthropic import Anthropic
from pinecone import Pinecone
from client_config import get_client_config, get_public_config

logger = logging.getLogger(__name__)

chat_bp = Blueprint("chat", __name__, url_prefix="/api")


def _resolve_client_id(url_client_id: str | None) -> str:
    """
    Resolve the client_id — mirrors get_client_id_from_request() in app.py.

    Priority:
      1. client_id from URL path      — /api/config/<client_id>
      2. ?client=xxx query param      — explicit override / share links
      3. Subdomain of Host header     — multi-tenant custom domains
      4. DEFAULT_CLIENT_ID env var    — last-resort fallback (Vercel single URL)
      5. "default"
    """
    if url_client_id:
        return url_client_id.strip().lower()

    param = request.args.get('client')
    if param:
        return param.strip().lower()

    host = request.host.split(':')[0]
    parts = host.split('.')
    is_ip = all(p.isdigit() for p in parts)
    if not is_ip and len(parts) >= 3 and parts[0] not in ('www', ''):
        return parts[0].lower()

    default_client = os.getenv('DEFAULT_CLIENT_ID', '').strip().lower()
    if default_client:
        return default_client

    return 'default'

# ---------------------------------------------------------------------------
# SDK clients — initialised once at import time.
# API keys are read from environment variables.
# ---------------------------------------------------------------------------
_openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
_anthropic_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
_pinecone_client = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PINECONE_HOST       = os.getenv("PINECONE_HOST", "").strip()
_raw_dims            = os.getenv("PINECONE_DIMENSIONS", "").strip()
_PINECONE_DIMENSIONS = int(_raw_dims) if _raw_dims.isdigit() else None


def _get_pinecone_index(index_name: str):
    """Return a Pinecone Index object, preferring a direct host connection."""
    if _PINECONE_HOST:
        return _pinecone_client.Index(host=_PINECONE_HOST)
    return _pinecone_client.Index(index_name)


def _embed_query(text: str, model: str = "text-embedding-3-small") -> list[float]:
    """Embed a single string with OpenAI and return the vector."""
    kwargs: dict = {"input": text, "model": model}
    if _PINECONE_DIMENSIONS:
        kwargs["dimensions"] = _PINECONE_DIMENSIONS
    response = _openai_client.embeddings.create(**kwargs)
    return response.data[0].embedding


def _query_pinecone(
    index,
    vector: list[float],
    namespace: str,
    top_k: int,
) -> list[dict]:
    """
    Query Pinecone and return a list of match dicts.
    Each dict has 'id', 'score', and 'metadata'.
    """
    kwargs = dict(vector=vector, top_k=top_k, include_metadata=True)
    if namespace:
        kwargs["namespace"] = namespace

    result = index.query(**kwargs)
    return result.get("matches", [])


def _build_context_block(matches: list[dict]) -> str:
    """
    Convert Pinecone matches into a readable context string to inject
    into the LLM prompt.
    """
    if not matches:
        return "No relevant context found in the knowledge base."

    parts = []
    for i, match in enumerate(matches, start=1):
        meta = match.get("metadata", {})
        score = match.get("score", 0)

        # Pull the text content — try common field names
        content = (
            meta.get("text")
            or meta.get("content")
            or meta.get("chunk_text")
            or meta.get("page_content")
            or ""
        ).strip()

        if not content:
            continue

        # Build a header from whatever metadata is available
        header_parts = []
        for field in ("vessel", "chapter", "sub_chapter", "clause_title", "clause_number"):
            val = meta.get(field)
            if val:
                header_parts.append(f"{field}: {val}")

        header = " | ".join(header_parts) if header_parts else f"Chunk {i}"
        parts.append(f"[{i}] {header} (relevance: {score:.2f})\n{content}")

    return "\n\n---\n\n".join(parts) if parts else "No relevant context found."


def _build_messages(
    system_prompt: str,
    context_block: str,
    history: list[dict],
    user_message: str,
    max_history: int,
) -> tuple[str, list[dict]]:
    """
    Build the system string and messages list for the Anthropic API call.

    Returns:
        system  — the system prompt string (injected separately in Anthropic's API)
        messages — list of {"role": ..., "content": ...} dicts
    """
    # Combine system prompt with retrieved context
    system = (
        f"{system_prompt}\n\n"
        "---\n"
        "RETRIEVED CONTEXT FROM KNOWLEDGE BASE\n"
        "Use the following excerpts to answer the user's question. "
        "If the answer is not present, say so clearly.\n\n"
        f"{context_block}"
    )

    # Trim history to the last N turns (each turn = 1 user + 1 assistant msg)
    trimmed_history = history[-(max_history * 2):]

    messages = trimmed_history + [{"role": "user", "content": user_message}]
    return system, messages


def _validate_history(raw_history) -> list[dict]:
    """
    Validate and sanitise the conversation history supplied by the client.
    Each item must be {"role": "user"|"assistant", "content": str}.
    """
    if not isinstance(raw_history, list):
        return []
    validated = []
    for item in raw_history:
        if not isinstance(item, dict):
            continue
        role = item.get("role", "")
        content = item.get("content", "")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        validated.append({"role": role, "content": content.strip()})
    return validated


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@chat_bp.route("/config", methods=["GET"])
@chat_bp.route("/config/<client_id>", methods=["GET"])
def get_config(client_id: str = None):
    """
    GET /api/config
    GET /api/config/<client_id>

    Returns public-safe client configuration for the frontend to use
    (branding, theme, chatbot name). No secrets are exposed.
    """
    resolved = _resolve_client_id(client_id)
    public_cfg = get_public_config(resolved)
    if public_cfg is None:
        return jsonify({"error": f"Client '{resolved}' not found or inactive"}), 404
    return jsonify(public_cfg)


@chat_bp.route("/chat", methods=["POST"])
@chat_bp.route("/chat/<client_id>", methods=["POST"])
def chat(client_id: str = None):
    """
    POST /api/chat
    POST /api/chat/<client_id>

    Request body (JSON):
        message         str   Required. The user's latest message.
        conversation_id str   Optional. Pass back the ID from a previous response
                              to maintain a logical thread ID on the client side.
        history         list  Optional. Previous turns as
                              [{"role": "user"|"assistant", "content": "..."}]

    Response body (JSON):
        reply           str   The assistant's response.
        conversation_id str   Echo back (or newly generated) conversation ID.
        sources         list  Metadata of the Pinecone chunks used (for debugging).
    """
    # --- Load client config ---------------------------------------------------
    resolved = _resolve_client_id(client_id)
    cfg = get_client_config(resolved)
    if cfg is None:
        return jsonify({"error": f"Client '{resolved}' not found or inactive"}), 404

    # --- Rate limit check -----------------------------------------------------
    daily_limit = cfg.get("daily_request_limit", 0)
    if daily_limit > 0:
        from models import db, UsageLog
        today = date_type.today()
        today_count = UsageLog.query.filter_by(
            client_id=resolved, date=today
        ).count()
        if today_count >= daily_limit:
            logger.warning(
                f"[chat] Rate limit hit: client={resolved} "
                f"count={today_count} limit={daily_limit}"
            )
            return jsonify({
                "error": (
                    f"Daily request limit of {daily_limit} reached for this account. "
                    "Please try again tomorrow."
                )
            }), 429

    # --- Parse request --------------------------------------------------------
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Request body must be JSON"}), 400

    user_message = (body.get("message") or "").strip()
    if not user_message:
        return jsonify({"error": "Field 'message' is required and cannot be empty"}), 400

    conversation_id = body.get("conversation_id") or str(uuid.uuid4())
    history = _validate_history(body.get("history", []))

    # Extract user email from JWT if present (optional auth — chat is open)
    user_email = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            import jwt as _jwt
            _secret = os.getenv("JWT_SECRET", "change-me-in-production")
            _payload = _jwt.decode(auth_header[7:], _secret, algorithms=["HS256"])
            user_email = _payload.get("sub")
        except Exception:
            pass

    t_start = time.monotonic()

    logger.info(
        f"[chat] client={resolved} conversation={conversation_id} "
        f"history_turns={len(history)//2} message_len={len(user_message)}"
    )

    # --- Step 1: Embed the user message --------------------------------------
    try:
        query_vector = _embed_query(user_message, model=cfg["embedding_model"])
    except Exception as e:
        logger.error(f"[chat] Embedding failed: {e}")
        return jsonify({"error": "Failed to embed query", "detail": str(e)}), 500

    # --- Step 2: Query Pinecone ----------------------------------------------
    try:
        index = _get_pinecone_index(cfg["pinecone_index"])
        matches = _query_pinecone(
            index=index,
            vector=query_vector,
            namespace=cfg["pinecone_namespace"],
            top_k=cfg["max_context_chunks"],
        )
        logger.info(f"[chat] Pinecone returned {len(matches)} matches")
    except Exception as e:
        logger.error(f"[chat] Pinecone query failed: {e}")
        return jsonify({"error": "Failed to query knowledge base", "detail": str(e)}), 500

    # --- Step 3: Build context -----------------------------------------------
    context_block = _build_context_block(matches)

    # --- Step 4: Build prompt and call Claude --------------------------------
    try:
        system_str, messages = _build_messages(
            system_prompt=cfg["system_prompt"],
            context_block=context_block,
            history=history,
            user_message=user_message,
            max_history=cfg["max_history"],
        )

        response = _anthropic_client.messages.create(
            model=cfg["llm_model"],
            max_tokens=2048,
            system=system_str,
            messages=messages,
        )

        reply = response.content[0].text
        tokens_in  = response.usage.input_tokens
        tokens_out = response.usage.output_tokens

        logger.info(
            f"[chat] Claude responded: input_tokens={tokens_in} "
            f"output_tokens={tokens_out}"
        )

    except Exception as e:
        logger.error(f"[chat] LLM call failed: {e}")
        return jsonify({"error": "Failed to generate response", "detail": str(e)}), 500

    # --- Log usage -----------------------------------------------------------
    try:
        from models import db, UsageLog
        response_ms = int((time.monotonic() - t_start) * 1000)
        now = datetime.now(timezone.utc)
        log = UsageLog(
            client_id=resolved,
            user_email=user_email,
            timestamp=now,
            date=now.date(),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=cfg["llm_model"],
            response_ms=response_ms,
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        # Never let logging failure break the chat response
        logger.error(f"[chat] Usage logging failed: {e}")

    # --- Step 5: Return response ---------------------------------------------
    sources = [
        {
            "score": m.get("score"),
            "metadata": {
                k: v for k, v in m.get("metadata", {}).items()
                if k in ("vessel", "chapter", "sub_chapter", "clause_title", "clause_number", "clause_type")
            },
        }
        for m in matches
    ]

    return jsonify({
        "reply": reply,
        "conversation_id": conversation_id,
        "sources": sources,
    })


@chat_bp.route("/health", methods=["GET"])
def chat_health():
    """
    GET /api/health
    Lightweight liveness check for the chat API specifically.
    """
    missing = [
        key for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PINECONE_API_KEY")
        if not os.getenv(key)
    ]
    if missing:
        return jsonify({
            "status": "degraded",
            "missing_env_vars": missing,
        }), 200

    return jsonify({"status": "ok"})
