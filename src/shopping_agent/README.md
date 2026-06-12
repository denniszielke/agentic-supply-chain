# shopping_agent — Shopping Tour Agent

## Overview

`shopping_agent` is a hosted **Azure AI Foundry agent** that plans an optimised weekly shopping tour. It uses the `agent-framework` library with three Azure AI Search context providers to search promotions, compare prices across retailers, and minimise the number of store stops.

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
# from repository root — interactive REPL
python -m src.shopping_agent.shopping_agent

# single query
python -m src.shopping_agent.shopping_agent --query "Ich brauche Milch, Hackfleisch und Tomaten."
```

The agent streams its response to stdout and exits after printing the plan.

---

## Container build

```bash
docker build -t shopping-agent -f src/shopping_agent/Dockerfile .
docker run --env-file .env shopping-agent \
  --query "Ich brauche Milch, Hackfleisch und Tomaten."
```

Or use the shared build script:

```bash
./scripts/build_containers.sh "${AZURE_ENV_NAME}"
```

---

## Deploy as hosted Foundry agent

```bash
python scripts/deploy_hosted_agents.py
```

This builds and pushes the container image to ACR, registers the agent version on Azure AI Foundry, and enables A2A / Responses / Invocations protocols.

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

---

## Key files

| File | Purpose |
|---|---|
| `shopping_agent.py` | Agent definition, context providers, interactive entry point |
| `Dockerfile` | Container image definition |
| `requirements.txt` | Python dependencies |
