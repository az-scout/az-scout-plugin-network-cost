"""Step 3 insight generation — decision-support output for all modes.

Generates business-oriented interpretations of peering cost analysis
results.  Each mode produces a set of insight cards and a summary
interpretation text designed for customer-facing conversations about
multi-region architecture cost trade-offs.
"""

from __future__ import annotations

from az_scout_network_cost.models import (
    BillingAnalysisResponse,
    EstimateResponse,
    InsightItem,
    InsightsResponse,
    TrafficAnalysisResponse,
)


def _fmt_usd(v: float) -> str:
    return f"${v:,.2f}"


def _fmt_pct(v: float) -> str:
    return f"{v:.1f}%"


# ---------------------------------------------------------------------------
# Mode 1 — Estimate-only insights
# ---------------------------------------------------------------------------


def generate_estimate_insights(
    estimate: EstimateResponse,
    same_region_estimate: EstimateResponse | None = None,
) -> InsightsResponse:
    """Generate Step 3 insights from an estimation result.

    If ``same_region_estimate`` is provided, compute the delta between
    global and same-region peering to contextualise the cost difference.
    """
    insights: list[InsightItem] = []

    # Regions & zones
    insights.append(
        InsightItem(
            icon="info",
            title="Regions",
            value=f"{estimate.source_region} ↔ {estimate.target_region}",
            description=(
                f"Source is in billing {estimate.source_zone.upper()}, "
                f"target is in billing {estimate.target_zone.upper()}."
            ),
        )
    )

    # Monthly cost
    insights.append(
        InsightItem(
            icon="dollar",
            title="Estimated monthly cost",
            value=_fmt_usd(estimate.monthly_total_usd),
            description=f"Annual: {_fmt_usd(estimate.annual_total_usd)}",
        )
    )

    # Cost per TB
    insights.append(
        InsightItem(
            icon="info",
            title="Cost per TB",
            value=_fmt_usd(estimate.per_tb_usd),
            description="Effective rate for the total traffic volume.",
        )
    )

    # Delta with same-region
    delta_value = ""
    delta_desc = ""
    if same_region_estimate and estimate.pricing_model == "global-vnet-peering":
        diff = estimate.monthly_total_usd - same_region_estimate.monthly_total_usd
        if same_region_estimate.monthly_total_usd > 0:
            pct = (diff / same_region_estimate.monthly_total_usd) * 100
            delta_value = f"+{_fmt_usd(diff)}/mo ({_fmt_pct(pct)} more)"
        else:
            delta_value = f"+{_fmt_usd(diff)}/mo"
        delta_desc = (
            f"Same-region peering: {_fmt_usd(same_region_estimate.monthly_total_usd)}/mo vs. "
            f"global peering: {_fmt_usd(estimate.monthly_total_usd)}/mo."
        )
        insights.append(
            InsightItem(
                icon="warning",
                title="Global vs. same-region delta",
                value=delta_value,
                description=delta_desc,
            )
        )

    # Interpretation
    total_gb = sum(b.traffic_gb for b in estimate.breakdown)
    total_tb = total_gb / 1024
    if estimate.monthly_total_usd < 500:
        interpretation = (
            f"At {total_tb:.1f} TB/month, global peering adds "
            f"{_fmt_usd(estimate.monthly_total_usd)}/month — a predictable cost "
            f"that is typically small compared to the value of multi-region "
            f"deployment flexibility."
        )
    elif estimate.monthly_total_usd < 5000:
        interpretation = (
            f"At {total_tb:.1f} TB/month, global peering costs "
            f"{_fmt_usd(estimate.monthly_total_usd)}/month. This is a meaningful "
            f"but manageable cost component. Optimising traffic patterns or "
            f"using regional caching can reduce it further."
        )
    else:
        interpretation = (
            f"At {total_tb:.1f} TB/month, global peering costs "
            f"{_fmt_usd(estimate.monthly_total_usd)}/month. At this volume, "
            f"traffic architecture and data locality optimisation should be "
            f"considered alongside the multi-region deployment decision."
        )

    recommendations = [
        "Global VNet peering cost is predictable — it should not block multi-region designs.",
        "Architecture and data consistency are the real challenges, not network cost.",
    ]
    if estimate.monthly_total_usd > 1000:
        recommendations.append(
            "Consider regional caching, CDN offload, or data-locality patterns "
            "to reduce inter-region traffic volume."
        )

    return InsightsResponse(
        mode="estimate",
        headline="Peering cost estimation summary",
        insights=insights,
        interpretation=interpretation,
        recommendations=recommendations,
    )


# ---------------------------------------------------------------------------
# Mode 2 — Billing import insights
# ---------------------------------------------------------------------------


def generate_billing_insights(billing: BillingAnalysisResponse) -> InsightsResponse:
    """Generate Step 3 insights from billing CSV analysis."""
    insights: list[InsightItem] = []

    # Total network cost
    insights.append(
        InsightItem(
            icon="dollar",
            title="Total network cost",
            value=_fmt_usd(billing.total_network_cost),
            description=f"From {billing.network_rows_found:,} network-related billing rows.",
        )
    )

    # Peering-related subset
    if billing.peering_related_cost > 0:
        pct = (
            (billing.peering_related_cost / billing.total_network_cost * 100)
            if billing.total_network_cost > 0
            else 0
        )
        insights.append(
            InsightItem(
                icon="info",
                title="Peering-related cost",
                value=_fmt_usd(billing.peering_related_cost),
                description=(
                    f"{_fmt_pct(pct)} of total network cost "
                    f"({billing.peering_rows_found:,} rows matched peering heuristics)."
                ),
            )
        )

    # Dominant region
    if billing.dominant_region:
        top = billing.region_breakdown[0] if billing.region_breakdown else None
        if top:
            insights.append(
                InsightItem(
                    icon="info",
                    title="Dominant region",
                    value=billing.dominant_region,
                    description=f"Highest network cost: {_fmt_usd(top.total_cost)}.",
                )
            )

    # Cost concentration
    if len(billing.region_breakdown) >= 2:
        top_2_cost = sum(r.total_cost for r in billing.region_breakdown[:2])
        if billing.total_network_cost > 0:
            conc = top_2_cost / billing.total_network_cost * 100
            insights.append(
                InsightItem(
                    icon="warning" if conc > 80 else "info",
                    title="Cost concentration",
                    value=_fmt_pct(conc),
                    description=(f"Top 2 regions account for {_fmt_pct(conc)} of network cost."),
                )
            )

    # Interpretation
    if billing.peering_related_cost > 0 and billing.total_network_cost > 0:
        peering_pct = billing.peering_related_cost / billing.total_network_cost * 100
        interpretation = (
            f"VNet peering accounts for approximately {_fmt_pct(peering_pct)} of total "
            f"network costs ({_fmt_usd(billing.peering_related_cost)} out of "
            f"{_fmt_usd(billing.total_network_cost)}). "
            f"Note that exact peering-pair mapping may require traffic telemetry for "
            f"full attribution."
        )
    else:
        interpretation = (
            f"Total billed network cost is {_fmt_usd(billing.total_network_cost)}. "
            f"No rows explicitly matched VNet peering meter names — the cost may be "
            f"distributed across VPN, Load Balancer, and other networking services. "
            f"Traffic-level analysis (Mode 3) can provide better peering attribution."
        )

    recommendations = [
        "Billing data provides actual cost but limited topology visibility.",
        "For peering-pair level attribution, consider traffic flow analysis.",
    ]
    if billing.peering_related_cost > 5000:
        recommendations.append(
            "Significant peering cost detected — review inter-region traffic "
            "patterns for optimisation opportunities."
        )

    return InsightsResponse(
        mode="billing",
        headline="Billing analysis summary",
        insights=insights,
        interpretation=interpretation,
        recommendations=recommendations,
    )


# ---------------------------------------------------------------------------
# Mode 3 — Traffic import insights
# ---------------------------------------------------------------------------


def generate_traffic_insights(traffic: TrafficAnalysisResponse) -> InsightsResponse:
    """Generate Step 3 insights from traffic CSV analysis."""
    insights: list[InsightItem] = []

    # Total traffic
    total_tb = traffic.total_traffic_gb / 1024
    insights.append(
        InsightItem(
            icon="info",
            title="Observed traffic",
            value=f"{total_tb:,.1f} TB",
            description=f"Across {traffic.pair_count} unique region pairs.",
        )
    )

    # Estimated cost
    insights.append(
        InsightItem(
            icon="dollar",
            title="Estimated peering cost",
            value=_fmt_usd(traffic.total_estimated_cost),
            description="Based on public Azure pricing applied to observed traffic.",
        )
    )

    # Top pair
    if traffic.top_pairs:
        top = traffic.top_pairs[0]
        insights.append(
            InsightItem(
                icon="info",
                title="Top region pair",
                value=f"{top.source_region} → {top.target_region}",
                description=(
                    f"{top.traffic_gb:,.1f} GB — "
                    f"estimated cost: {_fmt_usd(top.estimated_monthly_cost)}"
                ),
            )
        )

    # Same-region vs cross-region split
    same_region_gb = sum(p.traffic_gb for p in traffic.all_pairs if p.is_same_region)
    cross_region_gb = sum(p.traffic_gb for p in traffic.all_pairs if not p.is_same_region)
    if same_region_gb > 0 and cross_region_gb > 0:
        insights.append(
            InsightItem(
                icon="check",
                title="Traffic split",
                value=f"{cross_region_gb:,.0f} GB cross-region",
                description=(
                    f"{same_region_gb:,.0f} GB same-region, {cross_region_gb:,.0f} GB cross-region."
                ),
            )
        )

    # Optimisation opportunities
    cross_pairs = [p for p in traffic.all_pairs if not p.is_same_region]
    if len(cross_pairs) >= 2:
        top_cost_pair = max(cross_pairs, key=lambda p: p.estimated_monthly_cost)
        insights.append(
            InsightItem(
                icon="warning",
                title="Highest cost pair",
                value=f"{top_cost_pair.source_region} → {top_cost_pair.target_region}",
                description=(
                    f"{_fmt_usd(top_cost_pair.estimated_monthly_cost)}/mo at "
                    f"{top_cost_pair.rate_per_gb:.3f} $/GB."
                ),
            )
        )

    # Interpretation
    cross_cost = sum(p.estimated_monthly_cost for p in cross_pairs)
    if traffic.total_estimated_cost > 0 and cross_cost > 0:
        interpretation = (
            f"Cross-region traffic accounts for {_fmt_usd(cross_cost)}/month in "
            f"estimated peering cost. "
            f"Traffic-driven architecture decisions — such as regional caching, "
            f"data locality, or CDN offload — can meaningfully reduce this cost "
            f"while preserving multi-region deployment benefits."
        )
    else:
        interpretation = (
            f"Total estimated peering cost is {_fmt_usd(traffic.total_estimated_cost)}/month. "
            f"At this level, peering cost is unlikely to be a significant factor "
            f"in multi-region architecture decisions."
        )

    recommendations = [
        "Traffic analysis provides the most accurate peering cost attribution.",
        "Focus optimisation on the highest-cost region pairs.",
    ]
    if cross_cost > 1000:
        recommendations.append(
            "Consider regional caching or data-replication strategies to reduce "
            "the highest cross-region flows."
        )

    return InsightsResponse(
        mode="traffic",
        headline="Traffic analysis summary",
        insights=insights,
        interpretation=interpretation,
        recommendations=recommendations,
    )
