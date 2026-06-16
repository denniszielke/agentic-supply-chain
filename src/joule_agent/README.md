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

# Preflight — clear green/red on the prerequisites before registering
python -m scripts.preflight_joule_agent            # read-only checks
python -m scripts.preflight_joule_agent --probe     # also probe the A2A preview

# Step 2 — register in Foundry with the agent identity blueprint + A2A
python -m scripts.register_joule_agent --dry-run   # print the payload, no Azure calls
python -m scripts.register_joule_agent             # live registration
```

### Preflight checks

`preflight_joule_agent.py` gives a single green/red verdict (exit code 0/1) before
the demo. It checks: Foundry endpoint + Entra auth, the Joule Agent Card + `/health`
are reachable, `JOULE_BLUEPRINT_ID` is set (and best-effort resolvable in Entra via
Graph), a RemoteA2A connection exists (when configured), and — with `--probe` — that
the **A2A preview is accepted** (by creating then deleting a throwaway agent version).

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
| `JOULE_BLUEPRINT_ID` | **Agent identity blueprint appId (Entra Agent ID)** — the central identity input | — |
| `JOULE_PREVIEW_FEATURES` | `Foundry-Features` opt-in header for the preview | `AgentEndpoints=V1Preview` |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Model for the proxy prompt agent | `gpt-4.1-mini` |

## The agent identity blueprint (the important part)

Per Dennis's requirement, the externally-hosted Joule agent must **use an agent
identity blueprint** — a [Microsoft Entra Agent ID](https://learn.microsoft.com/entra/agent-id/agent-blueprint)
construct that gives a *class* of agents a governed, auditable identity (Conditional
Access, revoke-at-scale, audit), even though the agent runs outside Foundry. See
the [Foundry agent identity concept](https://learn.microsoft.com/azure/foundry/agents/concepts/agent-identity).

Create the blueprint once and pass its `appId` as `JOULE_BLUEPRINT_ID`:

1. **Entra admin center** — Entra ID → **Agents → Agent blueprints → New agent
   blueprint**, set a name + sponsor/owner. Or use **Microsoft Graph / PowerShell**
   ([create-blueprint](https://learn.microsoft.com/entra/agent-id/create-blueprint)).
2. Because Joule **receives incoming A2A requests**, configure an **identifier URI
   and scope** on the blueprint (Graph/PowerShell), and a **federated credential**
   to the project managed identity (recommended for production).
3. Record the blueprint **`appId`** → `JOULE_BLUEPRINT_ID`.

`register_joule_agent.py` then attaches it via `create_version(blueprint_reference=
ManagedAgentIdentityBlueprintReference(blueprint_id=<appId>))`.

## How this maps to the official Foundry docs (verified June 2026)

The external Joule agent meets Foundry in two complementary ways:

1. **Identity — the agent identity blueprint** *(above; the central requirement)*.
2. **Reachability — the A2A *tool***. Per
   [Connect to an A2A endpoint](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/agent-to-agent),
   `register_joule_agent.py` creates a prompt agent whose tool is an `A2APreviewTool`
   pointing at Joule, bound (recommended) to a project **connection** of category
   `RemoteA2A` that stores the endpoint `target` + auth (including `AgenticIdentity`
   / Entra Agent ID passthrough). Create it in the portal
   (**Tools → Connect tool → Custom → Agent2Agent (A2A)**) or via the ARM REST PUT
   in the doc, then pass `JOULE_A2A_CONNECTION_NAME`.

Optionally, govern Joule as a control-plane **asset** (proxy URL + observability)
via [Register a custom agent](https://learn.microsoft.com/azure/foundry/control-plane/register-custom-agent)
— a **portal** step that **requires an AI gateway (Azure API Management)** on the
Foundry resource, and is *not* performed by the script.

The Joule **server** matches the docs' "Option 2: build a custom A2A server using
the official A2A SDK", serving its card at `/.well-known/agent-card.json`.

> **Preview / availability.** The A2A tool is **public preview** (`a2a_preview`) —
> broadly available but no SLA and possible region limits; confirm against your
> project. Foundry RBAC was recently renamed — you need **Contributor/Owner** on the
> Foundry resource plus **Foundry User**; creating the blueprint needs Entra **Agent
> ID Developer/Administrator** (or Application Administrator). Run
> `register_joule_agent --dry-run` first to inspect the payload.

## Key files

| File | Purpose |
| --- | --- |
| `server.py` | A2A server (executor, agent card, fulfilment logic), `/health` |
| `joule_data.json` | Synthetic SAP/ERP supply master data |
| `Dockerfile` | Container image (runs the A2A server) |
| `requirements.txt` | `a2a-sdk[http-server]`, `uvicorn` |
