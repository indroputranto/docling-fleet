#!/usr/bin/env python3
"""
DigitalOcean Spaces (S3-compatible) object storage helper.

Uses MinIO for server-side object I/O and **boto3** for presigned URLs when the
endpoint is DigitalOcean Spaces (SigV4 + virtual-hosted style per DO docs).

Reads configuration from environment variables:
  OBJECT_STORAGE_ENDPOINT   — e.g. https://sgp1.digitaloceanspaces.com
  OBJECT_STORAGE_ACCESS_KEY — Spaces access key ID
  OBJECT_STORAGE_SECRET_KEY — Spaces secret access key
  OBJECT_STORAGE_BUCKET     — Space (bucket) name
  OBJECT_STORAGE_REGION     — optional SigV4 region override

DigitalOcean Spaces: the MinIO client must use the datacenter slug as the API
region (``sin1``, ``sgp1``, ``nyc3``, …). That slug matches the subdomain of
``OBJECT_STORAGE_ENDPOINT``. If unset, it is inferred from ``*.digitaloceanspaces.com``.
Without this, presigned URLs trigger GetBucketLocation signed as ``us-east-1`` and
Spaces responds with ``SignatureDoesNotMatch``.

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


def _infer_spaces_region(host: str) -> Optional[str]:
    """
    Parse DigitalOcean Spaces datacenter slug from the endpoint hostname.

    e.g. sgp1.digitaloceanspaces.com → sgp1
    """
    m = re.match(r"^([a-z0-9-]+)\.digitaloceanspaces\.com$", host.strip(), flags=re.I)
    return m.group(1).lower() if m else None


def _digitalocean_endpoint_url() -> str:
    """Normalize OBJECT_STORAGE_ENDPOINT to a URL string."""
    e = _ENDPOINT.strip().rstrip("/")
    if e.startswith(("http://", "https://")):
        return e
    return f"https://{e}"


def _endpoint_hostname() -> str:
    raw = _ENDPOINT.strip().rstrip("/")
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    host = urlparse(raw).hostname
    return (host or "").lower()


def _is_digitalocean_spaces() -> bool:
    return "digitaloceanspaces.com" in _ENDPOINT.lower()


def _spaces_sigv4_region(host: str) -> str:
    """Region name used in SigV4 for Spaces — matches datacenter slug from endpoint."""
    explicit = os.getenv("OBJECT_STORAGE_REGION", "").strip()
    if explicit:
        return explicit
    inferred = _infer_spaces_region(host)
    if inferred:
        return inferred
    raise RuntimeError(
        "Could not infer Spaces region from OBJECT_STORAGE_ENDPOINT. "
        "Use https://<region>.digitaloceanspaces.com (e.g. sin1) or set OBJECT_STORAGE_REGION."
    )


def _presigned_put_boto3(storage_key: str, expiry_seconds: int, content_type: str) -> str:
    """Presigned PUT using boto3 — DigitalOcean documents this path for Spaces."""
    import boto3
    from botocore.client import Config

    host = _endpoint_hostname()
    region = _spaces_sigv4_region(host)
    endpoint_url = _digitalocean_endpoint_url().rstrip("/")

    client = boto3.client(
        "s3",
        region_name=region,
        endpoint_url=endpoint_url,
        aws_access_key_id=_ACCESS_KEY,
        aws_secret_access_key=_SECRET_KEY,
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )
    url = client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": _BUCKET,
            "Key": storage_key,
            "ContentType": content_type,
        },
        ExpiresIn=min(expiry_seconds, 604800),
        HttpMethod="PUT",
    )
    logger.info(
        f"[object_storage] boto3 pre-signed PUT for '{storage_key}' "
        f"(region={region}, endpoint={endpoint_url})"
    )
    return url


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

    explicit_region = os.getenv("OBJECT_STORAGE_REGION", "").strip()
    region = explicit_region or _infer_spaces_region(host)

    return Minio(
        host,
        access_key=_ACCESS_KEY,
        secret_key=_SECRET_KEY,
        secure=secure,
        region=region,
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


def generate_presigned_put_url(
    storage_key: str,
    expiry_seconds: int = 900,
    content_type: Optional[str] = None,
) -> str:
    """
    Generate a pre-signed PUT URL so a browser can upload a file directly
    to the Space without routing the binary payload through the Flask server.

    Intended for large files (> 3 MB) that would exceed Vercel's serverless
    function request-body limit.  The URL expires after *expiry_seconds*
    (default: 15 minutes).

    *content_type* is included in the SigV4 signature where supported so the
    browser must send the same ``Content-Type`` header on PUT (match the value
    sent to ``/documents/presign``).

    DigitalOcean Spaces uses boto3 for presign (official SigV4 + virtual-hosted
    style). Other S3-compatible endpoints keep MinIO presign.

    Parameters
    ----------
    storage_key : str
        The object key the file will be stored under.
    expiry_seconds : int
        How long (in seconds) the URL remains valid.
    content_type : str, optional
        MIME type for the PUT (defaults to ``application/octet-stream``).

    Returns
    -------
    str
        A pre-signed HTTPS URL accepting HTTP PUT requests.
    """
    ct = (content_type or "").strip() or "application/octet-stream"

    if _is_digitalocean_spaces():
        try:
            return _presigned_put_boto3(storage_key, expiry_seconds, ct)
        except Exception as exc:
            logger.error(
                f"[object_storage] boto3 pre-signed PUT failed for '{storage_key}': {exc}",
                exc_info=True,
            )
            raise RuntimeError(f"Could not generate pre-signed PUT URL: {exc}") from exc

    from datetime import timedelta

    client = _get_client()
    try:
        url = client.presigned_put_object(
            _BUCKET,
            storage_key,
            expires=timedelta(seconds=expiry_seconds),
        )
        logger.info(f"[object_storage] MinIO pre-signed PUT for '{storage_key}'")
        return url
    except Exception as exc:
        logger.error(
            f"[object_storage] MinIO pre-signed PUT failed for '{storage_key}': {exc}",
            exc_info=True,
        )
        raise RuntimeError(f"Could not generate pre-signed PUT URL: {exc}") from exc


def download_file(storage_key: str) -> bytes:
    """
    Download an object from the Space and return its raw bytes.

    Used by the server-side processing pipeline to retrieve a file that was
    uploaded directly by the browser via a pre-signed PUT URL.

    Parameters
    ----------
    storage_key : str
        The object key to download.

    Returns
    -------
    bytes
        The full file contents.

    Raises
    ------
    RuntimeError
        When the download fails.
    """
    client = _get_client()
    try:
        response = client.get_object(_BUCKET, storage_key)
        data = response.read()
        response.close()
        response.release_conn()
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
