"""Tests for the VNet peering pricing engine."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from az_scout_network_cost.models import EstimateRequest
from az_scout_network_cost.price_fetcher import PricingData, RegionPricing
from az_scout_network_cost.pricing import estimate, get_billing_zone

# ---------------------------------------------------------------------------
# Fixtures — deterministic pricing data (no API calls)
# ---------------------------------------------------------------------------

_TEST_PRICING = PricingData(
    regions={
        "westeurope": RegionPricing(inter_ingress=0.035, inter_egress=0.035),
        "francecentral": RegionPricing(inter_ingress=0.035, inter_egress=0.035),
        "northeurope": RegionPricing(inter_ingress=0.035, inter_egress=0.035),
        "southeastasia": RegionPricing(inter_ingress=0.09, inter_egress=0.09),
        "centralindia": RegionPricing(inter_ingress=0.09, inter_egress=0.09),
        "brazilsouth": RegionPricing(inter_ingress=0.16, inter_egress=0.16),
    },
    intra_ingress=0.01,
    intra_egress=0.01,
    source="test-fixture",
)


def _mock_get_pricing() -> PricingData:
    return _TEST_PRICING


@pytest.fixture(autouse=True)
def _use_test_pricing() -> object:  # type: ignore[return]
    """Patch the price fetcher so tests never hit the real API."""
    with patch(
        "az_scout_network_cost.price_fetcher.get_pricing",
        side_effect=_mock_get_pricing,
    ):
        yield


class TestGetBillingZone:
    """Region → billing zone resolution."""

    def test_europe_is_zone1(self) -> None:
        assert get_billing_zone("westeurope") == "zone1"
        assert get_billing_zone("francecentral") == "zone1"
        assert get_billing_zone("northeurope") == "zone1"

    def test_asia_is_zone2(self) -> None:
        assert get_billing_zone("southeastasia") == "zone2"
        assert get_billing_zone("centralindia") == "zone2"

    def test_brazil_is_zone3(self) -> None:
        assert get_billing_zone("brazilsouth") == "zone3"

    def test_case_insensitive(self) -> None:
        assert get_billing_zone("WestEurope") == "zone1"
        assert get_billing_zone(" westeurope ") == "zone1"

    def test_unknown_region_raises(self) -> None:
        with pytest.raises(Exception, match="Unknown Azure region"):
            get_billing_zone("mars-central")


class TestEstimate:
    """End-to-end pricing estimation."""

    def test_same_region_peering(self) -> None:
        req = EstimateRequest(
            source_region="westeurope",
            target_region="westeurope",
            traffic_ab_gb=1000,
            traffic_ba_gb=1000,
            same_region=True,
        )
        result = estimate(req)
        assert result.pricing_model == "same-region-vnet-peering"
        # 1000 GB × ($0.01 out + $0.01 in) = $20 per direction
        # Total = $20 + $20 = $40
        assert result.monthly_total_usd == 40.0
        assert result.annual_total_usd == 480.0

    def test_global_peering_zone1(self) -> None:
        req = EstimateRequest(
            source_region="westeurope",
            target_region="francecentral",
            traffic_ab_gb=1000,
            traffic_ba_gb=1000,
            same_region=False,
        )
        result = estimate(req)
        assert result.pricing_model == "global-vnet-peering"
        assert result.source_zone == "zone1"
        assert result.target_zone == "zone1"
        # Zone 1 rate = $0.035/GB (from Azure Retail Prices API)
        # 1000 GB × ($0.035 out + $0.035 in) = $70 per direction
        # Total = $70 + $70 = $140
        assert result.monthly_total_usd == 140.0
        assert result.annual_total_usd == 1680.0

    def test_cross_zone_peering(self) -> None:
        req = EstimateRequest(
            source_region="westeurope",
            target_region="southeastasia",
            traffic_ab_gb=500,
            traffic_ba_gb=200,
            same_region=False,
        )
        result = estimate(req)
        assert result.source_zone == "zone1"
        assert result.target_zone == "zone2"
        # A→B: 500 × ($0.035 outbound from zone1 + $0.09 inbound to zone2) = $62.50
        # B→A: 200 × ($0.09 outbound from zone2 + $0.035 inbound to zone1) = $25.00
        # Total = $87.50
        assert result.monthly_total_usd == 87.5

    def test_zero_traffic(self) -> None:
        req = EstimateRequest(
            source_region="westeurope",
            target_region="francecentral",
            traffic_ab_gb=0,
            traffic_ba_gb=0,
            same_region=False,
        )
        result = estimate(req)
        assert result.monthly_total_usd == 0.0
        assert result.per_tb_usd == 0.0

    def test_breakdown_has_two_directions(self) -> None:
        req = EstimateRequest(
            source_region="westeurope",
            target_region="francecentral",
            traffic_ab_gb=100,
            traffic_ba_gb=50,
            same_region=False,
        )
        result = estimate(req)
        assert len(result.breakdown) == 2
        assert "A → B" in result.breakdown[0].direction
        assert "B → A" in result.breakdown[1].direction

    def test_notes_include_billing_zone_message(self) -> None:
        req = EstimateRequest(
            source_region="westeurope",
            target_region="francecentral",
            traffic_ab_gb=100,
            traffic_ba_gb=100,
            same_region=False,
        )
        result = estimate(req)
        assert any("billing zone" in n.lower() for n in result.notes)

    def test_per_tb_calculation(self) -> None:
        req = EstimateRequest(
            source_region="westeurope",
            target_region="francecentral",
            traffic_ab_gb=1024,
            traffic_ba_gb=0,
            same_region=False,
        )
        result = estimate(req)
        # Monthly = 1024 × ($0.035 + $0.035) = $71.68
        # per_tb = $71.68 / 1024 GB × 1000 = $70.0
        assert result.per_tb_usd == 70.0

    def test_pricing_source_is_reported(self) -> None:
        req = EstimateRequest(
            source_region="westeurope",
            target_region="francecentral",
            traffic_ab_gb=100,
            traffic_ba_gb=100,
            same_region=False,
        )
        result = estimate(req)
        assert result.pricing_source == "test-fixture"
