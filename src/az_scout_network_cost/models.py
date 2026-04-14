"""Domain models for VNet peering cost estimation.

Azure uses *billing zones* (not availability zones) to determine
inter-region data-transfer pricing.  Global VNet peering is more
expensive than same-region peering, but the cost is predictable
and typically low — rarely a blocker for multi-region architectures.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

BillingZone = Literal["zone1", "zone2", "zone3", "usgov", "unknown"]
PricingModelName = Literal["same-region-vnet-peering", "global-vnet-peering"]

# ---------------------------------------------------------------------------
# Price-to-zone mapping
# ---------------------------------------------------------------------------

# Derive billing zone from the inter-region rate (all regions in a zone
# share the same rate).  This avoids maintaining a manual region→zone map.
# Rates from the Azure Retail Prices API (2026-04):
#   Zone 1 = $0.035/GB (NA, Europe), Zone 2 = $0.09/GB, Zone 3 = $0.16/GB
#   US Gov = $0.044/GB, Delos Cloud = $0.0385/GB (both mapped to zone1)
RATE_TO_ZONE: dict[float, BillingZone] = {
    0.035: "zone1",
    0.0385: "zone1",  # Delos Cloud Germany
    0.04: "zone1",  # fallback value
    0.044: "zone1",  # US Gov
    0.09: "zone2",
    0.16: "zone3",
}

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class EstimateRequest(BaseModel):
    """Input for the VNet peering cost estimation endpoint."""

    source_region: str = Field(..., description="Azure region name for source VNet")
    target_region: str = Field(..., description="Azure region name for target VNet")
    traffic_ab_gb: float = Field(
        ..., ge=0, description="Monthly traffic from source → target in GB"
    )
    traffic_ba_gb: float = Field(
        ..., ge=0, description="Monthly traffic from target → source in GB"
    )
    same_region: bool = Field(
        False,
        description="Force same-region pricing (both VNets in the same region)",
    )
    currency: str = Field("USD", description="Currency code (currently only USD supported)")


class DirectionBreakdown(BaseModel):
    """Cost breakdown for one traffic direction."""

    direction: str = Field(..., description="e.g. 'A → B (source → target)'")
    traffic_gb: float
    outbound_rate_per_gb: float
    inbound_rate_per_gb: float
    outbound_cost: float
    inbound_cost: float
    subtotal: float


class EstimateResponse(BaseModel):
    """Output of the VNet peering cost estimation."""

    source_region: str
    target_region: str
    source_zone: str
    target_zone: str
    pricing_model: PricingModelName
    monthly_total_usd: float
    annual_total_usd: float
    per_tb_usd: float
    breakdown: list[DirectionBreakdown]
    notes: list[str]
    pricing_source: str = Field(
        "hardcoded-fallback",
        description="Data source: 'azure-retail-prices-api' or 'hardcoded-fallback'",
    )
