---
name: ops
description: >
  Operations runbook for the agentic-supply-chain repo. USE THIS SKILL when the
  user asks to deploy, build, provision, ingest, index, register, or clean up any
  part of the project — including infrastructure (azd), container images, Container
  Apps, AI Search indexes, Foundry toolboxes, and hosted agents. Covers the full
  lifecycle: provision → build → index → ingest → deploy → register → clean up.
---

# Ops Runbook — agentic-supply-chain

All commands are run from the **repo root**. Environment variables come from
`./.env`, which `azd up` writes automatically. Activate the venv first:

```bash
source .venv/bin/activate   # or: .venv/bin/python -m scripts.<name>
```

---

## 0. Prerequisites

| Tool | Version | Install |
|---|---|---|
| Azure Developer CLI (`azd`) | latest | https://aka.ms/azd |
| Azure CLI (`az`) | ≥ 2.60 | https://aka.ms/azcli |
| Python | 3.12 + | |

Install Python deps:
```bash
pip install -r requirements.txt
```

---

## 1. Provision Infrastructure

Creates all long-lived Azure resources (AI Foundry project, Azure AI Search,
Container Apps environment, VNet, ACR, user-assigned managed identity) and writes
all outputs to `./.env`.

```bash
azd up
```

Runs `postprovision` hook automatically:
- creates AI Search indexes + knowledge base (`scripts/deploy_assets.py`)

Runs `postdeploy` hook automatically:
- copies `.env`, builds all container images in ACR (`scripts/build_containers.sh`)

To provision only (no deploy):
```bash
azd provision
```

To deploy only (infra already provisioned):
```bash
azd deploy
```

To tear everything down:
```bash
azd down
```

---

## 2. Build Container Images

Builds all five service images in ACR (no local Docker). Each image is tagged
with both a concrete timestamp tag **and** `:latest`.

```bash
./scripts/build_containers.sh "${AZURE_ENV_NAME}"
# prints: Registry: myregistry.azurecr.io, Tag: 20260615120000
```

Override the tag:
```bash
./scripts/build_containers.sh "${AZURE_ENV_NAME}" 20260615120000
```

Images built:
- `shopping-chat` — from `src/shopping_chat/Dockerfile`
- `promotion-ingestion` — from `src/promotion_ingestion/Dockerfile`
- `shopping-agent` — from `src/shopping_agent/Dockerfile`
- `shopping-simulator` — from `src/shopping_simulations/Dockerfile`
- `pricing-mcp-server` — from `src/pricing_mcp_server/Dockerfile`
- `campaign-agent` — from `src/campaign_agent/Dockerfile`

---

## 3. AI Search Indexes & Knowledge Base

### Create / update all indexes and knowledge base (postprovision hook)
```bash
python -m scripts.deploy_assets
```

### Create / update indexes only
```bash
python -m scripts.create_search_index
```

### Create / update knowledge base only
```bash
python -m scripts.create_knowledgebase
```

### Seed the category taxonomy (one-time, re-runnable)
```bash
python scripts/create_category_items.py
python scripts/create_category_items.py --dry-run   # preview only
```

### Map uncategorized items to best-matching category
```bash
python scripts/map_items_to_category.py
python scripts/map_items_to_category.py --dry-run
python scripts/map_items_to_category.py --threshold 0.85 --batch-size 50
```

### Migrate supplier index to multi-location schema
```bash
python scripts/migrate_supplier_index.py
python scripts/migrate_supplier_index.py --export-only   # export, then inspect
python scripts/migrate_supplier_index.py --import-only   # skip delete, re-import
python scripts/migrate_supplier_index.py --dry-run
```

---

## 4. Flyer Ingestion

### Ingest all PDFs in data/files/ (derives supplier IDs from filenames)
```bash
python scripts/ingest_all.py
python scripts/ingest_all.py --dry-run
python scripts/ingest_all.py --files-dir data/files --output-dir data
```

### Ingest a single supplier's sources
```bash
python -m src.promotion_ingestion.processor \
    --supplier-id <supplier-id> \
    --source https://example.com/weekly-flyer.pdf \
    --source data/local-flyer.pdf \
    --output data/extraction-result.json

# Push directly to AI Search:
python -m src.promotion_ingestion.processor \
    --supplier-id <supplier-id> \
    --source https://example.com/weekly-flyer.pdf \
    --push-to-search
```

Filename → supplier-id convention:
- `StoreOne-Jun8.pdf` → `store-one`
- `StoreTwo-Jun15.pdf` → `store-two`
- `StoreBranch-Jun8.pdf` → `store-branch`

---

## 5. Deploy Core Container Apps

Deploys `shopping-chat`, `promotion-ingestion`, `shopping-agent` via `infra/core/host/app.bicep`.
Uses `TAG` env var (default: `latest`).

```bash
export TAG=20260615120000   # from build_containers.sh output; or omit for :latest
python -m scripts.deploy_agents
```

Required env vars (from `.env`):
- `AZURE_RESOURCE_GROUP`, `AZURE_REGISTRY`, `AZURE_CONTAINER_APPS_ENVIRONMENT_NAME`, `AZURE_IDENTITY_NAME`

---

## 5b. Shopping Simulator Workflow (multi-agent, DevUI)

A **multi-agent workflow** (Microsoft Agent Framework) that simulates one
shopping bill **per supplier in parallel** and recommends the cheapest one- or
two-stop tour. It is served on the **DevUI** from an externally ingressed
Container App and publishes OpenTelemetry traces to Application Insights for use
as a Foundry [external agent](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/register-external-agent).
Source: [src/shopping_simulations](../../../src/shopping_simulations).

Prerequisite: the shopping toolbox is registered (see §8 / harness README):
```bash
python -m scripts.register_shopping_toolbox
```

Build the image in ACR, then deploy the Container App (public DevUI on 8080):
```bash
python -m scripts.deploy_shopping_simulator --build
```

Deploy only (image already in ACR):
```bash
python -m scripts.deploy_shopping_simulator
```

The deploy script also grants the user-assigned managed identity
**Cognitive Services User** (consume Foundry models) and **Monitoring Metrics
Publisher** (publish telemetry to Application Insights). It prints the public
DevUI URL: `https://<fqdn>/`.

Key overrides:
- `SHOPPING_SIM_APP_NAME` — Container App name (default: `shopping-simulator`)
- `SHOPPING_SIM_PORT` — DevUI / container port (default: `8080`)
- `SHOPPING_SIM_EXTERNAL=false` — internal ingress (default: public)
- `SHOPPING_SIM_MAX_SUPPLIERS` — concurrent supplier-bill slots (default: `5`)
- `SHOPPING_TOOLBOX_NAME` — toolbox the agents consume (default: `shopping-tools`)
- `OTEL_AGENT_ID` — `gen_ai.agent.id` for external-agent trace matching
  (default: `shopping-simulator-v1`)

After telemetry flows, register it as a Foundry external agent with
`ExternalAgentDefinition(otel_agent_id="shopping-simulator-v1")` — see
[src/shopping_simulations/README.md](../../../src/shopping_simulations/README.md).

---

## 6. Campaign-Agent Pipeline (three discrete steps)

Run these in order. Each is independently re-runnable.

### Step 1 — Deploy pricing MCP server (internal Container App, port 8091)

```bash
# Build image in ACR first, then deploy:
python -m scripts.deploy_pricing_mcp_server --build

# Deploy only (image already in ACR):
python -m scripts.deploy_pricing_mcp_server

# Deploy a specific tag:
TAG=20260615120000 python -m scripts.deploy_pricing_mcp_server
```

Prints the resulting MCP URL, e.g.:
`https://pricing-mcp-server.<env-default-domain>/mcp`

Key overrides:
- `PRICING_MCP_APP_NAME` — Container App name (default: `pricing-mcp-server`)
- `PRICING_MCP_PORT` — container port (default: `8091`)
- `PRICING_MCP_EXTERNAL=true` — expose public ingress (default: internal)

### Step 2 — Register pricing MCP server as a Foundry toolbox

```bash
python -m scripts.register_pricing_toolbox
```

Derives the MCP URL from the Container App FQDN (`AZURE_RESOURCE_GROUP`), or
override with:
```bash
PRICING_MCP_URL=https://pricing-mcp-server.<env>.azurecontainerapps.io/mcp \
  python -m scripts.register_pricing_toolbox
```

Key overrides:
- `PRICING_TOOLBOX_NAME` — toolbox name (default: `pricing-tools`)
- `PRICING_MCP_URL` — explicit MCP server URL
- `PRICING_MCP_CONNECTION_ID` — Foundry connection ID (optional)

Prints the consumer endpoint:
`{project}/toolboxes/pricing-tools/mcp?api-version=v1`

### Step 3 — Deploy campaign planning agent (Foundry hosted agent)

```bash
python -m scripts.deploy_campaign_agent
```

Key overrides:
- `AZURE_AI_CAMPAIGN_AGENT_NAME` — agent name (default: `campaign-agent`)
- `PRICING_TOOLBOX_NAME` — toolbox the agent consumes (default: `pricing-tools`)
- `TOOLBOX_MCP_ENDPOINT` — explicit toolbox MCP URL (optional override)
- `PRICING_MCP_URL` — bypass toolbox, call MCP server directly (local dev)

---

## 6b. Campaign A365 *Autopilot* Digital Worker

This is a **separate deployment path** from §6. Instead of a RESPONSES-protocol
hosted agent, it publishes the campaign planner as an **Agent 365 autopilot /
digital worker** (activity protocol, bot-relayed, hireable in Microsoft 365).
Source lives in [src/campaign_a365_agent](../../../src/campaign_a365_agent) and
the Python step scripts in [scripts/autopilot](../../../scripts/autopilot).

Prerequisites: `azd up` has run (so `./.env` has `AZURE_AI_PROJECT_ENDPOINT`,
`AZURE_CONTAINER_REGISTRY_ENDPOINT`, `AZURE_RESOURCE_GROUP`, etc.) and you are
logged in with `az login` as **Owner** on the subscription.

### One-shot wrapper (recommended)

Runs the whole pipeline: provision autopilot infra → remote ACR build → create
agent version → publish digital worker → OAuth2 grants → add blueprint owner.

```bash
python -m scripts.deploy_campaign_autopilot
```

Useful flags:
- `--skip-infra --blueprint-id <id>` — reuse an existing blueprint, skip bicep
- `--configure-backend` — also PUT the Teams Developer Portal backend config
  (needs `az login --scope https://dev.teams.microsoft.com/.default`)

### What the wrapper provisions

`provision_infra` runs three things (MAIB creation + role grants are done in
Python, **not** bicep — see note below):
1. **MAIB** — `create_maib.py` PUTs a **Managed Agent Identity Blueprint** via
   the Foundry data-plane API and returns its client id.
2. **Project roles** — `grant_project_roles.py` grants the existing project
   system identity **AcrPull** on the registry + **Cognitive Services User** on
   the account (tolerating assignments `azd` already created).
3. **Bot service** — `infra/autopilot/main.bicep` (resourceGroup scope) deploys
   an **Azure Bot + Teams channel**, `msaAppId` = blueprint client id,
   endpoint = the agent's `activityProtocol` endpoint. References the existing
   account/project — it does not recreate them.

> **Why no deployment script?** The Foundry sample created the MAIB from an
> `AzurePowerShell` deploymentScript, which requires a *key-based* storage
> account. That is blocked by policy in some tenants
> (`KeyBasedAuthenticationNotPermitted`), so MAIB creation was moved into Python.
> `azd` also already grants AcrPull to the project identity, which made the
> bicep role assignment fail with `RoleAssignmentExists` — hence the Python grant.

### Running steps individually

Each step is also runnable standalone (all read `./.env`):

```bash
python -m scripts.autopilot.create_maib                # → AGENT_IDENTITY_BLUEPRINT_ID
python -m scripts.autopilot.grant_project_roles        # AcrPull + Cognitive Services User
python -m scripts.autopilot.provision_infra            # the above two + bot service bicep
AGENT_IDENTITY_BLUEPRINT_ID=<id> python -m scripts.autopilot.build_image
python -m scripts.autopilot.create_agent               # → AGENT_GUID
AGENT_GUID=<g> AGENT_IDENTITY_BLUEPRINT_ID=<id> python -m scripts.autopilot.publish_digital_worker
AGENT_IDENTITY_BLUEPRINT_ID=<id> python -m scripts.autopilot.create_oauth2_grants
AGENT_IDENTITY_BLUEPRINT_ID=<id> python -m scripts.autopilot.add_blueprint_owner
AGENT_IDENTITY_BLUEPRINT_ID=<id> python -m scripts.autopilot.configure_blueprint_backend  # optional
```

Key overrides:
- `AZURE_AI_CAMPAIGN_AGENT_NAME` — agent name (default: `campaign-a365-agent`)
- `MAIB_NAME` — blueprint name (default: `<agent>-maib`)
- `CAMPAIGN_A365_IMAGE_NAME` — image repo (default: `campaign-a365-agent`)
- `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_CHAT_DEPLOYMENT_NAME` — model the agent calls

### After deployment

Approve the blueprint in the Microsoft 365 admin center
(`https://admin.cloud.microsoft/?#/agents/all/requested`), configure it in the
Teams Developer Portal, then create agent instances in Teams.

---

## 7. Deploy All Hosted Agents (shopping + campaign together)

```bash
python -m scripts.deploy_hosted_agents
```

Requires: `AZURE_AI_PROJECT_ENDPOINT`, `AZURE_CONTAINER_REGISTRY_ENDPOINT`

Deploys `shopping-agent` and `campaign-agent` as Foundry hosted agents
(RESPONSES protocol). Builds images in ACR (timestamped + `:latest`).

---

## 8. Promotion Agent Pipeline (two discrete steps)

A lightweight **prompt-based** agent that identifies product promotions and
pricing details.  It reads the live retail-items AI Search index through a
Foundry toolbox — no container image required.

Run these in order. Each is independently re-runnable.

### Step 1 — Register the promotion toolbox (AI Search → Foundry toolbox)

```bash
python -m scripts.register_promotion_toolbox
```

Required env vars:
- `AZURE_AI_PROJECT_ENDPOINT`
- `AZURE_SEARCH_CONNECTION_NAME` — name of the AI Search connection in your
  Foundry project (Foundry → Settings → Connections).

Key overrides:
- `PROMOTION_TOOLBOX_NAME` — toolbox name (default: `promotion-tools`)
- `AZURE_SEARCH_ITEM_INDEX_NAME` — index to search (default: `retail-items`)

Prints the consumer endpoint:
`{project}/toolboxes/promotion-tools/mcp?api-version=v1`

### Step 2 — Deploy the promotion prompt agent

```bash
python -m scripts.deploy_promotion_agent
```

Key overrides:
- `AZURE_AI_PROMOTION_AGENT_NAME` — agent name (default: `promotion-agent`)
- `PROMOTION_TOOLBOX_NAME` — toolbox to connect (default: `promotion-tools`)
- `PROMOTION_TOOLBOX_MCP_URL` — explicit toolbox MCP URL (optional override)
- `PROMOTION_MCP_CONNECTION_ID` — Foundry connection ID (optional)
- `AZURE_AI_MODEL_DEPLOYMENT_NAME` — model (default: `gpt-4.1-mini`)

Prints the A2A card URL and base path:
`{project}/agents/promotion-agent/endpoint/protocols/a2a/agentCard/v0.3`

The agent supports **RESPONSES**, **A2A** and **INVOCATIONS** protocols.

---

## 9. Run Services Locally

<!-- NOTE: sections 9-12 are renumbered; was 8-11 before Promotion Agent Pipeline was added -->

### Pricing MCP server
```bash
python -m src.pricing_mcp_server.server
# serves http://127.0.0.1:8091/mcp
# override: PRICING_MCP_HOST / PRICING_MCP_PORT
```

### Campaign planning agent
```bash
export AZURE_AI_PROJECT_ENDPOINT="https://<project>.services.ai.azure.com/api/projects/<name>"
export AZURE_OPENAI_CHAT_DEPLOYMENT_NAME="gpt-4.1-mini"
export AZURE_SEARCH_ENDPOINT="https://<search>.search.windows.net"
export PRICING_MCP_URL="http://127.0.0.1:8091/mcp"
python -m src.campaign_agent.agent
# serves RESPONSES protocol on PORT (default 8088)
```

### Shopping chat UI
```bash
uvicorn src.shopping_chat.app:app --reload --port 8080
# open http://localhost:8080
```

### Shopping agent (CLI)
```bash
python -m src.shopping_agent.shopping_agent --query "Milch, Hackfleisch, Tomaten"
python -m src.shopping_agent.shopping_agent   # interactive REPL
```

---

## 10. Cleanup

### Delete search index data (keep schemas)
```bash
python scripts/delete_index_data.py
```

### Delete a search index entirely (schema + data)
```bash
python scripts/delete_index.py
```

### Delete all Container Apps
```bash
python scripts/delete_agents.py   # requires AZURE_RESOURCE_GROUP
```

### Delete all Foundry agents
```bash
python scripts/delete_agents.py   # if it covers hosted agents — check script
```

### Tear down all Azure resources
```bash
azd down
```

---

## 11. Environment Variable Reference

All variables are written to `./.env` by `azd up`.

| Variable | Source | Used by |
|---|---|---|
| `AZURE_RESOURCE_GROUP` | azd | all deploy scripts |
| `AZURE_REGISTRY` | azd | build, deploy |
| `AZURE_CONTAINER_APPS_ENVIRONMENT_NAME` | azd | deploy |
| `AZURE_IDENTITY_NAME` | azd | deploy |
| `AZURE_AI_PROJECT_ENDPOINT` | azd | agents, toolbox, ingestion |
| `AZURE_CONTAINER_REGISTRY_ENDPOINT` | azd | hosted agents |
| `AZURE_SEARCH_ENDPOINT` | azd | indexing, ingestion, agents |
| `AZURE_SEARCH_ADMIN_KEY` | azd | indexing (optional, falls back to DefaultAzureCredential) |
| `AZURE_SEARCH_SUPPLIER_INDEX_NAME` | azd | default: `retail-suppliers` |
| `AZURE_SEARCH_CATEGORY_INDEX_NAME` | azd | default: `retail-categories` |
| `AZURE_SEARCH_ITEM_INDEX_NAME` | azd | default: `retail-items` |
| `AZURE_SEARCH_KNOWLEDGE_BASE_NAME` | azd | default: `supply-chain-kb` |
| `AZURE_OPENAI_ENDPOINT` | azd | all AI calls |
| `AZURE_OPENAI_CHAT_DEPLOYMENT_NAME` | azd | default: `gpt-4.1-mini` |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME` | azd | default: `text-embedding-3-small` |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | azd | fallback model |
| `OPENAI_API_VERSION` | azd | default: `2024-05-01-preview` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | azd | telemetry |
| `TAG` | manual | image tag for deploy (default: `latest`) |
| `PRICING_MCP_URL` | manual | direct MCP URL (local dev / override) |
| `PRICING_MCP_APP_NAME` | manual | default: `pricing-mcp-server` |
| `PRICING_MCP_EXTERNAL` | manual | `true` to expose public ingress |
| `PRICING_TOOLBOX_NAME` | manual | default: `pricing-tools` |
| `TOOLBOX_MCP_ENDPOINT` | manual | explicit toolbox MCP URL |
| `AZURE_AI_CAMPAIGN_AGENT_NAME` | manual | default: `campaign-agent` |
| `AZURE_AI_HOSTED_AGENT_NAME` | manual | default: `shopping-agent` |
| `AZURE_SEARCH_CONNECTION_NAME` | manual | Foundry connection name for AI Search (promotion toolbox) |
| `PROMOTION_TOOLBOX_NAME` | manual | default: `promotion-tools` |
| `PROMOTION_TOOLBOX_MCP_URL` | manual | explicit promotion toolbox MCP URL (optional) |
| `PROMOTION_MCP_CONNECTION_ID` | manual | Foundry connection ID for restricted toolbox (optional) |
| `AZURE_AI_PROMOTION_AGENT_NAME` | manual | default: `promotion-agent` |
| `SHOPPING_TOOLBOX_NAME` | manual | default: `shopping-tools` |
| `SHOPPING_SIM_APP_NAME` | manual | simulator Container App name (default: `shopping-simulator`) |
| `SHOPPING_SIM_PORT` | manual | simulator DevUI port (default: `8080`) |
| `SHOPPING_SIM_EXTERNAL` | manual | `true` for public ingress (default: `true`) |
| `SHOPPING_SIM_MAX_SUPPLIERS` | manual | concurrent supplier-bill slots (default: `5`) |
| `OTEL_AGENT_ID` | manual | `gen_ai.agent.id` for external-agent matching (default: `shopping-simulator-v1`) |

---

## 12. Conventions

- **No real retailer brand names** in `src/` or `scripts/`. Use `the retailer`,
  `competitor-a`, `Store A`, `Naturgut Bio`, etc. `data/` input files are exempt.
- Run all scripts from the **repo root** as modules: `python -m scripts.<name>`.
- Scripts read `./.env` via `python-dotenv` — always source it before manual CLI work.
- Image builds use `az acr build` (no local Docker). Both `:<timestamp>` and
  `:latest` tags are pushed on every build.
- Hosted agents speak the **RESPONSES protocol** on port `8088` (campaign) /
  `8090` (shopping).
- The pricing MCP server is **internal by default** (no public ingress). Set
  `PRICING_MCP_EXTERNAL=true` only when Foundry needs to reach it directly.
