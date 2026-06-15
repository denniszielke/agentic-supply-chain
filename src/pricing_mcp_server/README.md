# Pricing MCP Server

A small [Model Context Protocol](https://modelcontextprotocol.io) server that
exposes **internal** retailer pricing, volume and margin master data to the
Campaign Planning Agent.

All data is **synthetic** and lives in [`pricing_data.json`](./pricing_data.json),
loaded once at startup. There is no database — the file *is* the source of
truth. This deliberately mirrors the real-world trust boundary: procurement
cost and margin are confidential and are only ever reachable through this
controlled MCP surface.

## What it models

For each internal SKU the dataset carries:

| Field | Meaning |
| --- | --- |
| `procurement_cost_eur` | What the retailer pays its supplier per unit |
| `logistics_cost_per_unit_eur` | Handling / distribution cost per unit |
| `current_shelf_price_eur` | Current consumer price |
| `expected_weekly_volume_units` | Base weekly demand forecast |
| `price_elasticity` | Constant-elasticity demand coefficient (negative) |

Gross margin is computed as `shelf_price − procurement_cost − logistics_cost`.
Volume responses to price changes use a constant-elasticity model, optionally
weighted by a shopping persona's price sensitivity.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_categories` | Aggregated weekly margin & volume per category |
| `list_products` | Internal products with cost / price / margin |
| `get_product_pricing` | Full pricing breakdown for one product |
| `get_category_margin_forecast` | Weekly volume / revenue / margin for a category |
| `get_volume_forecast` | Forecast weekly volume, optionally at a new price |
| `simulate_price_change` | Margin impact of a proposed shelf price |
| `list_personas` | Shopping personas with price sensitivity |

## Run

```bash
python -m src.pricing_mcp_server.server
```

Serves streamable-HTTP MCP at `http://127.0.0.1:8091/mcp`. Override the bind
address with `PRICING_MCP_HOST` / `PRICING_MCP_PORT`.

The Campaign Planning Agent connects to this URL via `MultiServerMCPClient`.

## Deploy to Azure Container Apps

Deploy this server as an (internal by default) Container App — step 1 of the
campaign-agent pipeline:

```bash
python -m scripts.deploy_pricing_mcp_server
```

This builds the image and deploys it via [`infra/core/host/app.bicep`](../../infra/core/host/app.bicep),
then prints the resulting `…/mcp` URL (e.g.
`https://pricing-mcp-server.<env-default-domain>/mcp`). All variables are sourced
from `./.env` (written by `azd up`); set `TAG` to the tag printed by
`scripts/build_containers.sh`.

| Variable | Description | Default |
| --- | --- | --- |
| `PRICING_MCP_APP_NAME` | Container App name | `pricing-mcp-server` |
| `PRICING_MCP_PORT` | Container port | `8091` |
| `PRICING_MCP_EXTERNAL` | Expose the app externally (`true`/`false`) | `false` (internal) |

Then register it as a Foundry toolbox and deploy the agent:

```bash
python -m scripts.register_pricing_toolbox   # step 2
python -m scripts.deploy_campaign_agent      # step 3
```
