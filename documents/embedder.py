#!/usr/bin/env python3
"""
Embedding + Pinecone upsert for the document upload pipeline.

Called synchronously from the /documents/<id>/save route after the user
has reviewed and edited their chunks.
"""

import os
import logging
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from models import Document, DocumentChunk

logger = logging.getLogger(__name__)

OPENAI_API_KEY     = os.environ.get("OPENAI_API_KEY", "")
PINECONE_API_KEY   = os.environ.get("PINECONE_API_KEY", "")
PINECONE_HOST      = os.environ.get("PINECONE_HOST", "")

# Optional: truncate embeddings to match a Pinecone index created with fewer
# dimensions (e.g. 1024).  text-embedding-3-* models support this natively.
# Leave unset to use the model's default (1536 for text-embedding-3-small).
_raw_dims          = os.environ.get("PINECONE_DIMENSIONS", "").strip()
PINECONE_DIMENSIONS: int | None = int(_raw_dims) if _raw_dims.isdigit() else None

EMBED_BATCH_SIZE = 20   # OpenAI allows up to 2048; keep batches small


def _get_openai_client():
    from openai import OpenAI
    return OpenAI(api_key=OPENAI_API_KEY)


def _get_pinecone_index(index_name: str):
    from pinecone import Pinecone
    pc = Pinecone(api_key=PINECONE_API_KEY)
    if PINECONE_HOST:
        # Direct host connection — bypasses controller-plane lookup and is
        # required when the index URL is pinned via PINECONE_HOST in .env.
        logger.info(f"[embedder] Connecting to Pinecone via host: {PINECONE_HOST}")
        return pc.Index(host=PINECONE_HOST)
    # Fallback: look up index by name through the controller plane
    logger.info(f"[embedder] Connecting to Pinecone index by name: {index_name}")
    return pc.Index(index_name)


def embed_and_upsert(
    document: "Document",
    chunks: List["DocumentChunk"],
    pinecone_index: str,
    pinecone_namespace: str,
    embedding_model: str = "text-embedding-3-small",
) -> int:
    """
    Embed all chunks for a document and upsert them into Pinecone.

    Each vector is stored with metadata:
      client_id, document_id, filename, chunk_title, chunk_position

    Returns:
        Number of vectors successfully upserted.

    Raises:
        RuntimeError on API failure (caller should catch and mark document as error).
    """
    if not chunks:
        logger.warning(f"[embedder] No chunks to embed for doc {document.id}")
        return 0

    openai_client = _get_openai_client()
    index         = _get_pinecone_index(pinecone_index)

    texts     = [f"{c.title}\n\n{c.body}" if c.title else c.body for c in chunks]
    vector_ids = [c.vector_id() for c in chunks]

    # ── Embed in batches ─────────────────────────────────────────────────────
    all_embeddings: List[List[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]
        logger.info(f"[embedder] Embedding batch {i // EMBED_BATCH_SIZE + 1} "
                    f"({len(batch)} chunks) for doc {document.id}")
        embed_kwargs = dict(model=embedding_model, input=batch)
        if PINECONE_DIMENSIONS:
            # Truncate to match the Pinecone index dimension.
            # Supported by text-embedding-3-* models only.
            embed_kwargs["dimensions"] = PINECONE_DIMENSIONS
        response = openai_client.embeddings.create(**embed_kwargs)
        all_embeddings.extend([item.embedding for item in response.data])

    # ── Derive boolean document-type flags from document_category ────────────
    # Stored as metadata so Pinecone can filter by type at query time without
    # callers needing to know the internal category slug names.
    cat = (document.document_category or "").lower()
    type_flags = {
        "is_charter_party":        cat == "charter_party",
        "is_fixture_recap":        cat == "fixture_recap",
        "is_vessel_specifications": cat == "vessel_specifications",
        "is_addendum":             cat == "addendum",
        "is_delivery_details":     cat == "delivery_details",
        "is_speed_consumption":    cat == "speed_consumption",
    }

    # ── Build Pinecone vectors ────────────────────────────────────────────────
    vectors = []
    for idx, (chunk, vector_id, embedding) in enumerate(zip(chunks, vector_ids, all_embeddings)):
        body_text = texts[idx]   # use loop index — not chunk.position, which may drift
        vectors.append({
            "id":     vector_id,
            "values": embedding,
            "metadata": {
                # ── Identity / filtering ──────────────────────────────────
                "client_id":         document.client_id,
                "document_id":       document.id,
                "vessel_id":         document.vessel_id or 0,
                "vessel":            document.group_name or "",   # human-readable name
                "filename":          document.filename,
                "document_category": document.document_category or "",
                # ── Chunk location ────────────────────────────────────────
                "chunk_title":       chunk.title or "",
                "chunk_position":    chunk.position,
                # ── Full text (for retrieval display) ─────────────────────
                "content":           body_text,
                # ── Document-type flags (for metadata filtering) ──────────
                **type_flags,
                # ── Content flags ─────────────────────────────────────────
                "contains_strikethrough": "~~" in body_text,
            },
        })

    # ── Upsert to Pinecone (batch of 100 max) ─────────────────────────────────
    logger.info(
        f"[embedder] Upserting {len(vectors)} vectors to index={pinecone_index!r} "
        f"ns={pinecone_namespace!r} host={PINECONE_HOST!r} doc={document.id}"
    )
    upserted = 0
    for i in range(0, len(vectors), 100):
        batch = vectors[i : i + 100]
        resp = index.upsert(vectors=batch, namespace=pinecone_namespace)
        # Pinecone v4 returns UpsertResponse(upserted_count=N).
        # Fall back to len(batch) for older SDK versions that return None.
        actual = getattr(resp, "upserted_count", None)
        if actual is None:
            actual = len(batch)
        upserted += actual
        if actual != len(batch):
            logger.warning(
                f"[embedder] Pinecone batch upsert mismatch: sent {len(batch)}, "
                f"confirmed {actual} (doc={document.id}, ns={pinecone_namespace!r})"
            )
        logger.info(f"[embedder] Upserted {upserted}/{len(vectors)} vectors "
                    f"(doc={document.id}, ns={pinecone_namespace!r})")

    if upserted < len(vectors):
        raise RuntimeError(
            f"Pinecone upsert incomplete: {upserted}/{len(vectors)} vectors written "
            f"(index={pinecone_index!r}, ns={pinecone_namespace!r}). "
            "Check PINECONE_API_KEY, PINECONE_HOST, and index quota."
        )

    return upserted


def delete_document_vectors(
    document: "Document",
    pinecone_index: str,
    pinecone_namespace: str,
) -> None:
    """
    Delete all Pinecone vectors associated with a document.

    Uses the stored pinecone_id on each DocumentChunk for targeted deletion.
    Falls back to metadata-filter delete if chunk IDs are unavailable.
    """
    from models import DocumentChunk

    chunk_ids = [
        c.pinecone_id for c in
        DocumentChunk.query.filter_by(document_id=document.id).all()
        if c.pinecone_id
    ]

    if not chunk_ids:
        logger.warning(
            f"[embedder] No pinecone_ids found for doc {document.id}; "
            "nothing deleted from Pinecone."
        )
        return

    index = _get_pinecone_index(pinecone_index)

    # Delete in batches of 1000 (Pinecone limit)
    for i in range(0, len(chunk_ids), 1000):
        batch = chunk_ids[i : i + 1000]
        index.delete(ids=batch, namespace=pinecone_namespace)
        logger.info(f"[embedder] Deleted {len(batch)} vectors from "
                    f"{pinecone_index}/{pinecone_namespace}")
