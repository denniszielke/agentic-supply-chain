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

- **Supplier** — one per flyer/campaign: store, region, validity window, address
- **Category** — normalised taxonomy: name, tags, embedding
- **Item** — single offer instance: product, pricing, promotion, linked to supplier + category

The vector schema uses `content_vector` with **1536 dimensions** (compatible with `text-embedding-3-small`). Changing embedding models requires a schema update in `infra/search-schema.json`.

Full schema: [`infra/search-schema.json`](infra/search-schema.json)

---

## Repository structure

```
agentic-supply-chain/
├── azure.yaml                    # azd configuration
├── infra/
│   ├── main.bicep                # Azure resource definitions
│   ├── main.parameters.json
│   └── search-schema.json        # Azure AI Search vector index schema
├── src/
│   ├── shared/                   # Pydantic models, planner, seed data
│   ├── shopping_chat/            # MCP app + UI  →  see src/shopping_chat/README.md
│   ├── promotion_ingestion/      # Flyer indexer  →  see src/promotion_ingestion/README.md
│   └── shopping_agent/           # A2A planning agent  →  see src/shopping_agent/README.md
├── scripts/
│   ├── build_containers.sh       # Build (and optionally push) all Docker images
│   ├── create_index.py           # Create / update Azure AI Search index
│   ├── delete_index.py           # Delete Azure AI Search index
│   ├── deploy_agents.py          # Deploy all Container Apps to Azure
│   ├── delete_agents.py          # Delete all Container Apps from Azure
│   └── search_index_pipeline.py  # Lower-level index helper (used by deploy_assets.py)
└── tests/
    ├── test_catalog.py
    └── test_planner.py
```

---

## Deployment

### Prerequisites

- [Azure Developer CLI (azd)](https://aka.ms/azd)
- Python 3.12+
- Docker (for container builds)
- Azure CLI (for post-provision scripts)

### 1. Provision Azure infrastructure

```bash
azd up
```

This provisions all core runtime dependencies from `infra/main.bicep`, including:

- Azure Container Apps environment
- Azure Container Registry (ACR)
- Azure AI Search
- Azure OpenAI account with model deployments (`gpt-4.1-mini`, `text-embedding-3-small`)
- Log Analytics + Application Insights
- Bootstrap Container Apps for `shopping-chat`, `promotion-ingestion`, and `shopping-agent`
- Optional Azure AI Foundry project environment variables for hosted-agent deployment (`AZURE_AI_PROJECT_ENDPOINT`, `AZURE_AI_PROJECT_ID`, `AZURE_AI_PROJECT_NAME`)

### 2. Create the search index

After `azd up`, azd writes all infra outputs to `.azure/<env-name>/.env`.
The project `postdeploy` hook copies this to `./.env`, so variables such as
`AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_INDEX_NAME`, `AZURE_SEARCH_ADMIN_KEY`,
`AZURE_OPENAI_ENDPOINT`, `AZURE_AI_MODEL_DEPLOYMENT_NAME`,
`AZURE_CONTAINER_REGISTRY_ENDPOINT`, `AZURE_REGISTRY`, and
`APPLICATIONINSIGHTS_CONNECTION_STRING` are available automatically.

Then run:

```bash
python scripts/create_index.py
```

### 3. Build and push container images

```bash
export AZURE_REGISTRY="<your-registry>.azurecr.io"
./scripts/build_containers.sh "${AZURE_REGISTRY}" latest
```

### 4. Deploy agents to Azure Container Apps (and optional hosted agents)

```bash
export AZURE_RESOURCE_GROUP="<resource-group>"
export AZURE_REGISTRY="<your-registry>.azurecr.io"
export AZURE_CONTAINER_APPS_ENVIRONMENT_NAME="<container-apps-environment-name>"
python scripts/deploy_agents.py
```

`scripts/deploy_agents.py` is idempotent: it updates existing apps and creates
missing ones directly from agent/container code when needed.

If `AZURE_AI_PROJECT_ENDPOINT` and `AZURE_CONTAINER_REGISTRY_ENDPOINT` are set,
the same command also builds and deploys a hosted agent version from source code
via `scripts/deploy_hosted_agents.py`.

### 5. Ingest a promotional flyer

```bash
python -m src.promotion_ingestion.job \
    --source https://example.com/weekly-flyer.pdf \
    --supplier-id rewe-berlin-week-24 \
    --output data/indexed-items.json
```

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

Delete all Container App agents:

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
