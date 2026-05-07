#!/usr/bin/env python3
"""
DigitalOcean Spaces (S3-compatible) object storage helper — cargo namespace.

This module is a deliberate **duplicate** of documents/object_storage.py
rather than an import.  Cargo and document upload paths must remain
completely independent so cargo work cannot accidentally regress the
existing document pipeline.  The two files share no code at runtime;
they only share environment variables (which are read independently).

Reads configuration from the same env vars as the documents helper:
  OBJECT_STORAGE_ENDPOINT   — e.g. https://sgp1.digitaloceanspaces.com
  OBJECT_STORAGE_ACCESS_KEY — Spaces access key ID
  OBJECT_STORAGE_SECRET_KEY — Spaces secret access key
  OBJECT_STORAGE_BUCKET     — Space (bucket) name
  OBJECT_STORAGE_REGION     — optional SigV4 region override

Storage keys for cargo manifests are namespaced under ``cargo/`` so they
sit beside (not inside) the documents/ tree:
    cargo/{client_id}/{vessel_id}/{filename}

Public API (parity with documents/object_storage.py):
  upload_file, delete_file, download_file
  get_public_url, generate_presigned_url, generate_presigned_put_url
  build_storage_key, is_configured
"""

import logging
import os
import re
from typing import IO, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

_ENDPOINT   = os.getenv("OBJECT_STORAGE_ENDPOINT", "").strip().rstrip("/")
_ACCESS_KEY = os.getenv("OBJECT_STORAGE_ACCESS_KEY", "").strip()
_SECRET_KEY = os.getenv("OBJECT_STORAGE_SECRET_KEY", "").strip()
_BUCKET     = os.getenv("OBJECT_STORAGE_BUCKET", "").strip()


def is_configured() -> bool:
    """Return True when all required object-storage env vars are set."""
    return bool(_ENDPOINT and _ACCESS_KEY and _SECRET_KEY and _BUCKET)


def _is_digitalocean_spaces() -> bool:
    return "digitaloceanspaces.com" in _ENDPOINT.lower()


def _normalize_spaces_endpoint() -> tuple[str, Optional[str], bool]:
    """Normalize OBJECT_STORAGE_ENDPOINT to the canonical region endpoint."""
    raw = _ENDPOINT.strip().rstrip("/")
    secure = not raw.startswith("http://")
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"

    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()

    if not host.endswith(".digitaloceanspaces.com"):
        return raw, None, secure

    prefix_parts = [
        p for p in host[: -len(".digitaloceanspaces.com")].split(".")
        if p and p != "cdn"
    ]
    if not prefix_parts:
        return raw, None, secure

    region = prefix_parts[-1]
    canonical = f"{'https' if secure else 'http'}://{region}.digitaloceanspaces.com"
    return canonical, region, secure


def _resolve_endpoint_and_region() -> tuple[str, str]:
    """Return (endpoint_url, region) for boto3."""
    if _is_digitalocean_spaces():
        endpoint_url, parsed_region, _secure = _normalize_spaces_endpoint()
        explicit = os.getenv("OBJECT_STORAGE_REGION", "").strip()
        region = explicit or parsed_region or ""
        if not region:
            raise RuntimeError(
                "Could not infer Spaces region from OBJECT_STORAGE_ENDPOINT. "
                "Use https://<region>.digitaloceanspaces.com (e.g. sgp1) "
                "or set OBJECT_STORAGE_REGION."
            )
        return endpoint_url, region

    raw = _ENDPOINT.strip().rstrip("/")
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    region = os.getenv("OBJECT_STORAGE_REGION", "").strip() or "us-east-1"
    return raw, region


def _get_client():
    """Return a configured boto3 S3 client pointed at the configured endpoint."""
    if not is_configured():
        raise RuntimeError(
            "Object storage is not configured. "
            "Set OBJECT_STORAGE_ENDPOINT, OBJECT_STORAGE_ACCESS_KEY, "
            "OBJECT_STORAGE_SECRET_KEY, and OBJECT_STORAGE_BUCKET."
        )

    import boto3
    from botocore.client import Config

    endpoint_url, region = _resolve_endpoint_and_region()

    return boto3.client(
        "s3",
        region_name=region,
        endpoint_url=endpoint_url,
        aws_access_key_id=_ACCESS_KEY,
        aws_secret_access_key=_SECRET_KEY,
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


# ── Public helpers ────────────────────────────────────────────────────────────

def upload_file(
    file_stream: IO[bytes],
    storage_key: str,
    content_type: Optional[str] = None,
    length: int = -1,
) -> str:
    """Upload *file_stream* to the configured Space under *storage_key*."""
    del length

    client = _get_client()
    mime = content_type or "application/octet-stream"

    try:
        client.upload_fileobj(
            Fileobj=file_stream,
            Bucket=_BUCKET,
            Key=storage_key,
            ExtraArgs={"ContentType": mime},
        )
        logger.info(f"[cargo.object_storage] Uploaded → {_BUCKET}/{storage_key}")
        return storage_key
    except Exception as exc:
        logger.error(
            f"[cargo.object_storage] Upload failed for key '{storage_key}': {exc}",
            exc_info=True,
        )
        raise RuntimeError(f"Cargo object storage upload failed: {exc}") from exc


def delete_file(storage_key: str) -> bool:
    """Delete *storage_key* from the Space. Errors logged, never raised."""
    if not storage_key:
        return False

    try:
        client = _get_client()
        client.delete_object(Bucket=_BUCKET, Key=storage_key)
        logger.info(f"[cargo.object_storage] Deleted → {_BUCKET}/{storage_key}")
        return True
    except Exception as exc:
        logger.error(
            f"[cargo.object_storage] Delete failed for key '{storage_key}': {exc}",
            exc_info=True,
        )
        return False


def get_public_url(storage_key: str) -> str:
    """Canonical public HTTPS URL for *storage_key*."""
    if _is_digitalocean_spaces():
        endpoint_url, _region, _secure = _normalize_spaces_endpoint()
        return f"{endpoint_url}/{_BUCKET}/{storage_key}"
    return f"{_ENDPOINT}/{_BUCKET}/{storage_key}"


def generate_presigned_put_url(
    storage_key: str,
    expiry_seconds: int = 900,
    content_type: Optional[str] = None,
) -> str:
    """Pre-signed PUT URL for direct browser → Space upload."""
    ct = (content_type or "").strip() or "application/octet-stream"

    client = _get_client()
    try:
        url = client.generate_presigned_url(
            "put_object",
            Params={"Bucket": _BUCKET, "Key": storage_key, "ContentType": ct},
            ExpiresIn=min(int(expiry_seconds), 604800),
            HttpMethod="PUT",
        )
        logger.info(f"[cargo.object_storage] Pre-signed PUT URL for '{storage_key}'")
        return url
    except Exception as exc:
        logger.error(
            f"[cargo.object_storage] Pre-signed PUT URL failed for '{storage_key}': {exc}",
            exc_info=True,
        )
        raise RuntimeError(f"Could not generate pre-signed PUT URL: {exc}") from exc


def download_file(storage_key: str) -> bytes:
    """Download an object from the Space and return raw bytes."""
    client = _get_client()
    try:
        resp = client.get_object(Bucket=_BUCKET, Key=storage_key)
        data = resp["Body"].read()
        logger.info(f"[cargo.object_storage] Downloaded {len(data)} bytes from '{storage_key}'")
        return data
    except Exception as exc:
        logger.error(
            f"[cargo.object_storage] Download failed for key '{storage_key}': {exc}",
            exc_info=True,
        )
        raise RuntimeError(f"Cargo object storage download failed: {exc}") from exc


def generate_presigned_url(storage_key: str, expiry_seconds: int = 3600) -> str:
    """Pre-signed GET URL for time-limited download access."""
    client = _get_client()
    try:
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": _BUCKET, "Key": storage_key},
            ExpiresIn=min(int(expiry_seconds), 604800),
            HttpMethod="GET",
        )
    except Exception as exc:
        logger.error(
            f"[cargo.object_storage] Pre-signed URL failed for '{storage_key}': {exc}",
            exc_info=True,
        )
        raise RuntimeError(f"Could not generate pre-signed URL: {exc}") from exc


def build_storage_key(client_id: str, vessel_id: int, filename: str) -> str:
    """
    Namespaced object key for a cargo manifest upload.

    Format: cargo/{client_id}/{vessel_id}/{filename}
    Example: cargo/vesfleet/42/M_1103995_Pluto_1_packing_list.xlsx

    Note this differs from documents/object_storage.py's signature
    (which takes only client_id + filename) — cargo files are scoped
    per-vessel because manifests are vessel-specific.
    """
    safe_filename = re.sub(r"[^\w.\-]", "_", filename)
    safe_client   = re.sub(r"[^\w.\-]", "_", str(client_id))
    safe_vessel   = re.sub(r"[^\w.\-]", "_", str(vessel_id))
    return f"cargo/{safe_client}/{safe_vessel}/{safe_filename}"
