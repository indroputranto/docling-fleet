"""Langdock-compatible DA-Desk REST routes mounted under `/api`."""

from __future__ import annotations

import datetime as _dt
import logging
from functools import wraps

from flask import Blueprint, jsonify, request

from da import handlers
from da import settings as da_settings

logger = logging.getLogger(__name__)

da_bp = Blueprint("da_desk", __name__)


def require_da_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not da_settings.da_proxy_enabled():
            return jsonify(
                {
                    "error": (
                        "DA-Desk API is not configured. Set DA_DESK_API_KEY (or API_KEY) and "
                        "DA_DESK_USERNAME, DA_DESK_PASSWORD, DA_DESK_HOST, DA_DESK_OPERATOR_ID, "
                        "DA_DESK_SERVICE_ID, DA_DESK_SERVICE_VERSION "
                        "(or the legacy USERNAME, PASSWORD, HOST, OPERATOR_ID, SERVICE_ID, SERVICE_VERSION)."
                    )
                }
            ), 503

        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return jsonify({"error": "Missing Authorization header"}), 401

        if auth_header.startswith("Bearer "):
            provided = auth_header.split(" ", 1)[1].strip()
        else:
            provided = auth_header.strip()

        if provided != da_settings.da_desk_api_key():
            return jsonify({"error": "Invalid API key"}), 401

        return f(*args, **kwargs)

    return decorated


@da_bp.route("/api/da-search", methods=["POST"])
@require_da_api_key
def route_da_search():
    try:
        body = request.get_json(silent=True) or {}
        query = body.get("query", "")
        if not query:
            return jsonify({"error": "Query parameter is required"}), 400
        payload, code = handlers.api_da_search(query)
        return jsonify(payload), code
    except Exception as e:
        logger.exception("[DA] da-search failed")
        return jsonify({"error": str(e)}), 500


@da_bp.route("/api/da-search-vessels", methods=["POST"])
@require_da_api_key
def route_da_search_vessels():
    try:
        body = request.get_json(silent=True) or {}
        query = body.get("query", "")
        page = int(body.get("page", 1))
        per_page = int(body.get("per_page", 50))
        if not query:
            return jsonify({"error": "Query parameter is required"}), 400
        payload, code = handlers.api_da_search_vessels(query, page, per_page)
        return jsonify(payload), code
    except Exception as e:
        logger.exception("[DA] da-search-vessels failed")
        return jsonify({"error": str(e)}), 500


@da_bp.route("/api/port-vessels/<path:port_name>", methods=["GET"])
@require_da_api_key
def route_port_vessels(port_name):
    try:
        from_year = request.args.get("fromYear")
        to_year = request.args.get("toYear")
        smart = request.args.get("smartPortMatch", "true").lower() != "false"
        payload, code = handlers.api_port_vessels(port_name, from_year, to_year, smart)
        return jsonify(payload), code
    except Exception as e:
        logger.exception("[DA] port-vessels failed")
        return jsonify(
            {"success": False, "error": f"Failed to retrieve vessels for {port_name}: {e}"}
        ), 500


@da_bp.route("/api/vessel-cost/<path:vessel_name>", methods=["GET"])
@require_da_api_key
def route_vessel_cost(vessel_name):
    try:
        payload, code = handlers.api_vessel_cost(vessel_name)
        return jsonify(payload), code
    except Exception as e:
        logger.exception("[DA] vessel-cost failed")
        return jsonify({"found": False, "error": str(e)}), 500


@da_bp.route("/api/vessel-by-reference/<path:reference_number>", methods=["GET"])
@require_da_api_key
def route_vessel_by_reference(reference_number):
    try:
        payload, code = handlers.api_vessel_by_reference(reference_number)
        return jsonify(payload), code
    except Exception as e:
        logger.exception("[DA] vessel-by-reference failed")
        return jsonify({"success": False, "error": str(e)}), 500


@da_bp.route("/api/da-details/<int:da_id>", methods=["GET"])
@require_da_api_key
def route_da_details(da_id):
    try:
        stage = request.args.get("stage", "PDA")
        persona = request.args.get("persona", "OPERATOR")
        payload, code = handlers.api_da_details(da_id, stage, persona)
        return jsonify(payload), code
    except Exception as e:
        logger.exception("[DA] da-details failed")
        return jsonify({"error": str(e)}), 500


@da_bp.route("/api/da-details/<int:da_id>/cost-details", methods=["GET"])
@require_da_api_key
def route_cost_details(da_id):
    try:
        stage = request.args.get("stage", "PDA")
        persona = request.args.get("persona", "OPERATOR")
        payload, code = handlers.api_cost_details(da_id, stage, persona)
        return jsonify(payload), code
    except Exception as e:
        logger.exception("[DA] cost-details failed")
        return jsonify({"error": str(e)}), 500


@da_bp.route("/api/vessel-lookup/<path:vessel_name>", methods=["GET"])
@require_da_api_key
def route_vessel_lookup(vessel_name):
    try:
        payload, code = handlers.api_vessel_lookup(vessel_name)
        return jsonify(payload), code
    except Exception as e:
        logger.exception("[DA] vessel-lookup failed")
        return jsonify({"error": str(e)}), 500


@da_bp.route("/api/vessel-search/<path:vessel_name>", methods=["GET"])
@require_da_api_key
def route_vessel_search(vessel_name):
    try:
        payload, code = handlers.api_vessel_search(vessel_name)
        return jsonify(payload), code
    except Exception as e:
        logger.exception("[DA] vessel-search failed")
        return jsonify({"error": str(e)}), 500


@da_bp.route("/api/example-queries", methods=["GET"])
@require_da_api_key
def route_example_queries():
    return jsonify(
        {
            "examples": [
                "Show me the DA for the port [PORT_NAME]",
                "Find vessels in [PORT_NAME] port",
                "Get DA details for vessel [VESSEL_NAME]",
                "Show DAs for [PORT_NAME] port",
                "Find all DAs for [DATE]",
            ]
        }
    )


@da_bp.route("/api/info", methods=["GET"])
@require_da_api_key
def route_api_info():
    return jsonify(
        {
            "name": "DA-Desk API (embedded in docling)",
            "description": "Marcura DA search and details — Langdock-compatible paths",
            "version": "1.0.0",
            "endpoints": {
                "da_search": "/api/da-search",
                "da_search_vessels": "/api/da-search-vessels",
                "port_vessels": "/api/port-vessels/{port_name}",
                "vessel_cost": "/api/vessel-cost/{vessel_name}",
                "vessel_by_reference": "/api/vessel-by-reference/{reference_number}",
                "da_details": "/api/da-details/{da_id}",
                "cost_details": "/api/da-details/{da_id}/cost-details",
            },
            "status": "running",
        }
    )


@da_bp.route("/api/status", methods=["GET"])
def route_status():
    """Public lightweight status (same path as standalone DA-Desk)."""
    return jsonify(
        {
            "status": "ok",
            "timestamp": _dt.datetime.now().isoformat(),
            "service": "da-desk-api",
            "configured": da_settings.marcura_configured()
            and bool(da_settings.da_desk_api_key()),
        }
    )
