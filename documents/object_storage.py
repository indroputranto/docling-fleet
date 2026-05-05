#!/usr/bin/env python3
"""
DigitalOcean Spaces (S3-compatible) object storage helper.

Uses **boto3** for all operations — DigitalOcean officially documents
SigV4 + virtual-hosted addressing for Spaces with the AWS SDKs, and the
same client handles uploads, downloads, deletes, and presigned URLs.

Reads configuration from environment variables:
  OBJECT_STORAGE_ENDPOINT   — e.g. https://sgp1.digitaloceanspaces.com
  OBJECT_STORAGE_ACCESS_KEY — Spaces access key ID
  OBJECT_STORAGE_SECRET_KEY — Spaces secret access key
  OBJECT_STORAGE_BUCKET     — Space (bucket) name
  OBJECT_STORAGE_REGION     — optional SigV4 region override

The endpoint may be the region endpoint, the bucket origin, or the CDN form;
``_normalize_spaces_endpoint`` collapses them all to
``https://<region>.digitaloceanspaces.com`` so virtual-hosted addressing
produces ``<bucket>.<region>.digitaloceanspaces.com`` (no doubled bucket name).

Public API:
  upload_file(file_stream, storage_key, content_type=None, length=-1) -> str
  delete_file(storage_key) -> bool
  download_file(storage_key) -> bytes
  get_public_url(storage_key) -> str
  generate_presigned_url(storage_key, expiry_seconds=3600) -> str
  generate_presigned_put_url(storage_key, expiry_seconds=900, content_type=None) -> str
  build_storage_key(client_id, filename) -> str
  is_configured() -> bool
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
    """
    Normalize ``OBJECT_STORAGE_ENDPOINT`` for DigitalOcean Spaces.

    Accepts every shape DO surfaces:
      • ``https://sgp1.digitaloceanspaces.com``           (preferred, region endpoint)
      • ``https://<bucket>.sgp1.digitaloceanspaces.com``  (bucket origin)
      • ``https://sgp1.cdn.digitaloceanspaces.com``       (CDN region)
      • ``https://<bucket>.sgp1.cdn.digitaloceanspaces.com`` (CDN origin)

    Returns ``(endpoint_url, region, secure)`` where ``endpoint_url`` is always
    the canonical region endpoint ``https://<region>.digitaloceanspaces.com``.
    """
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
    length: int = -1,  # kept for API compatibility; boto3 streams without it
) -> str:
    """
    Upload *file_stream* to the configured Space under *storage_key*.

    Returns the *storage_key* on success — store this in ``Document.storage_key``.

    Raises
    ------
    RuntimeError
        When the upload fails.
    """
    del length  # unused; boto3's upload_fileobj handles streaming + multipart

    client = _get_client()
    mime = content_type or "application/octet-stream"

    try:
        client.upload_fileobj(
            Fileobj=file_stream,
            Bucket=_BUCKET,
            Key=storage_key,
            ExtraArgs={"ContentType": mime},
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
    """Delete *storage_key* from the Space. Errors are logged but never raised."""
    if not storage_key:
        return False

    try:
        client = _get_client()
        client.delete_object(Bucket=_BUCKET, Key=storage_key)
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

    Format: {region_endpoint}/{bucket}/{key}
    Example: https://sgp1.digitaloceanspaces.com/vesfleet-docs/documents/...
    """
    if _is_digitalocean_spaces():
        endpoint_url, _region, _secure = _normalize_spaces_endpoint()
        return f"{endpoint_url}/{_BUCKET}/{storage_key}"
    return f"{_ENDPOINT}/{_BUCKET}/{storage_key}"


def generate_presigned_put_url(
    storage_key: str,
    expiry_seconds: int = 900,
    content_type: Optional[str] = None,
) -> str:
    """
    Generate a pre-signed PUT URL so a browser can upload a file directly
    to the Space without routing the binary payload through the Flask server.

    *content_type* is included in the SigV4 signature so the browser must
    send the same ``Content-Type`` header on PUT.
    """
    ct = (content_type or "").strip() or "application/octet-stream"

    client = _get_client()
    try:
        url = client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": _BUCKET,
                "Key": storage_key,
                "ContentType": ct,
            },
            ExpiresIn=min(int(expiry_seconds), 604800),
            HttpMethod="PUT",
        )
        logger.info(f"[object_storage] Pre-signed PUT URL generated for '{storage_key}'")
        return url
    except Exception as exc:
        logger.error(
            f"[object_storage] Pre-signed PUT URL generation failed for '{storage_key}': {exc}",
            exc_info=True,
        )
        raise RuntimeError(f"Could not generate pre-signed PUT URL: {exc}") from exc


def download_file(storage_key: str) -> bytes:
    """
    Download an object from the Space and return its raw bytes.

    Used by the server-side processing pipeline to retrieve a file that was
    uploaded directly by the browser via a pre-signed PUT URL.
    """
    client = _get_client()
    try:
        resp = client.get_object(Bucket=_BUCKET, Key=storage_key)
        data = resp["Body"].read()
        logger.info(f"[object_storage] Downloaded {len(data)} bytes from '{storage_key}'")
        return data
    except Exception as exc:
        logger.error(
            f"[object_storage] Download failed for key '{storage_key}': {exc}",
            exc_info=True,
        )
        raise RuntimeError(f"Object storage download failed: {exc}") from exc


def generate_presigned_url(storage_key: str, expiry_seconds: int = 3600) -> str:
    """
    Generate a time-limited pre-signed GET URL so a document can be downloaded
    directly from the Space without exposing permanent credentials.
    """
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
            f"[object_storage] Pre-signed URL generation failed for '{storage_key}': {exc}",
            exc_info=True,
        )
        raise RuntimeError(f"Could not generate pre-signed URL: {exc}") from exc


def build_storage_key(client_id: str, filename: str) -> str:
    """
    Build a namespaced object key for a document upload.

    Format: documents/{client_id}/{filename}
    Example: documents/ocean7/fixture_recap_mv_aurora.pdf
    """
    safe_filename = re.sub(r"[^\w.\-]", "_", filename)
    safe_client   = re.sub(r"[^\w.\-]", "_", client_id)
    return f"documents/{safe_client}/{safe_filename}"
