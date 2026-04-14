"""CSV parsers for billing and traffic data imports.

Billing parser
--------------
Parses Azure Cost Management CSV exports (Usage Details) to extract
network-related rows and identify likely VNet-peering entries using
meter / category naming heuristics.

Traffic parser
--------------
Parses a simplified CSV with columns: source_region, target_region,
direction, traffic_gb — then aggregates by region pair and estimates
peering cost using the pricing engine.
"""

from __future__ import annotations

import csv
import io
import logging
from collections import defaultdict
from typing import Any

from az_scout.plugin_api import PluginValidationError

from az_scout_network_cost.models import (
    BillingAnalysisResponse,
    BillingMeterSummary,
    BillingRegionSummary,
    TrafficAnalysisResponse,
    TrafficPairSummary,
)
from az_scout_network_cost.price_fetcher import (
    get_intra_region_rates,
    get_pricing_source,
    get_region_rates,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column detection helpers
# ---------------------------------------------------------------------------

# Possible column names for key fields in billing CSVs (case-insensitive).
# Both EA and MCA/MPA formats are supported, plus pivot-table style headers.
_COST_COLUMNS = {
    "costinbillingcurrency", "cost", "pretaxcost", "billingcost",
    "extendedcost", "totalcost", "effectiveprice",
    "sum of cost", "sum of pretaxcost", "sum of costinbillingcurrency",
}
_METER_CATEGORY_COLUMNS = {
    "metercategory", "meter category", "servicename", "service name",
    "consumedservice", "consumed service",
}
_METER_SUBCATEGORY_COLUMNS = {
    "metersubcategory", "meter sub-category", "meter subcategory",
    "metersub-category",
}
_METER_NAME_COLUMNS = {
    "metername", "meter name", "meter",
}
_REGION_COLUMNS = {
    "resourcelocation", "resource location", "region", "location",
    "meterlocation", "meter location", "meterregion", "meter region",
}
_USAGE_COLUMNS = {
    "quantity", "usagequantity", "usage quantity", "consumedquantity",
    "count of resourceid", "sum of quantity", "sum of usagequantity",
}
_UNIT_COLUMNS = {
    "unitofmeasure", "unit of measure", "unit",
}


def _find_column(headers: list[str], candidates: set[str]) -> str | None:
    """Find the first header that matches one of the candidate names.

    Uses exact match first, then falls back to substring matching
    to handle pivot-table style headers like "Sum of Cost".
    """
    header_map = {h.lower().strip(): h for h in headers}
    # Exact match
    for candidate in candidates:
        if candidate in header_map:
            return header_map[candidate]
    # Substring fallback: header contains a candidate or vice versa
    for header_lower, header_original in header_map.items():
        for candidate in candidates:
            if candidate in header_lower or header_lower in candidate:
                return header_original
    return None


import re

# Regex to strip currency symbols, spaces, and thousands separators
_CURRENCY_RE = re.compile(r"[€$£¥₹\s\u00a0]")


def _parse_number(raw: str | None) -> float | None:
    """Parse a numeric string that may contain currency symbols or locale formatting.

    Handles: "€ 67.99", "-€ 0.21", "1,234.56", "1.234,56" (EU), "$100", etc.
    Returns None if unparseable.
    """
    if not raw:
        return None
    s = _CURRENCY_RE.sub("", raw.strip())
    if not s:
        return None
    # Handle negative with symbol after minus: "-€ 0.21" → already "-0.21"
    # Detect EU locale: "1.234,56" → last separator is comma
    if "," in s and "." in s:
        # Whichever comes last is the decimal separator
        if s.rfind(",") > s.rfind("."):
            # EU: "1.234,56" → remove dots, replace comma with dot
            s = s.replace(".", "").replace(",", ".")
        else:
            # US: "1,234.56" → remove commas
            s = s.replace(",", "")
    elif "," in s and "." not in s:
        # Could be EU decimal "0,21" or US thousands "1,000"
        # If single comma with ≤2 digits after → decimal
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Network-related heuristics
# ---------------------------------------------------------------------------

_NETWORK_CATEGORIES = {
    "virtual network", "bandwidth", "networking", "network watcher",
    "azure dns", "load balancer", "vpn gateway", "expressroute",
    "application gateway", "traffic manager", "azure firewall",
    "private link", "nat gateway",
}

_PEERING_KEYWORDS = {
    "peering", "vnet peering", "inter-region", "intra-region",
    "virtual network peering", "global vnet peering",
}


def _is_network_row(category: str, subcategory: str, meter_name: str) -> bool:
    """Check if a billing row is network-related."""
    combined = f"{category} {subcategory} {meter_name}".lower()
    return any(kw in combined for kw in _NETWORK_CATEGORIES)


def _is_peering_row(category: str, subcategory: str, meter_name: str) -> bool:
    """Check if a billing row is likely VNet peering related."""
    combined = f"{category} {subcategory} {meter_name}".lower()
    if any(kw in combined for kw in _PEERING_KEYWORDS):
        return True
    # "Virtual Network" category with Ingress/Egress meters
    if "virtual network" in category.lower():
        if any(kw in meter_name.lower() for kw in ("ingress", "egress")):
            return True
    return False


# ---------------------------------------------------------------------------
# Billing CSV parser
# ---------------------------------------------------------------------------


def parse_billing_csv(content: str) -> BillingAnalysisResponse:
    """Parse an Azure billing CSV export and extract network cost analysis.

    Supports both EA and MCA/MPA export formats.  Detects columns
    dynamically and applies heuristics to identify VNet-peering-related
    entries.

    Parameters
    ----------
    content:
        Raw CSV text content.

    Returns
    -------
    BillingAnalysisResponse
        Structured summary with meter and region breakdowns.

    Raises
    ------
    PluginValidationError
        If the CSV is empty, has no recognisable columns, or contains
        no parseable rows.
    """
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise PluginValidationError("CSV file appears empty or has no header row.")

    headers = list(reader.fieldnames)

    # Detect columns
    cost_col = _find_column(headers, _COST_COLUMNS)
    cat_col = _find_column(headers, _METER_CATEGORY_COLUMNS)
    subcat_col = _find_column(headers, _METER_SUBCATEGORY_COLUMNS)
    meter_col = _find_column(headers, _METER_NAME_COLUMNS)
    region_col = _find_column(headers, _REGION_COLUMNS)
    usage_col = _find_column(headers, _USAGE_COLUMNS)
    unit_col = _find_column(headers, _UNIT_COLUMNS)

    if not cost_col:
        raise PluginValidationError(
            f"Could not find a cost column. Expected one of: "
            f"{', '.join(sorted(_COST_COLUMNS))}. "
            f"Found columns: {', '.join(headers[:20])}"
        )

    # Aggregate data
    meter_agg: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "meter_category": "",
            "meter_sub_category": "",
            "meter_name": "",
            "region": "",
            "total_cost": 0.0,
            "total_usage": 0.0,
            "unit": "",
            "row_count": 0,
        }
    )
    region_costs: dict[str, dict[str, float]] = defaultdict(
        lambda: {"total_cost": 0.0, "peering_cost": 0.0, "meter_count": 0}
    )

    total_rows = 0
    network_rows = 0
    peering_rows = 0

    for row in reader:
        total_rows += 1

        cost = _parse_number(row.get(cost_col, "0"))
        if cost is None:
            continue

        category = row.get(cat_col, "") if cat_col else ""
        subcategory = row.get(subcat_col, "") if subcat_col else ""
        meter_name = row.get(meter_col, "") if meter_col else ""
        region = row.get(region_col, "") if region_col else ""
        usage_str = row.get(usage_col, "0") if usage_col else "0"
        unit = row.get(unit_col, "") if unit_col else ""

        usage = _parse_number(usage_str) or 0.0

        if not _is_network_row(category, subcategory, meter_name):
            continue

        network_rows += 1
        is_peering = _is_peering_row(category, subcategory, meter_name)
        if is_peering:
            peering_rows += 1

        # Aggregate by meter key
        key = f"{category}|{subcategory}|{meter_name}|{region}"
        agg = meter_agg[key]
        agg["meter_category"] = category
        agg["meter_sub_category"] = subcategory
        agg["meter_name"] = meter_name
        agg["region"] = region
        agg["total_cost"] += cost
        agg["total_usage"] += usage
        agg["unit"] = unit
        agg["row_count"] += 1

        # Region aggregation
        region_key = region.lower().strip() or "unknown"
        region_costs[region_key]["total_cost"] += cost
        if is_peering:
            region_costs[region_key]["peering_cost"] += cost
        region_costs[region_key]["meter_count"] += 1

    if total_rows == 0:
        raise PluginValidationError("CSV file contains no data rows.")

    # Build response
    meter_breakdown = sorted(
        [BillingMeterSummary(**v) for v in meter_agg.values()],
        key=lambda m: m.total_cost,
        reverse=True,
    )

    region_breakdown = sorted(
        [
            BillingRegionSummary(
                region=r,
                total_cost=round(v["total_cost"], 2),
                peering_cost=round(v["peering_cost"], 2),
                meter_count=int(v["meter_count"]),
            )
            for r, v in region_costs.items()
        ],
        key=lambda r: r.total_cost,
        reverse=True,
    )

    total_network_cost = round(sum(m.total_cost for m in meter_breakdown), 2)
    peering_related_cost = round(
        sum(
            m.total_cost
            for m in meter_breakdown
            if _is_peering_row(m.meter_category, m.meter_sub_category, m.meter_name)
        ),
        2,
    )

    dominant = region_breakdown[0].region if region_breakdown else ""

    notes: list[str] = []
    notes.append(f"Parsed {total_rows:,} rows, found {network_rows:,} network-related entries.")
    if peering_rows > 0:
        notes.append(
            f"Identified {peering_rows:,} rows likely related to VNet peering "
            f"(${peering_related_cost:,.2f})."
        )
    else:
        notes.append(
            "No rows explicitly matched VNet peering meter names. "
            "Network costs shown may include other services (VPN, LB, etc.)."
        )

    return BillingAnalysisResponse(
        total_network_cost=total_network_cost,
        peering_related_cost=peering_related_cost,
        total_rows_parsed=total_rows,
        network_rows_found=network_rows,
        peering_rows_found=peering_rows,
        meter_breakdown=meter_breakdown,
        region_breakdown=region_breakdown,
        dominant_region=dominant,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Traffic CSV parser
# ---------------------------------------------------------------------------

_TRAFFIC_REQUIRED_COLUMNS = {"source_region", "target_region", "traffic_gb"}


def parse_traffic_csv(content: str) -> TrafficAnalysisResponse:
    """Parse a simplified traffic CSV and estimate peering cost.

    Expected columns: source_region, target_region, direction (optional),
    traffic_gb.

    Parameters
    ----------
    content:
        Raw CSV text content.

    Returns
    -------
    TrafficAnalysisResponse
        Aggregated traffic with estimated peering costs.

    Raises
    ------
    PluginValidationError
        If required columns are missing or no data is found.
    """
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise PluginValidationError("Traffic CSV appears empty or has no header row.")

    # Normalise headers
    header_map = {h.lower().strip(): h for h in reader.fieldnames}
    missing = _TRAFFIC_REQUIRED_COLUMNS - set(header_map.keys())
    if missing:
        raise PluginValidationError(
            f"Missing required columns: {', '.join(sorted(missing))}. "
            f"Expected: source_region, target_region, traffic_gb. "
            f"Found: {', '.join(reader.fieldnames)}"
        )

    src_col = header_map["source_region"]
    tgt_col = header_map["target_region"]
    gb_col = header_map["traffic_gb"]
    dir_col = header_map.get("direction")

    # Aggregate by region pair
    pair_agg: dict[tuple[str, str], float] = defaultdict(float)
    total_rows = 0

    for row in reader:
        total_rows += 1
        src = (row.get(src_col, "") or "").strip().lower()
        tgt = (row.get(tgt_col, "") or "").strip().lower()

        if not src or not tgt:
            continue

        try:
            gb = float(row.get(gb_col, "0") or "0")
        except (ValueError, TypeError):
            continue

        pair_agg[(src, tgt)] += gb

    if total_rows == 0:
        raise PluginValidationError("Traffic CSV contains no data rows.")

    if not pair_agg:
        raise PluginValidationError(
            "No valid region pairs found in the traffic CSV. "
            "Ensure source_region and target_region columns have values."
        )

    # Estimate cost for each pair
    all_pairs: list[TrafficPairSummary] = []
    total_traffic = 0.0
    total_cost = 0.0

    for (src, tgt), gb in sorted(pair_agg.items(), key=lambda x: x[1], reverse=True):
        is_same = src == tgt
        rate = 0.0

        if is_same:
            _, intra_out = get_intra_region_rates()
            # Same-region peering: ingress + egress per GB
            rate = intra_out * 2  # simplified: both directions same region
        else:
            src_rates = get_region_rates(src)
            tgt_rates = get_region_rates(tgt)
            if src_rates and tgt_rates:
                # outbound from source + inbound to target
                rate = src_rates.inter_egress + tgt_rates.inter_ingress
            elif src_rates:
                rate = src_rates.inter_egress
            elif tgt_rates:
                rate = tgt_rates.inter_ingress
            # If both unknown, rate stays 0 — will be noted

        est_cost = round(gb * rate, 2)
        total_traffic += gb
        total_cost += est_cost

        # Resolve zones
        src_zone = ""
        tgt_zone = ""
        from az_scout_network_cost.models import RATE_TO_ZONE

        if not is_same:
            src_r = get_region_rates(src)
            tgt_r = get_region_rates(tgt)
            if src_r:
                src_zone = RATE_TO_ZONE.get(src_r.inter_egress, "unknown")
            if tgt_r:
                tgt_zone = RATE_TO_ZONE.get(tgt_r.inter_egress, "unknown")

        all_pairs.append(
            TrafficPairSummary(
                source_region=src,
                target_region=tgt,
                source_zone=src_zone,
                target_zone=tgt_zone,
                traffic_gb=round(gb, 2),
                is_same_region=is_same,
                estimated_monthly_cost=est_cost,
                rate_per_gb=round(rate, 4),
            )
        )

    top_pairs = all_pairs[:10]  # top 10 by traffic volume

    # Find dominant pair and direction
    dominant_pair = ""
    dominant_direction = ""
    if all_pairs:
        top = all_pairs[0]
        dominant_pair = f"{top.source_region} → {top.target_region}"
        dominant_direction = dominant_pair

    notes: list[str] = [
        f"Parsed {total_rows:,} traffic rows, found {len(all_pairs)} unique region pairs.",
        f"Total observed traffic: {total_traffic:,.1f} GB.",
    ]

    unknown_regions = [
        p for p in all_pairs if not p.is_same_region and p.rate_per_gb == 0
    ]
    if unknown_regions:
        names = ", ".join(
            f"{p.source_region}→{p.target_region}" for p in unknown_regions[:3]
        )
        notes.append(
            f"⚠ {len(unknown_regions)} pair(s) contain unknown regions "
            f"(cost not estimated): {names}"
        )

    return TrafficAnalysisResponse(
        total_traffic_gb=round(total_traffic, 2),
        total_estimated_cost=round(total_cost, 2),
        pair_count=len(all_pairs),
        top_pairs=top_pairs,
        all_pairs=all_pairs,
        dominant_pair=dominant_pair,
        dominant_direction=dominant_direction,
        notes=notes,
        pricing_source=get_pricing_source(),
    )
