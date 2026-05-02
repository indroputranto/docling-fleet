"""Extract cost breakdown structures from raw Marcura DA JSON."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_cost_breakdown_for_stage(
    da_details_data: dict[str, Any],
    stage: str = "PDA",
    persona: str = "OPERATOR",
) -> list[dict[str, Any]]:
    """Same shape as DA-Desk `/api/da-details/{id}` cost_breakdown."""
    expense_categories = da_details_data.get("expenseCategories", [])
    cost_breakdown: list[dict[str, Any]] = []
    stage_u = stage.upper()
    persona_u = persona.upper()

    for category in expense_categories:
        category_name = category.get("name", "Unknown Category")
        cost_items = category.get("costItems", [])

        for item in cost_items:
            cost_item_name = item.get("costItemAlias", {}).get("name", "Unknown Item")
            costs = item.get("costs", [])
            selected_amount = None
            all_costs: dict[str, Any] = {}

            for cost in costs:
                da_stage = cost.get("daStage", "")
                cost_persona = cost.get("persona", "")
                amount = cost.get("amount", 0)
                key = f"{da_stage}_{cost_persona}"
                all_costs[key] = amount
                if da_stage == stage_u and cost_persona == persona_u:
                    selected_amount = amount

            if selected_amount is None:
                if stage_u == "FDA" and "PDA_OPERATOR" in all_costs:
                    selected_amount = all_costs["PDA_OPERATOR"]
                    logger.info("[breakdown] Using PDA_OPERATOR fallback for %s", cost_item_name)
                elif stage_u == "PDA" and "FDA_OPERATOR" in all_costs:
                    selected_amount = all_costs["FDA_OPERATOR"]
                    logger.info("[breakdown] Using FDA_OPERATOR fallback for %s", cost_item_name)
                else:
                    selected_amount = sum(c.get("amount", 0) for c in costs)
                    logger.info("[breakdown] Using sum of costs for %s", cost_item_name)

            cost_breakdown.append(
                {
                    "category": category_name,
                    "item_name": cost_item_name,
                    "amount": selected_amount,
                    "currency": da_details_data.get("currencies", {})
                    .get("da", {})
                    .get("currency", "Unknown"),
                    "comments": item.get("comments", []),
                    "cost_details": {
                        "selected_stage": stage_u,
                        "selected_persona": persona_u,
                        "all_costs": all_costs,
                        "raw_costs": costs,
                    },
                }
            )

    return cost_breakdown


def build_detailed_breakdown(
    da_details_data: dict[str, Any],
    stage: str = "PDA",
    persona: str = "OPERATOR",
) -> list[dict[str, Any]]:
    """Same shape as `/api/da-details/{id}/cost-details` detailed_breakdown."""
    expense_categories = da_details_data.get("expenseCategories", [])
    detailed_breakdown: list[dict[str, Any]] = []

    for category in expense_categories:
        category_name = category.get("name", "Unknown Category")
        cost_items = category.get("costItems", [])

        for item in cost_items:
            cost_item_name = item.get("costItemAlias", {}).get("name", "Unknown Item")
            costs = item.get("costs", [])
            comments = item.get("comments", [])
            selected_amount = None
            all_costs: dict[str, Any] = {}

            for cost in costs:
                da_stage = cost.get("daStage", "")
                cost_persona = cost.get("persona", "")
                amount = cost.get("amount", 0)
                key = f"{da_stage}_{cost_persona}"
                all_costs[key] = amount
                if da_stage == "PDA" and cost_persona == "OPERATOR":
                    selected_amount = amount

            if selected_amount is None:
                if "FDA_OPERATOR" in all_costs:
                    selected_amount = all_costs["FDA_OPERATOR"]
                else:
                    selected_amount = sum(c.get("amount", 0) for c in costs)

            comment_texts = []
            for comment in comments:
                comment_texts.append(
                    {
                        "text": comment.get("comment", ""),
                        "author": comment.get("author", "Unknown"),
                        "date": comment.get("date", ""),
                        "daStage": comment.get("daStage", ""),
                        "type": comment.get("type", ""),
                    }
                )

            has_meaningful = selected_amount > 0 or len(comment_texts) > 0
            if has_meaningful:
                detailed_breakdown.append(
                    {
                        "category": category_name,
                        "item_name": cost_item_name,
                        "amount": selected_amount,
                        "currency": da_details_data.get("currencies", {})
                        .get("da", {})
                        .get("currency", "Unknown"),
                        "comments": comment_texts,
                        "has_comments": len(comment_texts) > 0,
                        "comment_count": len(comment_texts),
                        "content_score": len(comment_texts) * 10 + (1 if selected_amount > 0 else 0),
                        "cost_details": {
                            "selected_stage": "PDA",
                            "selected_persona": "OPERATOR",
                            "all_costs": all_costs,
                            "raw_costs": costs,
                        },
                    }
                )

    detailed_breakdown.sort(key=lambda x: x.get("content_score", 0), reverse=True)
    return detailed_breakdown


def compact_positive_cost_rows(
    cost_breakdown: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], float, str]:
    """Rows for vessel-cost style responses (category, item, amount, currency)."""
    formatted: list[dict[str, Any]] = []
    total_amount = 0.0
    currency = "USD"

    for row in cost_breakdown:
        amount = row.get("amount") or 0
        try:
            amt_f = float(amount)
        except (TypeError, ValueError):
            amt_f = 0.0
        if amt_f <= 0:
            continue
        cur = row.get("currency") or "USD"
        currency = cur
        formatted.append(
            {
                "category": row.get("category", "Unknown"),
                "item": row.get("item_name") or row.get("item", "Unknown"),
                "amount": amt_f,
                "currency": cur,
            }
        )
        total_amount += amt_f

    return formatted, total_amount, currency
