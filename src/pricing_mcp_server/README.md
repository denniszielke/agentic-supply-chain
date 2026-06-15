# Pricing MCP Server

A small [Model Context Protocol](https://modelcontextprotocol.io) server that
exposes **internal** ALDI SÜD pricing, volume and margin master data to the
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
| `procurement_cost_eur` | What ALDI pays its supplier per unit |
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
