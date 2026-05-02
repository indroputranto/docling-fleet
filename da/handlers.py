"""Business logic for DA-Desk-compatible endpoints (ported from ocean7 DA-Desk main.py)."""

from __future__ import annotations

import datetime
import logging
import re
from typing import Any

from da import breakdown as bd
from da import marcura_client as mc

logger = logging.getLogger(__name__)


def _normalize_ref(value: str) -> str:
    if not isinstance(value, str):
        value = str(value or "")
    value = re.sub(r"[\u2010-\u2015]", "-", value)
    return value.strip().upper()


def api_da_search(query: str) -> tuple[dict[str, Any], int]:
    search_params = mc.parse_natural_language_query(query)
    logger.info("[API] Parsed search params: %s", search_params)
    token = mc.authenticate()
    try:
        _, search_data = mc.da_search(token, search_params)
    except Exception as e:
        logger.warning("[API] Search with params failed: %s — retry without", e)
        _, search_data = mc.da_search(token, None)

    da_ids = [item["id"] for item in search_data.get("results", []) if "id" in item]
    results: list[dict[str, Any]] = []
    for da_id in da_ids[:3]:
        try:
            details = mc.da_details(token, int(da_id))
            search_result = next(
                (item for item in search_data.get("results", []) if item.get("id") == da_id),
                None,
            )
            if search_result:
                details["vessel"] = search_result.get("vessel", {})
                details["port"] = search_result.get("port", {})
            results.append({"da_id": da_id, "details": details})
        except Exception as e:
            logger.error("[API] Error getting details for DA %s: %s", da_id, e)

    return (
        {
            "query": query,
            "search_params": search_params,
            "total_results": len(results),
            "results": results,
        },
        200,
    )


def api_da_search_vessels(
    query: str,
    page: int,
    per_page: int,
) -> tuple[dict[str, Any], int]:
    search_params = mc.parse_natural_language_query(query)
    token = mc.authenticate()
    _, search_data = mc.da_search(token, search_params)
    vessels: list[dict[str, Any]] = []
    for item in search_data.get("results", []):
        vessels.append(
            {
                "da_id": item.get("id"),
                "vessel_name": item.get("vessel", {}).get("name", "Unknown Vessel"),
                "reference_number": item.get("referenceNumber", "N/A"),
                "status": item.get("status", {}).get("name", "Unknown Status"),
                "type": item.get("type", "Unknown Type"),
                "subtype": item.get("subtype", "Unknown Subtype"),
                "created_date": item.get("createdDate", "N/A"),
            }
        )

    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    paginated = vessels[start_idx:end_idx]
    total_pages = max(1, (len(vessels) + per_page - 1) // per_page)

    vessel_list = []
    for i, vessel in enumerate(paginated, start_idx + 1):
        vessel_list.append(f"{i:2d}. {vessel['vessel_name']:<20} (ID: {vessel['da_id']})")
    formatted_vessels = "\n".join(vessel_list)
    pagination_info = f"Page {page} of {total_pages} (showing {len(paginated)} of {len(vessels)} vessels)"
    summary = (
        f"{pagination_info}\n\n{formatted_vessels}\n\n"
        "💡 To get detailed cost breakdown for a specific vessel, ask: "
        "'Get details for vessel [VESSEL_NAME]'"
    )

    return (
        {
            "query": query,
            "total_vessels": len(vessels),
            "vessels": paginated,
            "formatted_vessels": formatted_vessels,
            "message": f"Found {len(vessels)} vessels",
            "summary": summary,
            "compact_summary": f"All {len(vessels)} vessels:\n{formatted_vessels}",
            "pagination": {
                "current_page": page,
                "total_pages": total_pages,
                "per_page": per_page,
                "total_vessels": len(vessels),
                "showing": len(paginated),
            },
        },
        200,
    )


def api_port_vessels(
    port_name: str,
    from_year_param: str | None,
    to_year_param: str | None,
    smart_match_enabled: bool,
) -> tuple[dict[str, Any], int]:
    requested_port_original = port_name
    current_year = datetime.datetime.now().year
    updated_from = f"{current_year}-01-01"
    updated_to: str | None = None

    if (
        from_year_param
        and str(from_year_param).isdigit()
        and len(str(from_year_param)) == 4
    ):
        updated_from = f"{from_year_param}-01-01"
        if (
            to_year_param
            and str(to_year_param).isdigit()
            and len(str(to_year_param)) == 4
        ):
            updated_to = f"{to_year_param}-12-31"
        else:
            updated_to = f"{from_year_param}-12-31"

    token = mc.authenticate()
    all_results: list[dict[str, Any]] = []
    page = 1
    max_pages = 20

    while page <= max_pages:
        search_params: dict[str, Any] = {
            "portName": port_name,
            "updatedFrom": updated_from,
            "page": page,
            "size": 50,
            "activities": [
                {"id": 1110, "name": "Loading"},
                {"id": 1111, "name": "Discharging"},
            ],
        }
        if updated_to:
            search_params["updatedTo"] = updated_to

        try:
            _, search_data = mc.da_search(token, search_params, page)
            if not search_data.get("results"):
                break
            all_results.extend(search_data.get("results", []))
            page += 1
        except Exception as e:
            logger.error("[API] Error fetching page %s: %s", page, e)
            break

    def _item_to_vessel_row(item: dict[str, Any]) -> dict[str, Any]:
        status = item.get("status", {})
        status_name = "Unknown Status"
        if isinstance(status, dict):
            status_name = status.get("name", "Unknown Status")
        elif isinstance(status, str):
            status_name = status
        else:
            status_name = item.get("state", item.get("status_name", "Unknown Status"))

        reference_number = item.get(
            "reference", item.get("referenceNumber", item.get("ref", "N/A"))
        )
        vessel_name = item.get("vessel", {}).get(
            "name", item.get("vesselName", "Unknown Vessel")
        )
        da_id = item.get("id", item.get("daId", "N/A"))
        port_info = item.get("port", {})
        port_name_from_item = (
            port_info.get("name", "Unknown")
            if isinstance(port_info, dict)
            else "Unknown"
        )
        eta_ata = item.get("eta", item.get("ata", item.get("etaAta", "N/A")))
        state = item.get("state", "Unknown")
        activities = item.get("activities", [])
        activity_names: list[str] = []
        main_activity = "N/A"
        if isinstance(activities, list):
            for activity in activities:
                activity_name = None
                if isinstance(activity, dict):
                    if "type" in activity and isinstance(activity["type"], dict):
                        activity_name = activity["type"].get("name")
                    elif "name" in activity:
                        activity_name = activity.get("name")
                elif isinstance(activity, str):
                    activity_name = activity
                if activity_name:
                    activity_names.append(activity_name)
                    if activity_name == "Loading":
                        main_activity = "Loading"
                    elif activity_name == "Discharging" and main_activity == "N/A":
                        main_activity = "Discharging"

        return {
            "vessel_name": vessel_name,
            "reference_number": reference_number,
            "da_id": da_id,
            "status": status_name,
            "port_name": port_name_from_item,
            "eta_ata": eta_ata,
            "state": state,
            "main_activity": main_activity,
            "activities": activity_names,
        }

    vessels: list[dict[str, Any]] = []

    for item in all_results:
        row = _item_to_vessel_row(item)
        port_name_from_item = row["port_name"]
        eta_ata = row["eta_ata"]
        state = row["state"]

        eta_ata_in_range = True
        if updated_to:
            try:
                if eta_ata and eta_ata != "N/A":
                    eta_clean = re.sub(r"[+-]\d{2}:\d{2}$", "", str(eta_ata).replace("Z", ""))
                    eta_date = datetime.datetime.fromisoformat(eta_clean)
                    start_year = int(updated_from[:4])
                    end_year = int(updated_to[:4])
                    eta_year = eta_date.year
                    eta_ata_in_range = start_year <= eta_year <= end_year
            except Exception:
                eta_ata_in_range = True

        port_match = port_name_from_item.lower() == port_name.lower()
        state_match = state in ("PDA", "FDA")

        if port_match and state_match and eta_ata_in_range:
            vessels.append(row)

    if smart_match_enabled and len(vessels) == 0 and len(all_results) > 0:
        logger.info(
            "[API] No exact matches for '%s'. Trying smart port match...",
            requested_port_original,
        )
        candidate_counts: dict[str, int] = {}
        for item in all_results:
            c_port = (
                item.get("port", {}).get("name")
                if isinstance(item.get("port"), dict)
                else None
            )
            if not c_port:
                continue
            matched, _reason = mc.smart_port_match(requested_port_original, c_port)
            if matched:
                candidate_counts[c_port] = candidate_counts.get(c_port, 0) + 1

        if candidate_counts:
            best_port = max(candidate_counts.items(), key=lambda kv: kv[1])[0]
            logger.info(
                "[API] Smart match selected port '%s' for '%s'",
                best_port,
                requested_port_original,
            )
            for item in all_results:
                row = _item_to_vessel_row(item)
                port_name_from_item = row["port_name"]
                eta_ata = row["eta_ata"]
                state = row["state"]

                eta_ata_in_range = True
                if updated_to:
                    try:
                        if eta_ata and eta_ata != "N/A":
                            eta_clean = re.sub(
                                r"[+-]\d{2}:\d{2}$", "", str(eta_ata).replace("Z", "")
                            )
                            eta_date = datetime.datetime.fromisoformat(eta_clean)
                            start_year = int(updated_from[:4])
                            end_year = int(updated_to[:4])
                            eta_year = eta_date.year
                            eta_ata_in_range = start_year <= eta_year <= end_year
                    except Exception:
                        eta_ata_in_range = True

                matched, reason = mc.smart_port_match(
                    requested_port_original, port_name_from_item
                )
                if (
                    matched
                    and port_name_from_item == best_port
                    and state in ("PDA", "FDA")
                    and eta_ata_in_range
                ):
                    row["smart_match"] = True
                    row["matched_reason"] = reason
                    row["matched_from_query"] = requested_port_original
                    vessels.append(row)

            if vessels:
                port_name = best_port

    return (
        {
            "success": True,
            "port_name": port_name,
            "date_from": updated_from,
            "date_to": updated_to,
            "total_vessels": len(vessels),
            "vessels": vessels,
            "smart_port_match_enabled": smart_match_enabled,
            "original_query_port": requested_port_original,
            "message": (
                f"Found {len(vessels)} vessels with PDA/FDA state and Loading/Discharging "
                f"activity in {port_name} port (updated {updated_from}"
                f"{' to ' + updated_to if updated_to else ''})"
            ),
        },
        200,
    )


def _collect_paginated_results(token: str, max_pages: int = 20) -> list[dict[str, Any]]:
    all_results: list[dict[str, Any]] = []
    page = 1
    while page <= max_pages:
        try:
            _, search_data = mc.da_search(token, {"page": page, "size": 50}, page)
            if not search_data.get("results"):
                break
            all_results.extend(search_data.get("results", []))
            page += 1
        except Exception as e:
            logger.error("[API] Pagination error page %s: %s", page, e)
            break
    return all_results


def api_vessel_cost(vessel_name: str) -> tuple[dict[str, Any], int]:
    is_reference_number = bool(
        re.match(r"^[A-Z]{2,3}-\d{6,7}(-\d+)?$", vessel_name, re.IGNORECASE)
    )
    actual_vessel_name = vessel_name

    if is_reference_number:
        token = mc.authenticate()
        all_results = _collect_paginated_results(token)
        found_vessel = None
        for item in all_results:
            ref_number = item.get(
                "reference", item.get("referenceNumber", item.get("ref", "N/A"))
            )
            if ref_number == vessel_name:
                found_vessel = item.get("vessel", {}).get("name", "Unknown Vessel")
                break
        if found_vessel:
            actual_vessel_name = found_vessel
        else:
            return (
                {
                    "found": False,
                    "message": (
                        f'Reference number "{vessel_name}" not found. '
                        "Please check the reference number and try again."
                    ),
                    "vessel_name": vessel_name,
                },
                404,
            )

    token = mc.authenticate()
    all_results = _collect_paginated_results(token)

    target_vessel = None
    for item in all_results:
        vessel_info = item.get("vessel", {})
        name_from_item = vessel_info.get("name", "Unknown Vessel")
        if name_from_item.lower() == actual_vessel_name.lower():
            target_vessel = item
            break

    if not target_vessel:
        return (
            {
                "found": False,
                "message": f'No exact match found for vessel "{actual_vessel_name}"',
                "suggestions": [
                    "Check vessel name spelling",
                    "Try searching by port: GET /api/port-vessels/{port_name}",
                    "Use exact vessel name",
                ],
                "vessel_name": actual_vessel_name,
            },
            404,
        )

    da_id = target_vessel.get("id")
    port_info = target_vessel.get("port", {})
    port_nm = (
        port_info.get("name", "Unknown") if isinstance(port_info, dict) else "Unknown"
    )

    try:
        da_raw = mc.da_details(token, int(da_id))
        cost_breakdown_full = bd.build_cost_breakdown_for_stage(da_raw)
        formatted_breakdown, total_amount, currency = bd.compact_positive_cost_rows(
            cost_breakdown_full
        )

        if formatted_breakdown:
            reference_info = (
                f" (Reference: {vessel_name})" if is_reference_number else ""
            )
            return (
                {
                    "vessel_name": actual_vessel_name,
                    "found": True,
                    "da_id": da_id,
                    "port": port_nm,
                    "total_amount": total_amount,
                    "currency": currency,
                    "cost_breakdown": formatted_breakdown,
                    "message": (
                        f"Found cost details for {actual_vessel_name} in {port_nm}"
                        f"{reference_info}"
                    ),
                },
                200,
            )

        return (
            {
                "found": False,
                "message": f'No cost details available for vessel "{actual_vessel_name}"',
                "vessel_name": actual_vessel_name,
            },
            404,
        )
    except Exception as e:
        logger.error("[API] vessel-cost error: %s", e)
        return (
            {
                "found": False,
                "message": f'Error retrieving cost details for vessel "{actual_vessel_name}"',
                "vessel_name": actual_vessel_name,
            },
            500,
        )


def api_vessel_by_reference(reference_number: str) -> tuple[dict[str, Any], int]:
    normalized_target = _normalize_ref(reference_number)
    token = mc.authenticate()
    all_results = _collect_paginated_results(token)

    for item in all_results:
        ref_raw = item.get(
            "reference", item.get("referenceNumber", item.get("ref", "N/A"))
        )
        if _normalize_ref(str(ref_raw)) == normalized_target:
            vessel_name = item.get("vessel", {}).get(
                "name", item.get("vesselName", "Unknown Vessel")
            )
            da_id = item.get("id", item.get("daId", "N/A"))
            port_info = item.get("port", {})
            port_name = (
                port_info.get("name", "Unknown")
                if isinstance(port_info, dict)
                else "Unknown"
            )
            eta_ata = item.get("eta", item.get("ata", item.get("etaAta", "N/A")))
            state = item.get("state", "Unknown")
            activities = item.get("activities", [])
            activity_names: list[str] = []
            if isinstance(activities, list):
                for activity in activities:
                    if isinstance(activity, dict) and "name" in activity:
                        activity_names.append(activity["name"])
                    elif isinstance(activity, str):
                        activity_names.append(activity)

            vessel_payload = {
                "vessel_name": vessel_name,
                "reference_number": ref_raw,
                "da_id": da_id,
                "status": "Unknown Status",
                "port_name": port_name,
                "eta_ata": eta_ata,
                "state": state,
                "activities": activity_names,
            }
            return (
                {
                    "success": True,
                    "reference_number": reference_number,
                    "vessel": vessel_payload,
                    "message": f"Found vessel with reference {reference_number}",
                },
                200,
            )

    return (
        {
            "success": False,
            "reference_number": reference_number,
            "message": f"No vessel found with reference {reference_number}",
        },
        200,
    )


def api_da_details(da_id: int, stage: str, persona: str) -> tuple[dict[str, Any], int]:
    token = mc.authenticate()
    da_raw = mc.da_details(token, da_id)
    vessel_name = da_raw.get("vessel", {}).get("name", "Unknown Vessel")
    reference_number = da_raw.get("referenceNumber", "N/A")
    status = da_raw.get("status", {}).get("name", "Unknown Status")
    cost_breakdown = bd.build_cost_breakdown_for_stage(da_raw, stage=stage, persona=persona)

    return (
        {
            "da_id": da_id,
            "vessel_name": vessel_name,
            "reference_number": reference_number,
            "status": status,
            "total_cost_items": len(cost_breakdown),
            "cost_breakdown": cost_breakdown,
            "full_details": da_raw,
        },
        200,
    )


def api_cost_details(da_id: int, stage: str, persona: str) -> tuple[dict[str, Any], int]:
    token = mc.authenticate()
    da_raw = mc.da_details(token, da_id)
    vessel_name = da_raw.get("vessel", {}).get("name", "Unknown Vessel")
    port_name = da_raw.get("port", {}).get("name", "Unknown Port")
    detailed_breakdown = bd.build_detailed_breakdown(da_raw)

    return (
        {
            "da_id": da_id,
            "vessel_name": vessel_name,
            "port_name": port_name,
            "stage": stage.upper(),
            "persona": persona.upper(),
            "total_cost_items": len(detailed_breakdown),
            "detailed_breakdown": detailed_breakdown,
            "filtered_items": (
                f"Showing {len(detailed_breakdown)} items with meaningful content "
                "(comments or non-zero amounts)"
            ),
        },
        200,
    )


def api_vessel_lookup(vessel_name: str) -> tuple[dict[str, Any], int]:
    token = mc.authenticate()
    search_params = {"vesselName": vessel_name}
    da_ids, search_data = mc.da_search(token, search_params)

    if not da_ids:
        common_ports = [
            "Singapore",
            "Rotterdam",
            "Hamburg",
            "Antwerp",
            "Shanghai",
            "Hong Kong",
            "Los Angeles",
            "New York",
        ]
        for port in common_ports:
            try:
                port_search_params = {"portName": port, "vesselName": vessel_name}
                port_da_ids, port_search_data = mc.da_search(token, port_search_params)
                if port_da_ids:
                    da_ids = port_da_ids
                    search_data = port_search_data
                    break
            except Exception as e:
                logger.warning("[API] Port search error %s: %s", port, e)

    if not da_ids:
        return (
            {
                "vessel_name": vessel_name,
                "found": False,
                "message": f'No vessels found with name "{vessel_name}"',
                "suggestions": [
                    "Check vessel name spelling",
                    "Try searching by port: GET /api/port-vessels/{port_name}",
                    "Use exact vessel name",
                ],
            },
            404,
        )

    exact_match_da_id = None
    vessel_info: dict[str, Any] = {}
    port_info: dict[str, Any] = {}

    for result in search_data.get("results", []):
        result_vessel_name = result.get("vessel", {}).get("name", "").lower()
        if result_vessel_name == vessel_name.lower():
            exact_match_da_id = result.get("id")
            vessel_info = result.get("vessel", {})
            port_info = result.get("port", {})
            break

    if not exact_match_da_id:
        return (
            {
                "vessel_name": vessel_name,
                "found": False,
                "message": f'No exact match found for vessel "{vessel_name}"',
                "suggestions": [
                    "Check vessel name spelling",
                    "Try searching by port: GET /api/port-vessels/{port_name}",
                    "Use exact vessel name",
                ],
            },
            404,
        )

    return (
        {
            "vessel_name": vessel_name,
            "found": True,
            "found_vessels": len(da_ids),
            "da_id": exact_match_da_id,
            "port": port_info.get("name", "Unknown"),
            "vessel_details": {
                "name": vessel_info.get("name", "Unknown"),
                "imo": vessel_info.get("imo", "Unknown"),
                "mmsi": vessel_info.get("mmsi", "Unknown"),
            },
            "port_details": {
                "name": port_info.get("name", "Unknown"),
                "country": port_info.get("country", {}).get("name", "Unknown"),
            },
            "all_da_ids": da_ids,
            "message": (
                f"Found {vessel_name} in {port_info.get('name', 'Unknown')} "
                f"with DA ID {exact_match_da_id}"
            ),
        },
        200,
    )


def api_vessel_search(vessel_name: str) -> tuple[dict[str, Any], int]:
    token = mc.authenticate()
    search_params = {"vesselName": vessel_name}
    da_ids, search_data = mc.da_search(token, search_params)

    if not da_ids:
        common_ports = ["Singapore", "Rotterdam", "Hamburg", "Antwerp", "Shanghai"]
        for port in common_ports:
            try:
                port_search_params = {"portName": port, "vesselName": vessel_name}
                port_da_ids, port_search_data = mc.da_search(token, port_search_params)
                if port_da_ids:
                    da_ids = port_da_ids
                    search_data = port_search_data
                    break
            except Exception as e:
                logger.warning("[API] Port search error %s: %s", port, e)

    if not da_ids:
        return (
            {
                "error": f'No vessels found with name "{vessel_name}"',
                "suggestions": [
                    "Check vessel name spelling",
                    "Try searching by port first: GET /api/port-vessels/{port_name}",
                    "Use exact vessel name",
                ],
            },
            404,
        )

    first_da_id = da_ids[0]
    vessel_details = mc.da_details(token, int(first_da_id))
    vessel_info = vessel_details.get("vessel", {})
    port_info = vessel_details.get("port", {})

    return (
        {
            "vessel_name": vessel_name,
            "found_vessels": len(da_ids),
            "da_id": first_da_id,
            "vessel_details": {
                "name": vessel_info.get("name", "Unknown"),
                "imo": vessel_info.get("imo", "Unknown"),
                "mmsi": vessel_info.get("mmsi", "Unknown"),
            },
            "port_details": {
                "name": port_info.get("name", "Unknown"),
                "country": port_info.get("country", "Unknown"),
            },
            "all_da_ids": da_ids,
            "search_data": search_data,
        },
        200,
    )
