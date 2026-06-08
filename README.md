# agentic-supply-chain

Agentic supermarket supply-chain scenario with:

- **MCP app + UI** for product search, recommendations, and supplier inventory
- **Vector-enabled Azure AI Search model** for Supplier / Category / Item
- **Flyer indexing job** (images, PDFs, websites)
- **Hosted shopping planner agent** with A2A-style HTTP capability
- **`azd` infrastructure deployment** and Python asset deployment scripts

## Repository structure

- `/infra` – Bicep templates and search schema
- `/src` – Python services (MCP app, indexer job, shopping agent)
- `/tools` – deployment and index automation scripts

## Data model

The implementation follows a normalized model:

- `Supplier`: flyer/store/timeframe context
- `Category`: normalized taxonomy and semantic grouping
- `Item`: concrete offer instance linked to supplier + category

See `/src/shared/models.py` and `/infra/search-schema.json`.

## Quickstart

### 1) Provision infrastructure with azd

```bash
azd up
```

### 2) Deploy search assets (index)

```bash
python /tmp/workspace/denniszielke/agentic-supply-chain/tools/deploy_assets.py
```

### 3) Run services locally

MCP app + UI:

```bash
uvicorn src.mcp_app.app:app --reload --port 8080
```

Indexer job:

```bash
python -m src.indexer.job --source https://example.org/flyer.pdf --supplier-id rewe-week-24
```

Shopping planner agent (A2A endpoint):

```bash
uvicorn src.agent.a2a_api:app --reload --port 8090
```

## Tests

```bash
python -m unittest discover -s tests -v
```
