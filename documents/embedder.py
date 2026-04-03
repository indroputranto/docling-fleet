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

OPENAI_API_KEY   = os.environ.get("OPENAI_API_KEY", "")
PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY", "")

EMBED_BATCH_SIZE = 20   # OpenAI allows up to 2048; keep batches small


def _get_openai_client():
    from openai import OpenAI
    return OpenAI(api_key=OPENAI_API_KEY)


def _get_pinecone_index(index_name: str):
    from pinecone import Pinecone
    pc = Pinecone(api_key=PINECONE_API_KEY)
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
        response = openai_client.embeddings.create(
            model=embedding_model,
            input=batch,
        )
        all_embeddings.extend([item.embedding for item in response.data])

    # ── Build Pinecone vectors ────────────────────────────────────────────────
    vectors = []
    for chunk, vector_id, embedding in zip(chunks, vector_ids, all_embeddings):
        vectors.append({
            "id":     vector_id,
            "values": embedding,
            "metadata": {
                "client_id":       document.client_id,
                "document_id":     document.id,
                "filename":        document.filename,
                "chunk_title":     chunk.title or "",
                "chunk_position":  chunk.position,
                "text":            texts[chunk.position],  # stored for retrieval context
            },
        })

    # ── Upsert to Pinecone (batch of 100 max) ─────────────────────────────────
    upserted = 0
    for i in range(0, len(vectors), 100):
        batch = vectors[i : i + 100]
        index.upsert(vectors=batch, namespace=pinecone_namespace)
        upserted += len(batch)
        logger.info(f"[embedder] Upserted {upserted}/{len(vectors)} vectors "
                    f"(doc={document.id}, ns={pinecone_namespace!r})")

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
