"""VNet peering pricing engine.

Computes peering costs using live prices from the Azure Retail Prices
API (with in-memory TTL cache and hardcoded fallback).

Key concept: for each traffic direction, cost = outbound (source side)
+ inbound (destination side).  Total = A→B cost + B→A cost.

Azure billing zones are *geographic* groupings used for data-transfer
pricing — they are NOT availability zones.
"""

from __future__ import annotations

from az_scout.plugin_api import PluginValidationError

from az_scout_network_cost.models import (
    RATE_TO_ZONE,
    BillingZone,
    DirectionBreakdown,
    EstimateRequest,
    EstimateResponse,
    PricingModelName,
)
from az_scout_network_cost.price_fetcher import (
    RegionPricing,
    get_intra_region_rates,
    get_known_regions,
    get_pricing_source,
    get_region_rates,
)


def _zone_from_rate(rate: float) -> BillingZone:
    """Derive billing zone from an inter-region rate."""
    return RATE_TO_ZONE.get(rate, "unknown")


def get_billing_zone(region: str) -> BillingZone:
    """Resolve an Azure region name to its billing zone.

    Uses the per-region rates fetched from the Azure Retail Prices API
    to derive the zone.  Raises ``PluginValidationError`` if unknown.
    """
    rp = get_region_rates(region)
    if rp is None:
        known = ", ".join(sorted(get_known_regions().keys()))
        raise PluginValidationError(f"Unknown Azure region '{region}'. Known regions: {known}")
    return _zone_from_rate(rp.inter_egress)


def _get_rates(region: str) -> RegionPricing:
    """Return peering rates for *region*, raising on unknown."""
    rp = get_region_rates(region)
    if rp is None:
        known = ", ".join(sorted(get_known_regions().keys()))
        raise PluginValidationError(f"Unknown Azure region '{region}'. Known regions: {known}")
    return rp


def _compute_direction(
    *,
    label: str,
    traffic_gb: float,
    outbound_rate: float,
    inbound_rate: float,
) -> DirectionBreakdown:
    """Compute cost for a single traffic direction."""
    outbound_cost = round(traffic_gb * outbound_rate, 4)
    inbound_cost = round(traffic_gb * inbound_rate, 4)
    return DirectionBreakdown(
        direction=label,
        traffic_gb=traffic_gb,
        outbound_rate_per_gb=outbound_rate,
        inbound_rate_per_gb=inbound_rate,
        outbound_cost=outbound_cost,
        inbound_cost=inbound_cost,
        subtotal=round(outbound_cost + inbound_cost, 4),
    )


def estimate(req: EstimateRequest) -> EstimateResponse:
    """Run the full peering cost estimation.

    Returns a response with monthly/annual totals, per-TB cost,
    per-direction breakdown, and explanatory notes.
    """
    source_rates = _get_rates(req.source_region)
    target_rates = _get_rates(req.target_region)

    source_zone = _zone_from_rate(source_rates.inter_egress)
    target_zone = _zone_from_rate(target_rates.inter_egress)

    notes: list[str] = []

    pricing_model: PricingModelName
    if req.same_region:
        pricing_model = "same-region-vnet-peering"
        intra_in, intra_out = get_intra_region_rates()
        outbound_rate_ab = intra_out
        inbound_rate_ab = intra_in
        outbound_rate_ba = intra_out
        inbound_rate_ba = intra_in
        notes.append("Same-region VNet peering: both VNets are in the same Azure region.")
    else:
        pricing_model = "global-vnet-peering"
        # A→B: outbound from source region, inbound to target region
        outbound_rate_ab = source_rates.inter_egress
        inbound_rate_ab = target_rates.inter_ingress
        # B→A: outbound from target region, inbound to source region
        outbound_rate_ba = target_rates.inter_egress
        inbound_rate_ba = source_rates.inter_ingress

        if source_zone == target_zone:
            notes.append(
                f"Both regions are in billing {source_zone.upper()} — "
                f"global peering rates within the same zone apply."
            )
        else:
            notes.append(
                f"Cross-zone peering: {source_zone.upper()} ↔ {target_zone.upper()}. "
                f"Rates differ by zone."
            )

    # A→B direction
    ab = _compute_direction(
        label=f"A → B ({req.source_region} → {req.target_region})",
        traffic_gb=req.traffic_ab_gb,
        outbound_rate=outbound_rate_ab,
        inbound_rate=inbound_rate_ab,
    )

    # B→A direction
    ba = _compute_direction(
        label=f"B → A ({req.target_region} → {req.source_region})",
        traffic_gb=req.traffic_ba_gb,
        outbound_rate=outbound_rate_ba,
        inbound_rate=inbound_rate_ba,
    )

    monthly_total = round(ab.subtotal + ba.subtotal, 2)
    annual_total = round(monthly_total * 12, 2)

    total_traffic_gb = req.traffic_ab_gb + req.traffic_ba_gb
    per_tb = round((monthly_total / total_traffic_gb * 1000), 2) if total_traffic_gb > 0 else 0.0

    # Key messaging: peering cost is rarely a blocker
    notes.append(
        "Azure billing zones determine data-transfer pricing — "
        "these are NOT the same as availability zones."
    )
    if pricing_model == "global-vnet-peering" and monthly_total < 1000:
        notes.append(
            f"At {total_traffic_gb:.0f} GB/month, global peering costs "
            f"${monthly_total:.2f}/month — typically low compared to "
            f"the value of multi-region deployment."
        )
    notes.append(
        "The real challenge in multi-region architectures is design and "
        "data consistency, not network cost."
    )

    pricing_source = get_pricing_source()
    if pricing_source == "hardcoded-fallback":
        notes.append("⚠ Prices from hardcoded fallback — API was unreachable.")

    return EstimateResponse(
        source_region=req.source_region.lower(),
        target_region=req.target_region.lower(),
        source_zone=source_zone,
        target_zone=target_zone,
        pricing_model=pricing_model,
        monthly_total_usd=monthly_total,
        annual_total_usd=annual_total,
        per_tb_usd=per_tb,
        breakdown=[ab, ba],
        notes=notes,
        pricing_source=pricing_source,
    )
