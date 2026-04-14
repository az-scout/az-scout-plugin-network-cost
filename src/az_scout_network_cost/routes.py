"""FastAPI routes for the VNet peering cost estimation plugin.

Mounted at ``/plugins/network-cost/`` by the plugin host.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from az_scout_network_cost.models import RATE_TO_ZONE, EstimateRequest, EstimateResponse
from az_scout_network_cost.price_fetcher import get_known_regions, get_pricing_source
from az_scout_network_cost.pricing import estimate

router = APIRouter()


@router.post("/v1/estimate", response_model=EstimateResponse)
async def estimate_peering(req: EstimateRequest) -> EstimateResponse:
    """Estimate monthly VNet peering cost between two Azure regions.

    Supports same-region peering and global VNet peering with
    per-direction breakdown and explanatory notes.

    Prices are fetched from the Azure Retail Prices API and cached
    (1 hour TTL).  Falls back to hardcoded values if the API is down.
    """
    return estimate(req)


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
