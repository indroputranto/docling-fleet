"""
DA Desk CMS assistant — Anthropic tool loop over Marcura-backed handlers.

System prompt: da/prompts/port_bill_assistant.md
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from anthropic import Anthropic

logger = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = 14
_DEFAULT_MODEL = "claude-sonnet-4-6"


def load_system_prompt(api_base_url: str) -> str:
    path = Path(__file__).resolve().parent / "prompts" / "port_bill_assistant.md"
    text = path.read_text(encoding="utf-8")
    return text.replace("__API_BASE__", api_base_url.rstrip("/"))


def _filter_owner_compact(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows or []:
        c = (r.get("category") or "").lower()
        i = (r.get("item") or r.get("item_name") or "").lower()
        if "owner" in c or "owner" in i:
            continue
        out.append(r)
    return out


def _filter_owner_detail(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in items or []:
        c = (r.get("category") or "").lower()
        i = (r.get("item_name") or "").lower()
        if "owner" in c or "owner" in i:
            continue
        out.append(r)
    return out


DA_TOOLS: list[dict[str, Any]] = [
    {
        "name": "port_vessels",
        "description": (
            "List vessels in a port with PDA/FDA state (Loading/Discharging). "
            "Optional from_year filters Marcura updated-from range."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "port_name": {
                    "type": "string",
                    "description": "Port name, e.g. Singapore or Houston TX",
                },
                "from_year": {
                    "type": "integer",
                    "description": "Calendar year (20xx). Omit to use API default window.",
                },
            },
            "required": ["port_name"],
        },
    },
    {
        "name": "vessel_cost",
        "description": (
            "Compact PDA-style cost table for a vessel name or reference number. "
            "Owner cost lines are stripped server-side."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "vessel_or_reference": {
                    "type": "string",
                    "description": "Exact vessel name or OCS reference XXX-XXXXXX-X",
                },
            },
            "required": ["vessel_or_reference"],
        },
    },
    {
        "name": "vessel_by_reference",
        "description": "Resolve vessel name, da_id, port from a DA reference number.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reference_number": {"type": "string"},
            },
            "required": ["reference_number"],
        },
    },
    {
        "name": "vessel_lookup",
        "description": "Find DA id and port for an exact vessel name match.",
        "input_schema": {
            "type": "object",
            "properties": {"vessel_name": {"type": "string"}},
            "required": ["vessel_name"],
        },
    },
    {
        "name": "da_cost_details",
        "description": (
            "Detailed cost breakdown with comments (cost-details endpoint). "
            "Use after you know da_id. Owner lines removed server-side."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "da_id": {"type": "integer"},
                "stage": {
                    "type": "string",
                    "description": "Usually PDA",
                    "default": "PDA",
                },
                "persona": {
                    "type": "string",
                    "description": "Usually OPERATOR",
                    "default": "OPERATOR",
                },
            },
            "required": ["da_id"],
        },
    },
    {
        "name": "da_search",
        "description": "Natural-language DA search (ported parser); returns a few detail snapshots.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
]


def execute_tool(name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    from da import handlers as da_handlers

    try:
        if name == "port_vessels":
            port = (tool_input.get("port_name") or "").strip()
            if len(port) < 2:
                return {"ok": False, "error": "port_name too short"}
            fy = tool_input.get("from_year")
            fy_s = str(int(fy)) if fy is not None else None
            data, _code = da_handlers.api_port_vessels(port, fy_s, None, True)
            return {"ok": True, "data": data}

        if name == "vessel_cost":
            key = (tool_input.get("vessel_or_reference") or "").strip()
            if not key:
                return {"ok": False, "error": "vessel_or_reference required"}
            payload, code = da_handlers.api_vessel_cost(key)
            if isinstance(payload, dict) and payload.get("cost_breakdown"):
                payload = {
                    **payload,
                    "cost_breakdown": _filter_owner_compact(payload["cost_breakdown"]),
                }
            return {"ok": code == 200, "http_status": code, "data": payload}

        if name == "vessel_by_reference":
            ref = (tool_input.get("reference_number") or "").strip()
            if not ref:
                return {"ok": False, "error": "reference_number required"}
            payload, code = da_handlers.api_vessel_by_reference(ref)
            return {"ok": code == 200, "http_status": code, "data": payload}

        if name == "vessel_lookup":
            vn = (tool_input.get("vessel_name") or "").strip()
            if not vn:
                return {"ok": False, "error": "vessel_name required"}
            payload, code = da_handlers.api_vessel_lookup(vn)
            return {"ok": code == 200, "http_status": code, "data": payload}

        if name == "da_cost_details":
            da_id = tool_input.get("da_id")
            if da_id is None:
                return {"ok": False, "error": "da_id required"}
            stage = (tool_input.get("stage") or "PDA").strip() or "PDA"
            persona = (tool_input.get("persona") or "OPERATOR").strip() or "OPERATOR"
            payload, code = da_handlers.api_cost_details(int(da_id), stage, persona)
            if code == 200 and isinstance(payload, dict):
                payload = {
                    **payload,
                    "detailed_breakdown": _filter_owner_detail(
                        payload.get("detailed_breakdown")
                    ),
                }
            return {"ok": code == 200, "http_status": code, "data": payload}

        if name == "da_search":
            q = (tool_input.get("query") or "").strip()
            if not q:
                return {"ok": False, "error": "query required"}
            payload, code = da_handlers.api_da_search(q)
            return {"ok": code == 200, "http_status": code, "data": payload}

        return {"ok": False, "error": f"unknown tool {name}"}
    except Exception as e:
        logger.exception("[DA assistant] tool %s failed", name)
        return {"ok": False, "error": str(e)}


def run_chat_turn(
    *,
    message: str,
    history: list[dict[str, Any]],
    api_base_url: str,
) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    model = os.getenv("DA_DESK_ASSISTANT_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    system = load_system_prompt(api_base_url)
    client = Anthropic(api_key=api_key)

    messages: list[dict[str, Any]] = []
    for turn in history[-24:]:
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content.strip()})
    messages.append({"role": "user", "content": message.strip()})

    for round_i in range(_MAX_TOOL_ROUNDS):
        resp = client.messages.create(
            model=model,
            max_tokens=8192,
            system=system,
            tools=DA_TOOLS,
            messages=messages,
        )

        if resp.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": resp.content})
            tool_results: list[dict[str, Any]] = []
            for block in resp.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                inp = getattr(block, "input", None) or {}
                if not isinstance(inp, dict):
                    inp = {}
                result = execute_tool(block.name, inp)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                    }
                )
            if not tool_results:
                logger.warning("[DA assistant] tool_use stop but no tool blocks")
                break
            messages.append({"role": "user", "content": tool_results})
            logger.info(
                "[DA assistant] tool round %s executed %s tools",
                round_i + 1,
                len(tool_results),
            )
            continue

        texts: list[str] = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                texts.append(block.text)
        reply = "".join(texts).strip()
        return reply if reply else "NO_HTTP_CALL_MADE"

    return "NO_HTTP_CALL_MADE"


_KEY_NOTES_SYSTEM = """You are a maritime disbursement account (DA) analyst. The user sends JSON with \
vessel/port context, PDA/FDA stage, verified numeric category subtotals, grand total, and line items \
(category, item, amount, optional comment snippets).

Write concise Key Notes as Markdown body text only (no document title or "Key Notes" heading — the UI already shows that label):
- Start directly with a bullet list (use - bullets).
- Explain what drives the larger amounts using ONLY the supplied figures and comments.
- Explicitly reference category subtotals and the grand total where helpful (numbers must match the JSON).
- If stage is PDA, note these are provisional/agreed estimates unless comments say otherwise.
- Do not invent tariffs, GT, regulations, or rates not implied by the data or comments.
- Stay under 400 words. No salutation."""


def _fallback_key_notes_markdown(context: dict[str, Any]) -> str:
    """Non-AI bullets when the model returns no text (timeouts, safety filters, etc.)."""
    cur = (context.get("currency") or "").strip()
    stage = context.get("stage") or "PDA"
    persona = context.get("persona") or "OPERATOR"
    lines = [
        f"- Figures use **{stage}** / **{persona}** staging.",
        "- Category subtotals:",
    ]
    subs = context.get("category_subtotals") or {}
    for cat in sorted(subs.keys(), key=lambda x: str(x).lower()):
        raw_amt = subs.get(cat)
        try:
            amt_s = f"{float(raw_amt):,.2f}"
        except (TypeError, ValueError):
            amt_s = str(raw_amt)
        lines.append(f"- **{cat}**: {amt_s} {cur}".rstrip())
    gt = context.get("grand_total")
    if gt is not None:
        try:
            gt_s = f"{float(gt):,.2f}"
        except (TypeError, ValueError):
            gt_s = str(gt)
        lines.append(f"- **Grand total**: {gt_s} {cur}".rstrip())
    lines.append(
        "- _(No narrative from the model — showing totals only; retry or check model/logs.)_"
    )
    return "\n".join(lines)


def generate_da_key_notes(context: dict[str, Any]) -> str:
    """Single-turn Claude call: narrative key notes from a DA cost breakdown snapshot."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    notes_model = (
        os.getenv("DA_DESK_KEY_NOTES_MODEL", "").strip()
        or os.getenv("DA_DESK_ASSISTANT_MODEL", "").strip()
        or _DEFAULT_MODEL
    )
    client = Anthropic(api_key=api_key)
    user_blob = json.dumps(context, ensure_ascii=False, default=str)
    resp = client.messages.create(
        model=notes_model,
        max_tokens=2048,
        temperature=0.25,
        system=_KEY_NOTES_SYSTEM,
        messages=[{"role": "user", "content": user_blob}],
    )
    texts: list[str] = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            texts.append(block.text)
    out = "".join(texts).strip()
    if out:
        return out
    return _fallback_key_notes_markdown(context)
