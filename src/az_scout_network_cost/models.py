"""Domain models for VNet peering cost estimation.

Azure uses *billing zones* (not availability zones) to determine
inter-region data-transfer pricing.  Global VNet peering is more
expensive than same-region peering, but the cost is predictable
and typically low — rarely a blocker for multi-region architectures.

This module defines request/response models for three analysis modes:
1. Estimate-only — simulate peering cost without Azure data
2. Billing import — analyse actual billed network cost from CSV exports
3. Traffic import — estimate peering cost from observed traffic flows
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

BillingZone = Literal["zone1", "zone2", "zone3", "usgov", "unknown"]
PricingModelName = Literal["same-region-vnet-peering", "global-vnet-peering"]
AnalysisMode = Literal["estimate", "billing", "traffic"]

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
# Mode 1 — Estimate-only models (existing)
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


# ---------------------------------------------------------------------------
# Mode 2 — Billing import models
# ---------------------------------------------------------------------------


class BillingMeterSummary(BaseModel):
    """Aggregated view of a single billing meter / sub-category."""

    meter_category: str = Field(..., description="e.g. 'Virtual Network'")
    meter_sub_category: str = Field("", description="e.g. 'Peering'")
    meter_name: str = Field("", description="e.g. 'Inter-Region Egress'")
    region: str = Field("", description="Azure region if identifiable")
    total_cost: float = Field(0.0, description="Total billed cost in USD")
    total_usage: float = Field(0.0, description="Total usage quantity")
    unit: str = Field("", description="Unit of measure (e.g. GB)")
    row_count: int = Field(0, description="Number of billing rows aggregated")


class BillingRegionSummary(BaseModel):
    """Network cost summary for a single region."""

    region: str
    total_cost: float
    peering_cost: float
    meter_count: int


class BillingAnalysisResponse(BaseModel):
    """Output of the billing CSV analysis endpoint."""

    total_network_cost: float = Field(
        ..., description="Total network-related billed cost"
    )
    peering_related_cost: float = Field(
        ..., description="Estimated peering-related subset of network cost"
    )
    total_rows_parsed: int
    network_rows_found: int
    peering_rows_found: int
    meter_breakdown: list[BillingMeterSummary]
    region_breakdown: list[BillingRegionSummary]
    dominant_region: str = Field("", description="Region with highest network cost")
    notes: list[str]
    caveats: list[str] = Field(
        default_factory=lambda: [
            "Billing exports may not allow perfect peering-pair attribution.",
            "This is actual billed data analysis, not topology-aware mapping.",
            "Some meter categories may include non-peering network charges.",
        ]
    )


# ---------------------------------------------------------------------------
# Mode 3 — Traffic import models
# ---------------------------------------------------------------------------


class TrafficPairSummary(BaseModel):
    """Aggregated traffic and estimated cost for a region pair."""

    source_region: str
    target_region: str
    source_zone: str = ""
    target_zone: str = ""
    traffic_gb: float
    is_same_region: bool = False
    estimated_monthly_cost: float = 0.0
    rate_per_gb: float = 0.0


class TrafficAnalysisResponse(BaseModel):
    """Output of the traffic CSV analysis endpoint."""

    total_traffic_gb: float
    total_estimated_cost: float
    pair_count: int
    top_pairs: list[TrafficPairSummary]
    all_pairs: list[TrafficPairSummary]
    dominant_pair: str = Field(
        "", description="Region pair with highest traffic volume"
    )
    dominant_direction: str = Field(
        "", description="Highest traffic direction label"
    )
    notes: list[str]
    pricing_source: str = "hardcoded-fallback"
    caveats: list[str] = Field(
        default_factory=lambda: [
            "Cost is estimated from observed traffic mapped to public pricing.",
            "Actual billed cost may differ based on negotiated rates.",
        ]
    )


# ---------------------------------------------------------------------------
# Step 3 — Insights (shared across modes)
# ---------------------------------------------------------------------------


class InsightItem(BaseModel):
    """A single decision-support insight."""

    icon: str = Field("info", description="Icon hint: info, check, warning, dollar")
    title: str
    value: str = Field("", description="Key numeric or textual value")
    description: str


class InsightsResponse(BaseModel):
    """Step 3 decision-support output."""

    mode: AnalysisMode
    headline: str
    insights: list[InsightItem]
    interpretation: str = Field(
        ...,
        description="Business-oriented interpretation text",
    )
    recommendations: list[str] = Field(default_factory=list)
