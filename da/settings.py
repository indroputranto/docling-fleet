"""Environment configuration for DA-Desk / Marcura credentials."""

from __future__ import annotations

import os


def da_desk_api_key() -> str:
    """Bearer token clients send (e.g. Langdock action auth.apiKey)."""
    return (os.getenv("DA_DESK_API_KEY") or os.getenv("API_KEY") or "").strip()


def marcura_username() -> str:
    return (os.getenv("DA_DESK_USERNAME") or os.getenv("USERNAME") or "").strip()


def marcura_password() -> str:
    return (os.getenv("DA_DESK_PASSWORD") or os.getenv("PASSWORD") or "").strip()


def marcura_host() -> str:
    return (os.getenv("DA_DESK_HOST") or os.getenv("HOST") or "https://api.marcura.com").strip().rstrip("/")


def operator_id() -> str:
    return (os.getenv("DA_DESK_OPERATOR_ID") or os.getenv("OPERATOR_ID") or "").strip()


def service_id() -> str:
    return (os.getenv("DA_DESK_SERVICE_ID") or os.getenv("SERVICE_ID") or "").strip()


def service_version() -> str:
    return (os.getenv("DA_DESK_SERVICE_VERSION") or os.getenv("SERVICE_VERSION") or "").strip()


def marcura_configured() -> bool:
    return bool(
        marcura_username()
        and marcura_password()
        and operator_id()
        and service_id()
        and service_version()
    )


def da_proxy_enabled() -> bool:
    """Require API key plus full Marcura credentials before serving DA routes."""
    return bool(da_desk_api_key() and marcura_configured())
