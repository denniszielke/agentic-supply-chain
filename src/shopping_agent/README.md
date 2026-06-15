# shopping_agent — Shopping Tour Agent (AG-UI web app)

## Overview

`shopping_agent` is a containerised **AG-UI web application** that plans an optimised weekly shopping tour. A single FastAPI container hosts both the [AG-UI](https://github.com/microsoft/agent-framework/tree/main/python/packages/ag-ui) protocol endpoint and an attractive, streaming chat UI.

The agent uses the `agent-framework` library with three Azure AI Search context providers to search promotions, compare prices across retailers, and minimise the number of store stops. It keeps three live sidebar panels in sync through AG-UI **shared state** (pushed by the `update_plan` tool):

- **Shopping List** — items and their match status (planned / matched / unavailable / upcoming / non-food)
- **Selected Suppliers** — the ≤ 2 stores chosen for the tour
- **Bill Projection** — projected total, number of stops, and savings

### Scenarios

1. Plan a cost-optimised tour for a shopping list while minimising stops.
2. Future outlook — surface upcoming discounts and wait-or-buy advice.
3. Promotion statistics — categories contested by multiple suppliers and items currently not promoted.
4. Unusual non-food highlights — toys, garden tools, clothing, electronics.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Streaming chat web UI |
| `POST` | `/agent` | AG-UI protocol endpoint (Server-Sent Events) |
| `GET` | `/healthz` | Liveness probe |

### Context providers

| Provider | Mode | Source | Purpose |
|---|---|---|---|
| `kb_promotions` | `agentic` (medium effort, extractive_data) | `supply-chain-kb` knowledge base | Multi-hop reasoning across all three indexes for shopping plan creation |
| `semantic_items` | `semantic` (hybrid) | `retail-items` index | Fast cross-retailer product price comparison |
| `semantic_categories` | `semantic` (hybrid) | `retail-categories` index | Category resolution and alternative product suggestions |

A2A protocol is provided natively by Azure AI Foundry — no custom HTTP server required.

---

## Running locally

Make sure `.env` is populated (see [Environment variables](#environment-variables)), then:

```bash
# from repository root — start the AG-UI web app on http://localhost:8090
python -m src.shopping_agent.server
```

Open <http://localhost:8090> and chat with the agent. The sidebar updates live as the plan evolves.

A non-interactive CLI is still available for quick checks:

```bash
python -m src.shopping_agent.shopping_agent --query "Ich brauche Milch, Hackfleisch und Tomaten."
```

---

## Container build & deploy

The shared build script builds all images and can deploy the `shopping-agent` container app, wiring every required environment variable (including the managed-identity `AZURE_CLIENT_ID`):

```bash
# build images only
./scripts/build_containers.sh "${AZURE_ENV_NAME}" latest

# build, then deploy the shopping-agent container app
./scripts/build_containers.sh "${AZURE_ENV_NAME}" latest --deploy
```

Deployment uses `infra/core/host/app.bicep`, exposes the app externally on port `8090`, and prints the public URL. The `azd up` / `azd deploy` postdeploy hook runs the same command with `--deploy`.

To build/run the container directly:

```bash
docker build -t shopping-agent -f src/shopping_agent/Dockerfile .
docker run --env-file .env -p 8090:8090 shopping-agent
```

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `AZURE_AI_PROJECT_ENDPOINT` | ✅ | — | Azure AI Foundry project endpoint |
| `AZURE_SEARCH_ENDPOINT` | ✅ | — | Azure AI Search service endpoint |
| `AZURE_SEARCH_ADMIN_KEY` | | DefaultAzureCredential | Search admin API key |
| `AZURE_SEARCH_KNOWLEDGE_BASE_NAME` | | `supply-chain-kb` | Agentic retrieval knowledge base |
| `AZURE_SEARCH_ITEM_INDEX_NAME` | | `retail-items` | Retail items / promotions index |
| `AZURE_SEARCH_CATEGORY_INDEX_NAME` | | `retail-categories` | Product category taxonomy index |
| `AZURE_OPENAI_CHAT_DEPLOYMENT_NAME` | | `gpt-4.1-mini` | Chat model deployment |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | | `gpt-4.1-mini` | Fallback model name |
| `AZURE_OPENAI_ENDPOINT` | | — | Azure OpenAI endpoint (required for hybrid embedding search) |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME` | | — | Embedding model for hybrid semantic search |
| `AZURE_CLIENT_ID` | | — | Client id of the user-assigned managed identity (set automatically on deploy) |
| `HOST` | | `0.0.0.0` | Bind address for the web server |
| `PORT` | | `8090` | Listen port for the web server |

---

## Key files

| File | Purpose |
|---|---|
| `server.py` | FastAPI AG-UI endpoint, shared-state `update_plan` tool, serves the web UI |
| `templates/index.html` | Streaming chat UI with live shopping list / suppliers / bill panels |
| `shopping_agent.py` | Agent definition, context providers, CLI entry point |
| `Dockerfile` | Container image definition (runs the AG-UI server) |
| `requirements.txt` | Python dependencies |
