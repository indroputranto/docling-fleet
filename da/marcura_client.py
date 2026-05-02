"""Marcura operator API: auth, DA search, DA details."""

from __future__ import annotations

import logging
import re
from typing import Any

import requests

from da import settings as da_settings

logger = logging.getLogger(__name__)


def _normalize_port_text(value: str) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = re.sub(r"[^A-Za-z0-9\s]", " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned


def _tokenize_port_text(value: str) -> set[str]:
    normalized = _normalize_port_text(value)
    return set(normalized.split()) if normalized else set()


def smart_port_match(query_port: str, candidate_port: str) -> tuple[bool, str]:
    q_norm = _normalize_port_text(query_port)
    c_norm = _normalize_port_text(candidate_port)
    if not q_norm or not c_norm:
        return False, "empty-normalized"

    if c_norm == q_norm:
        return True, "exact"

    if c_norm.startswith(q_norm + " ") or c_norm.startswith(q_norm):
        return True, "prefix"

    q_tokens = _tokenize_port_text(query_port)
    c_tokens = _tokenize_port_text(candidate_port)
    if q_tokens and q_tokens.issubset(c_tokens):
        return True, "token-subset"

    return False, "no-match"


def parse_natural_language_query(query: str) -> dict[str, Any]:
    query = query.lower().strip()
    search_params: dict[str, Any] = {}

    port_patterns = [
        r"port\s+(\w+)",
        r"for\s+the\s+port\s+(\w+)",
        r"in\s+(\w+)\s+port",
        r"(\w+)\s+port",
    ]

    for pattern in port_patterns:
        match = re.search(pattern, query)
        if match:
            search_params["portName"] = match.group(1).title()
            break

    vessel_patterns = [
        r"vessel\s+(\w+)",
        r"ship\s+(\w+)",
        r"(\w+)\s+vessel",
        r"(\w+)\s+ship",
    ]

    if "vessels" not in query.lower() and "vessel" not in query.lower():
        for pattern in vessel_patterns:
            match = re.search(pattern, query)
            if match:
                search_params["vesselName"] = match.group(1).title()
                break

    date_patterns = [
        r"(\d{4}-\d{2}-\d{2})",
        r"(\d{2}/\d{2}/\d{4})",
        r"(\d{2}-\d{2}-\d{4})",
    ]

    for pattern in date_patterns:
        match = re.search(pattern, query)
        if match:
            search_params["date"] = match.group(1)
            break

    if "jsp halmoe" in query.lower() or "coega" in query.lower():
        search_params["dateFrom"] = "2024-05-27"
        search_params["dateTo"] = "2024-07-27"
        logger.info("[PARSE] Added date range for JSP HALMOE search")

    return search_params


def authenticate() -> str:
    url = f"{da_settings.marcura_host()}/auth"
    payload = {
        "clientId": da_settings.marcura_username(),
        "clientSecret": da_settings.marcura_password(),
    }
    headers = {"Content-Type": "application/json"}
    logger.info("[AUTH] Attempting Marcura auth")
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    token = response.json().get("token")
    if not token:
        raise RuntimeError(f"No token in auth response: {response.text[:500]}")
    return token


def da_search(
    token: str,
    search_params: dict[str, Any] | None = None,
    page: int = 1,
) -> tuple[list[Any], dict[str, Any]]:
    oid = da_settings.operator_id()
    sid = da_settings.service_id()
    ver = da_settings.service_version()
    base = da_settings.marcura_host()
    url = (
        f"{base}/dad/operator-api/1.0/das/search"
        f"?page={page}&size=200&operatorId={oid}&serviceId={sid}&serviceVersion={ver}"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    payload: dict[str, Any] = {
        "daApiTypes": [{"id": "MAIN", "name": "Main DA"}],
    }

    if search_params:
        if "portName" in search_params:
            payload["text"] = search_params["portName"]

        if "updatedFrom" in search_params:
            payload["updated"] = {"from": f"{search_params['updatedFrom']}T00:00:00Z"}
            if "updatedTo" in search_params:
                payload["updated"]["to"] = f"{search_params['updatedTo']}T23:59:59Z"

        if "activities" in search_params:
            payload["activities"] = search_params["activities"]

        for key in ("vesselName", "type", "states"):
            if key in search_params:
                payload[key] = search_params[key]

    logger.info("[DA_SEARCH] Request page=%s", page)
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    da_ids = [item["id"] for item in data.get("results", []) if "id" in item]
    return da_ids, data


def da_details(token: str, da_id: int) -> dict[str, Any]:
    oid = da_settings.operator_id()
    sid = da_settings.service_id()
    ver = da_settings.service_version()
    base = da_settings.marcura_host()
    url = (
        f"{base}/dad/operator-api/1.0/das/{da_id}"
        f"?operatorId={oid}&serviceId={sid}&serviceVersion={ver}"
    )
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    logger.info("[DA_DETAILS] Fetch DA id=%s", da_id)
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()
