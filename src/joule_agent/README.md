# joule_agent — Simulated SAP Joule agent (external A2A server)

`joule_agent` is a standalone **A2A** server that stands in for an **SAP Joule
agent** in the AI² narrative. It runs as its own Azure Container App — it is
**not** a Foundry hosted agent — and exposes ERP **supply-side** data over the
open [A2A protocol](https://a2a-protocol.org). Other agents (notably the Campaign
Planning Agent) hand it a scoped, auditable sub-task across the vendor boundary:

> *"We want to deepen the coffee promotion to ~30,000 units next week — can the
> supply chain actually fulfil that?"*

Even though Foundry never hosts it, the agent is still **registered in the Foundry
control plane** with a managed **agent identity blueprint** (Entra Agent ID) and
reached over A2A through a project connection — so it inherits the same identity,
governance and audit fabric as the in-Foundry agents. This is the "agents are
digital employees, governed by one identity model regardless of where they run"
boundary in action.

Built on the **official `a2a-sdk`** (`AgentExecutor` + `A2AStarletteApplication`).
All data is **synthetic** and lives in [`joule_data.json`](./joule_data.json),
loaded once at startup. There is no database. SKUs deliberately align with the
pricing MCP server so the campaign → supply handoff is coherent. Supplier names
are invented and do not refer to any real company.

## What it models

For each SKU the dataset carries an ERP supply snapshot:

| Field | Meaning |
| --- | --- |
| `stock_on_hand_units` | Current warehouse stock |
| `safety_stock_units` | Reserved buffer (not available to promise) |
| `in_transit_units` | Units already shipped, not yet received |
| `weekly_inbound_units` | Standing weekly replenishment |
| `open_purchase_orders` | Open POs with quantity, ETA and status |
| `supplier` | Vendor id, name, country and lead-time days |
| `dc_plant` | Distribution centre / plant |

**Available to promise** = `stock_on_hand − safety_stock`. A promotion is deemed
fulfillable for the week when *available + in-transit + POs landing within 7 days*
covers the requested volume; otherwise the agent reports the projected shortfall
and the earliest restock date.

## Skills (advertised in the Agent Card)

| Skill | Purpose |
| --- | --- |
| `fulfilment-check` | Can the supply chain fulfil a SKU at a forecast weekly volume? |
| `stock-lookup` | Current stock, supplier, lead time and open POs for a product |

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/.well-known/agent-card.json` | A2A Agent Card |
| `POST` | `/` | A2A JSON-RPC (`message/send`, `message/stream`) |
| `GET` | `/health` | Liveness probe |

## Run locally

```bash
python -m src.joule_agent.server
```

Serves A2A on `http://0.0.0.0:8092`. Override with `JOULE_AGENT_HOST` /
`JOULE_AGENT_PORT`. Set `JOULE_PUBLIC_URL` to control the URL advertised in the
Agent Card (defaults to the local bind address).

Try it:

```bash
curl http://localhost:8092/.well-known/agent-card.json

curl -s http://localhost:8092/ -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":"1","method":"message/send",
  "params":{"message":{"role":"user","messageId":"m1",
    "parts":[{"kind":"text","text":"Can we fulfil 30000 units of AS-FW-002 next week?"}]}}}'
```

## Deploy to Azure Container Apps

Two discrete, independently re-runnable steps (mirrors the pricing/campaign
pipeline):

```bash
# Step 1 — deploy as an external Container App (port 8092)
python -m scripts.deploy_joule_agent --build   # build image in ACR + deploy
python -m scripts.deploy_joule_agent           # deploy only (image already in ACR)

# Step 2 — register in the Foundry control plane (identity blueprint + external A2A)
python -m scripts.register_joule_agent --dry-run   # print the payload, no Azure calls
python -m scripts.register_joule_agent             # live registration
```

### Deploy variables

| Variable | Description | Default |
| --- | --- | --- |
| `JOULE_AGENT_APP_NAME` | Container App name | `joule-agent` |
| `JOULE_AGENT_PORT` | Container port | `8092` |
| `JOULE_AGENT_EXTERNAL` | Expose externally (`true`/`false`) | `true` |
| `JOULE_PUBLIC_URL` | Override the public URL in the Agent Card | derived from the ACA FQDN |

### Register variables

| Variable | Description | Default |
| --- | --- | --- |
| `JOULE_AGENT_NAME` | Control-plane agent name | `joule-agent` |
| `JOULE_AGENT_URL` | Public A2A base URL | derived from the ACA FQDN |
| `JOULE_AGENT_CARD_PATH` | Agent-card path | `/.well-known/agent-card.json` |
| `JOULE_BLUEPRINT_ID` | Managed agent identity blueprint id (Entra Agent ID) | — (strongly recommended) |
| `JOULE_CONNECTION_ID` | Foundry connection id holding auth to the A2A server | — (optional) |
| `JOULE_PREVIEW_FEATURES` | `Foundry-Features` opt-in header for the preview | `AgentEndpoints=V1Preview` |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Model for the control-plane shell agent | `gpt-4.1-mini` |

> **Preview note.** Attaching an external A2A endpoint to a control-plane agent
> uses Foundry preview features (`Foundry-Features: AgentEndpoints=V1Preview` — or
> `ExternalAgents=V1Preview`, set via `JOULE_PREVIEW_FEATURES` — and the
> `a2a_preview` tool). **These previews are not guaranteed enabled on every
> project/region**; confirm against your project (a live call returns a 4xx when
> the flag is not honoured). Run `register_joule_agent --dry-run` first to inspect
> the exact `create_version` / `patch_agent_details` payload, then run live once
> the preview is enabled on your project. Without `JOULE_BLUEPRINT_ID` the agent
> is still registered, but **without** the managed identity blueprint.

## Key files

| File | Purpose |
| --- | --- |
| `server.py` | A2A server (executor, agent card, fulfilment logic), `/health` |
| `joule_data.json` | Synthetic SAP/ERP supply master data |
| `Dockerfile` | Container image (runs the A2A server) |
| `requirements.txt` | `a2a-sdk[http-server]`, `uvicorn` |
