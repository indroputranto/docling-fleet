#!/usr/bin/env python3
"""
DigitalOcean Spaces (S3-compatible) object storage helper.

Uses the MinIO Python client — it is fully S3-compatible and installs at
~1 MB, versus ~90 MB for boto3+botocore, keeping the Vercel bundle lean.

Reads configuration from environment variables:
  OBJECT_STORAGE_ENDPOINT   — e.g. https://sgp1.digitaloceanspaces.com
  OBJECT_STORAGE_ACCESS_KEY — Spaces access key ID
  OBJECT_STORAGE_SECRET_KEY — Spaces secret access key
  OBJECT_STORAGE_BUCKET     — Space (bucket) name

Public API:
  upload_file(file_stream, storage_key, content_type=None, length=-1) -> str
      Upload a file-like object and return the storage key on success.

  delete_file(storage_key) -> bool
      Delete an object by its storage key. Returns True on success.

  get_public_url(storage_key) -> str
      Return the public HTTPS URL for an object.

  generate_presigned_url(storage_key, expiry_seconds=3600) -> str
      Return a time-limited pre-signed download URL.

  build_storage_key(client_id, filename) -> str
      Build a namespaced object key, e.g. "documents/ocean7/charter.pdf".

  is_configured() -> bool
      Return True when all required env vars are present.
"""

import logging
import os
import re
from typing import IO, Optional

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

_ENDPOINT   = os.getenv("OBJECT_STORAGE_ENDPOINT", "").rstrip("/")
_ACCESS_KEY = os.getenv("OBJECT_STORAGE_ACCESS_KEY", "")
_SECRET_KEY = os.getenv("OBJECT_STORAGE_SECRET_KEY", "")
_BUCKET     = os.getenv("OBJECT_STORAGE_BUCKET", "")


def is_configured() -> bool:
    """Return True when all required object-storage env vars are set."""
    return bool(_ENDPOINT and _ACCESS_KEY and _SECRET_KEY and _BUCKET)


def _get_client():
    """Return a configured MinIO client pointed at DigitalOcean Spaces."""
    from minio import Minio  # lazy import — app boots without minio installed

    if not is_configured():
        raise RuntimeError(
            "Object storage is not configured. "
            "Set OBJECT_STORAGE_ENDPOINT, OBJECT_STORAGE_ACCESS_KEY, "
            "OBJECT_STORAGE_SECRET_KEY, and OBJECT_STORAGE_BUCKET."
        )

    # Strip scheme — MinIO client takes host only, with secure= flag separately.
    # e.g. "https://sgp1.digitaloceanspaces.com" → "sgp1.digitaloceanspaces.com"
    host = re.sub(r"^https?://", "", _ENDPOINT)
    secure = _ENDPOINT.startswith("https://")

    return Minio(
        host,
        access_key=_ACCESS_KEY,
        secret_key=_SECRET_KEY,
        secure=secure,
    )


# ── Public helpers ────────────────────────────────────────────────────────────

def upload_file(
    file_stream: IO[bytes],
    storage_key: str,
    content_type: Optional[str] = None,
    length: int = -1,
) -> str:
    """
    Upload *file_stream* to the configured Space under *storage_key*.

    Parameters
    ----------
    file_stream : file-like object
        Readable binary stream (e.g. Flask's ``request.files['f'].stream``
        or an ``io.BytesIO``).  The stream is read from its current position.
    storage_key : str
        The object key (path inside the bucket), e.g.
        ``"documents/ocean7/fixture_recap_mv_aurora.pdf"``.
    content_type : str, optional
        MIME type.  Defaults to ``"application/octet-stream"``.
    length : int, optional
        Content length in bytes.  Pass -1 (default) when unknown — the MinIO
        client will buffer the stream to determine the size automatically.

    Returns
    -------
    str
        The *storage_key* that was used — store this in ``Document.storage_key``.

    Raises
    ------
    RuntimeError
        When the upload fails.
    """
    client = _get_client()
    mime = content_type or "application/octet-stream"

    try:
        client.put_object(
            bucket_name=_BUCKET,
            object_name=storage_key,
            data=file_stream,
            length=length,
            content_type=mime,
            part_size=10 * 1024 * 1024,  # 10 MB multipart threshold
        )
        logger.info(f"[object_storage] Uploaded → {_BUCKET}/{storage_key}")
        return storage_key
    except Exception as exc:
        logger.error(
            f"[object_storage] Upload failed for key '{storage_key}': {exc}",
            exc_info=True,
        )
        raise RuntimeError(f"Object storage upload failed: {exc}") from exc


def delete_file(storage_key: str) -> bool:
    """
    Delete the object identified by *storage_key* from the Space.

    Returns True on success, False when the operation fails (errors are
    logged but not re-raised so callers can proceed with DB cleanup).
    """
    if not storage_key:
        return False

    try:
        client = _get_client()
        client.remove_object(_BUCKET, storage_key)
        logger.info(f"[object_storage] Deleted → {_BUCKET}/{storage_key}")
        return True
    except Exception as exc:
        logger.error(
            f"[object_storage] Delete failed for key '{storage_key}': {exc}",
            exc_info=True,
        )
        return False


def get_public_url(storage_key: str) -> str:
    """
    Return the canonical public HTTPS URL for *storage_key*.

    Format: {endpoint}/{bucket}/{key}
    Example: https://sgp1.digitaloceanspaces.com/vesfleet-docs/documents/...
    """
    return f"{_ENDPOINT}/{_BUCKET}/{storage_key}"


def generate_presigned_url(storage_key: str, expiry_seconds: int = 3600) -> str:
    """
    Generate a time-limited pre-signed URL so a document can be downloaded
    directly from the Space without exposing permanent credentials.

    Parameters
    ----------
    storage_key : str
        The object key to generate a URL for.
    expiry_seconds : int
        How long (in seconds) the URL remains valid.  Default: 1 hour.

    Returns
    -------
    str
        A pre-signed HTTPS URL.
    """
    from datetime import timedelta

    client = _get_client()
    try:
        url = client.presigned_get_object(
            _BUCKET,
            storage_key,
            expires=timedelta(seconds=expiry_seconds),
        )
        return url
    except Exception as exc:
        logger.error(
            f"[object_storage] Pre-signed URL generation failed for '{storage_key}': {exc}",
            exc_info=True,
        )
        raise RuntimeError(f"Could not generate pre-signed URL: {exc}") from exc


def build_storage_key(client_id: str, filename: str) -> str:
    """
    Build a namespaced object key for a document upload.

    Format: documents/{client_id}/{filename}
    Example: documents/ocean7/fixture_recap_mv_aurora.pdf

    Keeps files organised per client in the same bucket.
    """
    safe_filename = re.sub(r"[^\w.\-]", "_", filename)
    safe_client   = re.sub(r"[^\w.\-]", "_", client_id)
    return f"documents/{safe_client}/{safe_filename}"
