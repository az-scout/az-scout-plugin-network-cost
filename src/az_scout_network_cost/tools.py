"""MCP tools for the VNet peering cost estimation plugin.

Tools are plain functions with type annotations and docstrings.
They are automatically registered on the az-scout MCP server
and available in AI chat.  Keep them stateless.
"""

from __future__ import annotations

from typing import Any

from az_scout_network_cost.models import EstimateRequest
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
        pricing_model, source_zone, target_zone, breakdown, and notes.
    """
    try:
        req = EstimateRequest(
            source_region=source_region,
            target_region=target_region,
            traffic_ab_gb=traffic_ab_gb,
            traffic_ba_gb=traffic_ba_gb,
            same_region=same_region,
        )
        result = estimate(req)
        return result.model_dump()
    except Exception as exc:
        return {"error": str(exc)}
