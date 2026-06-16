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
campaign-agent pipeline.

```bash
# Build the image in ACR, then deploy (recommended for first deploy or after code changes)
python -m scripts.deploy_pricing_mcp_server --build

# Deploy only — image already in ACR, uses :latest (or TAG env var)
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


## Register for A365 directly

Use the helper script to generate and print the correctly formatted command from env vars:

```bash
PRICING_MCP_URL="https://pricing-mcp-server.<env-default-domain>/mcp" \
python -m scripts.register_pricing_a365_tool
```

Or run the command directly using the input file (recommended — avoids interactive prompts):

```bash
a365 develop-mcp register-external-mcp-server \
  -f src/pricing_mcp_server/register-external-mcp-server.json
```

Or with explicit flags:

```bash
a365 develop-mcp register-external-mcp-server \
  --server-name "ext_pricing" \
  --server-url "$PRICING_MCP_URL" \
  --publisher "Contoso" \
  --description "Internal retail pricing MCP server - provides procurement cost, weekly volume forecasts, and margin data for retail categories." \
  --auth-type "NoAuth" \
  --tools "list_categories,list_products,get_product_pricing,get_category_margin_forecast,get_volume_forecast,simulate_price_change,list_personas"

a365 develop-mcp register-external-mcp-server -f ./register-external-mcp-server.json

```

> **Note:** The server name must start with `ext_` and be ≤ 20 characters.

### Cleanup after a failed registration

If the registration fails the CLI may leave orphaned Entra app registrations behind.
Find and delete them before retrying:

```bash
# List all stale ext_pricing-related app registrations
az ad app list --display-name "ext_pricing" \
  --query "[].{name:displayName, appId:appId}" -o table

# Also check for the BYO app created by the backend
az ad app list --display-name "ext_pricing - BYO" \
  --query "[].{name:displayName, appId:appId}" -o table

# Delete each one by appId
az ad app delete --id <appId>
```

Common apps left behind after a failed run:

| Display name | Created by |
| --- | --- |
| `ext_pricing-A365Proxy` | CLI (first step) |
| `ext_pricing-PublicClients` | CLI (first step) |
| `ext_pricing - BYO` | Backend (second step) |

Once all stale registrations are removed, retry the registration.
