# az-scout-plugin-network-cost

An [az-scout](https://github.com/az-scout/az-scout) plugin that estimates Azure VNet peering costs between two regions. It helps teams evaluate multi-region architectures by showing that global VNet peering cost is predictable and typically **not** a blocker.

## What it does

The plugin computes monthly and annual VNet peering costs based on live pricing from the [Azure Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices). It supports:

- **Global VNet peering** (cross-region) — rates vary by Azure billing zone
- **Same-region VNet peering** — flat intra-region rates
- **Bidirectional traffic** — separate A→B and B→A volumes with per-direction cost breakdown
- **Billing zone detection** — automatically maps regions to Zone 1 (NA/Europe), Zone 2 (Asia Pacific), or Zone 3 (South America/Middle East/Africa)
- **Same-region vs. global comparison** — shows the delta between both pricing models

### Pricing data

Prices are fetched from the public Azure Retail Prices API (no authentication required):

```
GET https://prices.azure.com/api/retail/prices
  ?$filter=serviceName eq 'Virtual Network'
       and currencyCode eq 'USD'
       and priceType eq 'Consumption'
```

The plugin filters on four VNet peering meters: `Intra-Region Ingress`, `Intra-Region Egress`, `Inter-Region Ingress`, and `Inter-Region Egress`. Results are cached in-memory for 1 hour (configurable via `NETWORK_COST_CACHE_TTL` env var). If the API is unreachable, hardcoded fallback rates are used.

## Features

### UI tab

A "Network Cost" tab in the az-scout web interface with:

- Region selectors (grouped by billing zone)
- Traffic inputs in TB/month per direction
- Scenario toggle (global vs. same-region peering)
- Summary cards: monthly cost, annual cost, cost per TB
- Per-direction breakdown table (outbound/inbound rates and costs)
- Same-region vs. global delta comparison
- Pricing source indicator and explanatory notes

### API routes

Mounted at `/plugins/network-cost/`:

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/v1/estimate` | Estimate VNet peering cost (accepts `EstimateRequest`, returns `EstimateResponse`) |
| `GET` | `/v1/regions` | List all known regions grouped by billing zone |

### MCP tool

One tool registered on the az-scout MCP server, available in AI chat:

| Tool | Parameters | Description |
|------|-----------|-------------|
| `estimate_peering_cost` | `source_region`, `target_region`, `traffic_ab_gb`, `traffic_ba_gb`, `same_region` | Estimate VNet peering cost between two Azure regions |

The tool is also surfaced in the default chat mode via a system prompt addendum.

## Setup

```bash
cd az-scout-plugin-network-cost
uv pip install -e .
az-scout  # plugin is auto-discovered
```

## Project structure

```text
src/az_scout_network_cost/
├── __init__.py          # NetworkCostPlugin class + entry point
├── models.py            # Pydantic models (EstimateRequest, EstimateResponse, DirectionBreakdown)
├── pricing.py           # Pricing engine (zone detection, direction cost, full estimation)
├── price_fetcher.py     # Azure Retail Prices API client (fetch, cache, fallback)
├── routes.py            # FastAPI routes (/v1/estimate, /v1/regions)
├── tools.py             # MCP tool (estimate_peering_cost)
└── static/
    ├── css/network-cost.css          # Styles (light + dark theme)
    ├── html/network-cost-tab.html    # UI fragment (loaded at runtime)
    └── js/network-cost-tab.js        # Tab logic (region loading, estimation, rendering)
```

## How it works

1. On startup, az-scout discovers the plugin via the `az_scout.plugins` entry point.
2. The plugin registers its API routes, MCP tool, static assets, and UI tab.
3. The tab JS loads the HTML fragment into `#plugin-tab-network-cost`.
4. Region selectors are populated from `GET /v1/regions` (grouped by billing zone).
5. The user selects source/target regions, traffic volumes, and scenario.
6. Clicking "Estimate Cost" sends a `POST /v1/estimate` request.
7. The pricing engine fetches rates from the Azure Retail Prices API (or cache/fallback), computes per-direction costs, and returns a full breakdown with notes.

### Cost calculation

For each traffic direction, cost = **outbound** (source side) + **inbound** (destination side). Total = A→B cost + B→A cost.

- **Global peering**: outbound rate from the source region's billing zone, inbound rate from the target region's billing zone
- **Same-region peering**: flat $0.01/GB for both ingress and egress

## Configuration

| Environment variable | Default | Description |
|---------------------|---------|-------------|
| `NETWORK_COST_CACHE_TTL` | `3600` | Pricing cache TTL in seconds |

## Quality checks

CI runs lint and tests on Python 3.11–3.13. Run locally:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest
```

## CI/CD

- **CI** (`.github/workflows/ci.yml`): Lint + tests on push/PR to `main` (Python 3.11–3.13).
- **Publish** (`.github/workflows/publish.yml`): On version tags (`v*`) — builds, creates GitHub Release, publishes to PyPI via trusted publishing (OIDC).

## Versioning

Version is derived from git tags via `hatch-vcs`. Tags follow CalVer: `v2026.2.0`, `v2026.2.1`, etc.

## License

[MIT](LICENSE.txt)


## License

[MIT](LICENSE.txt)

## Disclaimer

> **This tool is not affiliated with Microsoft.** All capacity, pricing, and latency information are indicative and not a guarantee of deployment success. Spot placement scores are probabilistic. Quota values and pricing are dynamic and may change between planning and actual deployment. Latency values are based on [Microsoft published statistics](https://learn.microsoft.com/en-us/azure/networking/azure-network-latency) and must be validated with in-tenant measurements.
