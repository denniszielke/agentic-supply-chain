# agentic-supply-chain

An agentic scenario that solves **supplier optimization for retail shopping**. Weekly promotional flyers from multiple supermarkets (e.g. REWE, ALDI SÜD) are ingested and indexed, then agents and MCP-capable apps help users plan optimal shopping tours across stores.

---

## Components

| Component | Folder | Description |
|---|---|---|
| **shopping_chat** | `src/shopping_chat` | Containerized MCP app + interactive browser UI for product search, recommendations, and supplier inventory |
| **promotion_ingestion** | `src/promotion_ingestion` | Container job that downloads and indexes promotional flyers (PDF, images, websites) into Azure AI Search |
| **shopping_agent** | `src/shopping_agent` | Hosted agent with A2A-style HTTP API for shopping list optimization across current promotions |
| **shared** | `src/shared` | Shared Pydantic data models (`Supplier`, `Category`, `Item`), shopping planner logic, and seed data |
| **infra** | `infra` | Bicep templates for Azure deployment and AI Search vector schema |
| **scripts** | `scripts` | Deployment, index, container build and lifecycle scripts |

---

## Data model

The project normalises all flyer data into three core entities:

- **Supplier** — one per flyer/campaign: store, region, validity window, address, opening hours
- **Category** — normalised semantic grouping: name, slug ID, tags, optional parent, vector embedding
- **Item** — single promotional offer: product name, brand, description, pricing, promotion mechanic, linked to supplier + category

Pydantic models live in [`src/shared/models.py`](src/shared/models.py). The full domain ontology (field descriptions, types, enumerations, relationships) is in [`src/shared/ontology.json`](src/shared/ontology.json) and is used as context for LLM-based extraction.

The AI Search vector fields use **1536 dimensions** (compatible with `text-embedding-3-small`).

---

## Repository structure

```
agentic-supply-chain/
├── azure.yaml                    # azd configuration
├── requirements.txt              # Combined local dev dependencies (all services)
├── infra/
│   ├── main.bicep                # Top-level subscription-scoped deployment
│   ├── main.parameters.json
│   └── core/
│       ├── ai/                   # AI Foundry account, project, connections
│       ├── host/                 # VNet, Container Apps environment, ACR, identity, app
│       ├── monitor/              # Log Analytics, Application Insights
│       └── search/               # Azure AI Search, Bing grounding
├── src/
│   ├── shared/
│   │   ├── models.py             # Pydantic models: Supplier, Category, Item
│   │   ├── ontology.json         # Domain ontology used by the processor
│   │   ├── planner.py            # Shopping planner logic
│   │   └── seed_data.py          # Local dev seed data
│   ├── shopping_chat/            # MCP app + UI  →  see src/shopping_chat/README.md
│   ├── promotion_ingestion/      # Flyer processor  →  see src/promotion_ingestion/README.md
│   └── shopping_agent/           # A2A planning agent  →  see src/shopping_agent/README.md
├── scripts/
│   ├── build_containers.sh       # Build all images via az acr build
│   ├── create_index.py           # Create / update Azure AI Search indexes
│   ├── delete_index.py           # Delete Azure AI Search indexes
│   ├── deploy_agents.py          # Deploy Container Apps via app.bicep
│   ├── delete_agents.py          # Delete all Container Apps
│   └── search_index_pipeline.py  # Azure AI Search index schema definitions
└── tests/
    ├── test_catalog.py
    └── test_planner.py
```

---

## Deployment

### Prerequisites

- [Azure Developer CLI (azd)](https://aka.ms/azd)
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli)
- Python 3.12+

---

## Step 1 — Provision infrastructure

This step creates all long-lived Azure resources: AI Foundry project, Azure AI Search, Container Apps environment, VNet, ACR, and the user-assigned managed identity used by every container app.

```bash
azd up
```

Resources provisioned by `infra/main.bicep`:

| Resource | Naming |
|---|---|
| Resource group | `rg-<AZURE_ENV_NAME>` |
| VNet + subnets | `vnet-rg-<AZURE_ENV_NAME>` |
| Container Apps environment | `cae-<AZURE_ENV_NAME>` |
| Azure Container Registry | discovered from resource group |
| User-assigned managed identity | `id-<AZURE_ENV_NAME>` |
| Azure AI Foundry account + project | `ai-project-<AZURE_ENV_NAME>` |
| Azure AI Search service | `search-<token>` |
| Log Analytics + Application Insights | `logs-<token>` / `appi-<token>` |

After `azd up` completes, azd writes all infra outputs to `.azure/<AZURE_ENV_NAME>/.env`.
The `postdeploy` hook copies this automatically to `./.env`, making the following variables available for subsequent steps:

```
AZURE_RESOURCE_GROUP
AZURE_LOCATION
AZURE_IDENTITY_NAME
AZURE_CONTAINER_APPS_ENVIRONMENT_NAME
AZURE_CONTAINER_APPS_ENVIRONMENT_ID
AZURE_CONTAINER_REGISTRY_ENDPOINT
AZURE_REGISTRY
AZURE_SEARCH_ENDPOINT
AZURE_SEARCH_INDEX_NAME
AZURE_SEARCH_ADMIN_KEY
AZURE_OPENAI_ENDPOINT
AZURE_AI_PROJECT_ENDPOINT
AZURE_AI_PROJECT_ID
AZURE_AI_PROJECT_NAME
AZURE_AI_MODEL_DEPLOYMENT_NAME
AZURE_OPENAI_CHAT_DEPLOYMENT_NAME
AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME
OPENAI_API_VERSION
APPLICATIONINSIGHTS_CONNECTION_STRING
```

### 1a. Create the search index

```bash
python scripts/create_index.py
```

---

## Step 2 — Build containers and deploy

This step builds the container images in ACR and deploys each service as a Container App. It can be repeated independently whenever source code changes — no re-provisioning required.

### 2a. Build container images

Uses `az acr build` to build directly in the cloud — no local Docker required.

```bash
# Reads AZURE_ENV_NAME to locate the ACR in rg-<AZURE_ENV_NAME>
./scripts/build_containers.sh "${AZURE_ENV_NAME}"
```

The script prints the registry name and image tag on completion, e.g.:

```
Registry: myregistry.azurecr.io, Tag: 0608120000
```

Set `TAG` to that value before deploying:

```bash
export TAG=0608120000
```

### 2b. Deploy Container Apps

Deploys (or re-deploys) `shopping-chat`, `promotion-ingestion`, and `shopping-agent` using `infra/core/host/app.bicep` via `az deployment group create`. Each app is assigned the shared user-managed identity for ACR pull access.

```bash
# All variables are sourced from .env (written by azd) — set TAG explicitly
export TAG=0608120000

python scripts/deploy_agents.py
```

Required variables (all populated automatically from `.env` after `azd up`):

| Variable | Description |
|---|---|
| `AZURE_RESOURCE_GROUP` | Target resource group |
| `AZURE_REGISTRY` | ACR login server, e.g. `myregistry.azurecr.io` |
| `AZURE_CONTAINER_APPS_ENVIRONMENT_NAME` | Container Apps environment name |
| `AZURE_IDENTITY_NAME` | User-assigned managed identity name |
| `TAG` | Image tag to deploy |

If `AZURE_AI_PROJECT_ENDPOINT` and `AZURE_CONTAINER_REGISTRY_ENDPOINT` are also set, the script additionally deploys a hosted agent via `scripts/deploy_hosted_agents.py`.

### 2c. Ingest a promotional flyer

Runs the vision-model extraction pipeline against one or more PDF/image sources and writes the result to a JSON file:

```bash
python -m src.promotion_ingestion.processor \
    --supplier-id rewe-berlin-week-24 \
    --source https://example.com/weekly-flyer.pdf \
    --source data/local-flyer.pdf \
    --output data/extraction-result.json
```

Key env vars for this step:

| Variable | Description |
|---|---|
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint |
| `AZURE_OPENAI_CHAT_DEPLOYMENT_NAME` | Vision model deployment (default: `gpt-4o`) |
| `PROCESSING_WORK_DIR` | Where page images are stored (default: `/tmp/agentic-supply-chain`) |
| `PROCESSING_BATCH_SIZE` | Images per batch (default: `8`) |
| `PROCESSING_OVERLAP` | Sliding-window overlap (default: `2`) |

---

## Running services locally

**MCP app + UI:**

```bash
uvicorn src.shopping_chat.app:app --reload --port 8080
```

Open http://localhost:8080

**Shopping planner agent (A2A):**

```bash
uvicorn src.shopping_agent.a2a_api:app --reload --port 8090
```

---

## Cleanup

Delete search index:

```bash
python scripts/delete_index.py
```

Delete all Container Apps:

```bash
export AZURE_RESOURCE_GROUP="<resource-group>"
python scripts/delete_agents.py
```

Tear down all Azure resources:

```bash
azd down
```

---

## Tests

```bash
python -m unittest discover -s tests -v
```

---

## Component READMEs

- [`src/shopping_chat/README.md`](src/shopping_chat/README.md)
- [`src/promotion_ingestion/README.md`](src/promotion_ingestion/README.md)
- [`src/shopping_agent/README.md`](src/shopping_agent/README.md)
