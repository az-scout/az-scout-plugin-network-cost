"""FastAPI routes for the VNet peering cost estimation plugin.

Mounted at ``/plugins/network-cost/`` by the plugin host.

Endpoints
---------
POST /v1/estimate           — Mode 1: estimate-only peering cost
POST /v1/analyze-billing    — Mode 2: analyse billing CSV export
POST /v1/analyze-traffic    — Mode 3: analyse traffic CSV export
GET  /v1/regions            — list known regions by billing zone
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, UploadFile

from az_scout_network_cost.insights import (
    generate_billing_insights,
    generate_estimate_insights,
    generate_traffic_insights,
)
from az_scout_network_cost.models import (
    RATE_TO_ZONE,
    EstimateRequest,
    EstimateResponse,
)
from az_scout_network_cost.parsers import parse_billing_csv, parse_traffic_csv
from az_scout_network_cost.price_fetcher import get_known_regions, get_pricing_source
from az_scout_network_cost.pricing import estimate

router = APIRouter()


# ---------------------------------------------------------------------------
# Mode 1 — Estimate only
# ---------------------------------------------------------------------------


@router.post("/v1/estimate", response_model=EstimateResponse)
async def estimate_peering(req: EstimateRequest) -> EstimateResponse:
    """Estimate monthly VNet peering cost between two Azure regions.

    Supports same-region peering and global VNet peering with
    per-direction breakdown and explanatory notes.

    Prices are fetched from the Azure Retail Prices API and cached
    (1 hour TTL).  Falls back to hardcoded values if the API is down.
    """
    return estimate(req)


@router.post("/v1/estimate-with-insights")
async def estimate_with_insights(req: EstimateRequest) -> dict[str, Any]:
    """Estimate peering cost and generate Step 3 insights.

    Automatically computes the same-region baseline for delta comparison.
    """
    result = estimate(req)

    # Compute same-region baseline for comparison
    same_region_result: EstimateResponse | None = None
    if not req.same_region:
        same_req = req.model_copy(update={"same_region": True})
        same_region_result = estimate(same_req)

    insights = generate_estimate_insights(result, same_region_result)

    return {
        "estimate": result.model_dump(),
        "same_region_estimate": same_region_result.model_dump() if same_region_result else None,
        "insights": insights.model_dump(),
    }


# ---------------------------------------------------------------------------
# Mode 2 — Billing CSV analysis
# ---------------------------------------------------------------------------


@router.post("/v1/analyze-billing")
async def analyze_billing(
    usage_file: UploadFile = File(..., description="Azure billing usage CSV file"),  # noqa: B008
) -> dict[str, Any]:
    """Analyse an Azure billing CSV export for network-related costs.

    Parses the uploaded CSV, extracts network-related rows, identifies
    likely VNet peering entries, and returns a structured summary with
    meter and region breakdowns.
    """
    content = (await usage_file.read()).decode("utf-8-sig")
    billing_result = parse_billing_csv(content)
    insights = generate_billing_insights(billing_result)

    return {
        "billing": billing_result.model_dump(),
        "insights": insights.model_dump(),
    }


# ---------------------------------------------------------------------------
# Mode 3 — Traffic CSV analysis
# ---------------------------------------------------------------------------


@router.post("/v1/analyze-traffic")
async def analyze_traffic(
    traffic_file: UploadFile = File(..., description="Traffic summary CSV file"),  # noqa: B008
) -> dict[str, Any]:
    """Analyse a traffic CSV export to estimate VNet peering costs.

    Parses the uploaded CSV, aggregates traffic by region pair,
    estimates peering cost using the pricing engine, and returns
    a structured summary with insights.
    """
    content = (await traffic_file.read()).decode("utf-8-sig")
    traffic_result = parse_traffic_csv(content)
    insights = generate_traffic_insights(traffic_result)

    return {
        "traffic": traffic_result.model_dump(),
        "insights": insights.model_dump(),
    }


# ---------------------------------------------------------------------------
# Regions reference
# ---------------------------------------------------------------------------


@router.get("/v1/regions")
async def list_regions() -> dict[str, Any]:
    """Return all known regions grouped by billing zone.

    Regions and zones are derived from live pricing data.
    """
    regions = get_known_regions()
    zones: dict[str, list[str]] = {}
    for region, rp in sorted(regions.items()):
        zone = RATE_TO_ZONE.get(rp.inter_egress, "unknown")
        zones.setdefault(zone, []).append(region)
    return {
        "zones": zones,
        "pricing_source": get_pricing_source(),
    }
