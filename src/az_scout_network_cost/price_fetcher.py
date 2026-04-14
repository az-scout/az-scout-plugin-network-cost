"""Fetch VNet peering prices from the Azure Retail Prices API.

Endpoint: https://prices.azure.com/api/retail/prices
Filter:   serviceName eq 'Virtual Network'

The API returns per-region pricing for four peering meters:

  - Intra-Region Ingress / Egress  (same-region peering)
  - Inter-Region Ingress / Egress  (global VNet peering)

Results are cached in-memory with a configurable TTL (default 1 hour).
If the API is unreachable, hardcoded fallback prices are used so the
plugin remains functional offline.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_API_BASE = "https://prices.azure.com/api/retail/prices"
_FILTER = (
    "serviceName eq 'Virtual Network' and currencyCode eq 'USD' and priceType eq 'Consumption'"
)
_PAGE_SIZE = 100
_CACHE_TTL = int(os.environ.get("NETWORK_COST_CACHE_TTL", "86400"))  # 24 hours

# Meters of interest
_INTRA_INGRESS = "Intra-Region Ingress"
_INTRA_EGRESS = "Intra-Region Egress"
_INTER_INGRESS = "Inter-Region Ingress"
_INTER_EGRESS = "Inter-Region Egress"
_PEERING_METERS = {_INTRA_INGRESS, _INTRA_EGRESS, _INTER_INGRESS, _INTER_EGRESS}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class RegionPricing:
    """Peering prices for a single Azure region (USD / GB)."""

    intra_ingress: float = 0.01
    intra_egress: float = 0.01
    inter_ingress: float = 0.0
    inter_egress: float = 0.0


@dataclass
class PricingData:
    """Complete peering pricing fetched from the Azure Retail Prices API."""

    # Per-region inter-region peering rates
    regions: dict[str, RegionPricing] = field(default_factory=dict)
    # Intra-region rate (same for all regions)
    intra_ingress: float = 0.01
    intra_egress: float = 0.01
    # Metadata
    fetched_at: str = ""
    source: str = "hardcoded-fallback"


# ---------------------------------------------------------------------------
# Hardcoded fallback (used when the API is unreachable)
# ---------------------------------------------------------------------------

# Based on Azure Retail Prices API as of 2026-04.
# Zone 1 = $0.04/GB, Zone 2 = $0.09/GB, Zone 3 = $0.16/GB.
_FALLBACK_ZONE_RATES: dict[str, float] = {
    "zone1": 0.035,
    "zone2": 0.09,
    "zone3": 0.16,
}

_FALLBACK_ZONE_MAP: dict[str, str] = {
    # Zone 1 — North America
    "eastus": "zone1",
    "eastus2": "zone1",
    "centralus": "zone1",
    "northcentralus": "zone1",
    "southcentralus": "zone1",
    "westcentralus": "zone1",
    "westus": "zone1",
    "westus2": "zone1",
    "westus3": "zone1",
    "canadacentral": "zone1",
    "canadaeast": "zone1",
    # Zone 1 — Europe
    "northeurope": "zone1",
    "westeurope": "zone1",
    "francecentral": "zone1",
    "francesouth": "zone1",
    "germanywestcentral": "zone1",
    "germanynorth": "zone1",
    "norwayeast": "zone1",
    "norwaywest": "zone1",
    "swedencentral": "zone1",
    "swedensouth": "zone1",
    "switzerlandnorth": "zone1",
    "switzerlandwest": "zone1",
    "uksouth": "zone1",
    "ukwest": "zone1",
    "italynorth": "zone1",
    "polandcentral": "zone1",
    "spaincentral": "zone1",
    "denmarkeast": "zone1",
    "belgiumcentral": "zone1",
    "austriaeast": "zone1",
    "mexicocentral": "zone1",
    # Zone 2 — Asia Pacific, Australia, Japan
    "australiaeast": "zone2",
    "australiasoutheast": "zone2",
    "australiacentral": "zone2",
    "australiacentral2": "zone2",
    "japaneast": "zone2",
    "japanwest": "zone2",
    "eastasia": "zone2",
    "southeastasia": "zone2",
    "koreacentral": "zone2",
    "koreasouth": "zone2",
    "centralindia": "zone2",
    "southindia": "zone2",
    "westindia": "zone2",
    "jioindiawest": "zone2",
    "jioindiacentral": "zone2",
    "indonesiacentral": "zone2",
    "malaysiawest": "zone2",
    "newzealandnorth": "zone2",
    # Zone 3 — South America, Middle East, Africa
    "brazilsouth": "zone3",
    "southafricanorth": "zone3",
    "southafricawest": "zone3",
    "uaenorth": "zone3",
    "uaecentral": "zone3",
    "qatarcentral": "zone3",
    "chilecentral": "zone3",
    "israelcentral": "zone1",
    "israelnorthwest": "zone2",
    # US Gov
    "usgovarizona": "zone1",
    "usgovtexas": "zone1",
    "usgovvirginia": "zone1",
}


def _build_fallback() -> PricingData:
    """Build pricing data from hardcoded zone rates."""
    regions: dict[str, RegionPricing] = {}
    for region, zone in _FALLBACK_ZONE_MAP.items():
        rate = _FALLBACK_ZONE_RATES[zone]
        regions[region] = RegionPricing(
            inter_ingress=rate,
            inter_egress=rate,
        )
    return PricingData(
        regions=regions,
        source="hardcoded-fallback",
        fetched_at="",
    )


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_cached_data: PricingData | None = None
_cached_at: float = 0.0


def _is_cache_valid() -> bool:
    return _cached_data is not None and (time.monotonic() - _cached_at) < _CACHE_TTL


# ---------------------------------------------------------------------------
# API fetcher
# ---------------------------------------------------------------------------


def _fetch_from_api() -> PricingData:
    """Query the Azure Retail Prices API for all VNet peering meters.

    Paginates through all results and builds a ``PricingData`` mapping.
    """
    regions: dict[str, RegionPricing] = {}
    intra_ingress = 0.01
    intra_egress = 0.01

    url: str | None = f"{_API_BASE}?$filter={_FILTER}&$top={_PAGE_SIZE}"
    page = 0

    with httpx.Client(timeout=30.0) as client:
        while url:
            page += 1
            resp = client.get(url)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()

            for item in data.get("Items", []):
                meter: str = item.get("meterName", "")
                if meter not in _PEERING_METERS:
                    continue

                region: str = (item.get("armRegionName") or "").lower()
                price: float = float(item.get("retailPrice", 0))

                # Intra-region uses "Global" or specific edge regions — capture rate
                if meter in (_INTRA_INGRESS, _INTRA_EGRESS):
                    if meter == _INTRA_INGRESS:
                        intra_ingress = price
                    else:
                        intra_egress = price
                    continue

                # Skip the "Global" aggregate row for inter-region
                if not region or region == "global":
                    continue

                rp = regions.setdefault(region, RegionPricing())
                if meter == _INTER_INGRESS:
                    rp.inter_ingress = price
                elif meter == _INTER_EGRESS:
                    rp.inter_egress = price

            url = data.get("NextPageLink")

    logger.info(
        "Fetched VNet peering prices: %d regions across %d pages",
        len(regions),
        page,
    )

    return PricingData(
        regions=regions,
        intra_ingress=intra_ingress,
        intra_egress=intra_egress,
        source="azure-retail-prices-api",
        fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_pricing() -> PricingData:
    """Return cached peering prices, refreshing from the API if stale.

    Falls back to hardcoded prices if the API is unreachable.
    """
    global _cached_data, _cached_at  # noqa: PLW0603

    if _is_cache_valid():
        assert _cached_data is not None
        return _cached_data

    try:
        _cached_data = _fetch_from_api()
        _cached_at = time.monotonic()
        return _cached_data
    except Exception:
        logger.warning(
            "Failed to fetch prices from Azure Retail Prices API — using fallback",
            exc_info=True,
        )
        _cached_data = _build_fallback()
        _cached_at = time.monotonic()
        return _cached_data


def get_region_rates(region: str) -> RegionPricing | None:
    """Return peering rates for a specific region, or ``None`` if unknown."""
    pricing = get_pricing()
    return pricing.regions.get(region.strip().lower())


def get_known_regions() -> dict[str, RegionPricing]:
    """Return all known regions and their peering rates."""
    return get_pricing().regions


def get_intra_region_rates() -> tuple[float, float]:
    """Return (intra_ingress, intra_egress) rates in USD/GB."""
    pricing = get_pricing()
    return pricing.intra_ingress, pricing.intra_egress


def get_pricing_source() -> str:
    """Return the data source label ('azure-retail-prices-api' or 'hardcoded-fallback')."""
    return get_pricing().source
