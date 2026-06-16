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
| `JOULE_AGENT_NAME` | Foundry agent name | `joule-agent` |
| `JOULE_A2A_CONNECTION_NAME` | Name of a **RemoteA2A** project connection (recommended; carries endpoint target + auth) | — |
| `JOULE_CONNECTION_ID` | Explicit connection id (alternative to the name) | — |
| `JOULE_AGENT_URL` | Public A2A base URL (only needed without a RemoteA2A connection) | derived from the ACA FQDN |
| `JOULE_AGENT_CARD_PATH` | Agent-card path | `/.well-known/agent-card.json` |
| `JOULE_BLUEPRINT_ID` | Managed agent identity blueprint id (Entra Agent ID) — advanced/undocumented | — |
| `JOULE_PREVIEW_FEATURES` | `Foundry-Features` opt-in header for the preview | `AgentEndpoints=V1Preview` |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Model for the proxy prompt agent | `gpt-4.1-mini` |

## How this maps to the official Foundry docs (public preview)

Verified against Microsoft Learn (June 2026). There are **two distinct** ways the
external Joule agent meets Foundry, and this repo covers both — but one of them is
a **portal** step with its own prerequisite:

1. **Make Joule callable from Foundry agents — the A2A *tool*** *(what
   `register_joule_agent.py` does, SDK)*. Per
   [Connect to an A2A endpoint](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/agent-to-agent),
   you create a prompt agent whose tool is an `A2APreviewTool` pointing at Joule.
   The **recommended** binding is a project **connection** of category `RemoteA2A`
   (it stores the endpoint `target` *and* the auth — including `AgenticIdentity`
   / **Entra Agent ID** passthrough). Create it once in the portal
   (**Tools → Connect tool → Custom → Agent2Agent (A2A)**) or via the ARM REST PUT
   in the doc, then pass `JOULE_A2A_CONNECTION_NAME`.

2. **Govern Joule as a control-plane *asset*** *(portal, needs an AI gateway)*. Per
   [Register a custom agent in Foundry Control Plane](https://learn.microsoft.com/azure/foundry/control-plane/register-custom-agent),
   you register the externally-hosted A2A agent via **Operate → Register asset**
   (Protocol = **A2A**, card path `/.well-known/agent-card.json`). Foundry then
   issues a **proxy URL** (via Azure API Management) and gives you access control +
   observability. **Prerequisite: an AI gateway (Azure API Management) must be
   enabled on the Foundry resource.** This step is **portal-driven** and is *not*
   performed by the script.

The Joule **server** matches the docs' "Option 2: build a custom A2A server using
the official A2A SDK", serving its card at `/.well-known/agent-card.json`.

> **Preview / availability.** The A2A tool is **public preview** (the `a2a_preview`
> tool type) — broadly available but with no SLA and possible region limits;
> confirm against your project. The `JOULE_BLUEPRINT_ID` (managed identity
> blueprint) path is advanced/undocumented and only sent when set. Foundry RBAC was
> recently renamed — you need **Contributor/Owner** on the Foundry resource plus
> **Foundry User**. Run `register_joule_agent --dry-run` first to inspect the
> payload.

## Key files

| File | Purpose |
| --- | --- |
| `server.py` | A2A server (executor, agent card, fulfilment logic), `/health` |
| `joule_data.json` | Synthetic SAP/ERP supply master data |
| `Dockerfile` | Container image (runs the A2A server) |
| `requirements.txt` | `a2a-sdk[http-server]`, `uvicorn` |
