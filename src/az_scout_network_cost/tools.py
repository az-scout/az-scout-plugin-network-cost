"""MCP tools for the VNet peering cost estimation plugin.

Tools are plain functions with type annotations and docstrings.
They are automatically registered on the az-scout MCP server
and available in AI chat.  Keep them stateless.
"""

from __future__ import annotations

from typing import Any

from az_scout_network_cost.insights import (
    generate_billing_insights,
    generate_estimate_insights,
    generate_traffic_insights,
)
from az_scout_network_cost.models import EstimateRequest
from az_scout_network_cost.parsers import parse_billing_csv, parse_traffic_csv
from az_scout_network_cost.pricing import estimate


def estimate_peering_cost(
    source_region: str,
    target_region: str,
    traffic_ab_gb: float = 100.0,
    traffic_ba_gb: float = 100.0,
    same_region: bool = False,
) -> dict[str, Any]:
    """Estimate Azure VNet peering cost between two regions.

    Calculates monthly and annual costs for VNet peering traffic
    with a per-direction breakdown.  Supports same-region peering
    and global VNet peering across billing zones.

    Args:
        source_region: Azure region name for the source VNet (e.g. "westeurope").
        target_region: Azure region name for the target VNet (e.g. "francecentral").
        traffic_ab_gb: Monthly traffic from source → target in GB (default 100).
        traffic_ba_gb: Monthly traffic from target → source in GB (default 100).
        same_region: Set True to use same-region peering pricing.

    Returns:
        Dict with monthly_total_usd, annual_total_usd, per_tb_usd,
        pricing_model, source_zone, target_zone, breakdown, notes,
        and insights for decision support.
    """
    try:
        req = EstimateRequest(
            source_region=source_region,
            target_region=target_region,
            traffic_ab_gb=traffic_ab_gb,
            traffic_ba_gb=traffic_ba_gb,
            same_region=same_region,
            currency="USD",
        )
        result = estimate(req)

        # Generate same-region baseline for comparison
        same_region_result = None
        if not same_region:
            same_req = req.model_copy(update={"same_region": True})
            same_region_result = estimate(same_req)

        insights = generate_estimate_insights(result, same_region_result)

        return {
            **result.model_dump(),
            "insights": insights.model_dump(),
        }
    except Exception as exc:
        return {"error": str(exc)}


def analyze_billing_network_cost(
    csv_content: str,
) -> dict[str, Any]:
    """Analyse Azure billing CSV export for network-related costs.

    Parses a billing Usage Details CSV export, extracts network-related
    rows, identifies likely VNet peering entries using meter / category
    naming heuristics, and returns a structured summary.

    Args:
        csv_content: Raw CSV text content from an Azure billing export.

    Returns:
        Dict with total_network_cost, peering_related_cost,
        meter_breakdown, region_breakdown, notes, caveats,
        and decision-support insights.
    """
    try:
        billing = parse_billing_csv(csv_content)
        insights = generate_billing_insights(billing)
        return {
            "billing": billing.model_dump(),
            "insights": insights.model_dump(),
        }
    except Exception as exc:
        return {"error": str(exc)}


def analyze_traffic_peering_cost(
    csv_content: str,
) -> dict[str, Any]:
    """Analyse traffic CSV to estimate VNet peering cost from observed flows.

    Parses a simplified traffic CSV (columns: source_region, target_region,
    traffic_gb), aggregates by region pair, and estimates peering cost
    using the Azure pricing engine.

    Args:
        csv_content: Raw CSV text content with traffic data.

    Returns:
        Dict with total_traffic_gb, total_estimated_cost,
        top_pairs, notes, and decision-support insights.
    """
    try:
        traffic = parse_traffic_csv(csv_content)
        insights = generate_traffic_insights(traffic)
        return {
            "traffic": traffic.model_dump(),
            "insights": insights.model_dump(),
        }
    except Exception as exc:
        return {"error": str(exc)}
