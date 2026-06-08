# shopping_chat — MCP App & Interactive UI

## Overview

`shopping_chat` is a FastAPI container that exposes **Model Context Protocol (MCP)** capabilities and an interactive browser-based UI for querying supermarket offers.

### MCP capabilities

| Endpoint | Method | Description |
|---|---|---|
| `/mcp/search` | POST | Full-text product search by description |
| `/mcp/recommend` | POST | Top-N recommendations within a category |
| `/mcp/inventory` | POST | All current offers for a given supplier |
| `/mcp/suppliers` | GET | List all known suppliers with metadata |

The `/` route serves an interactive HTML UI that lets users call all MCP endpoints without writing code.

## Running locally

```bash
# from repository root
uvicorn src.shopping_chat.app:app --reload --port 8080
```

Open http://localhost:8080 in your browser.

## Container build

```bash
docker build -t shopping-chat -f src/shopping_chat/Dockerfile .
docker run -p 8080:8080 shopping-chat
```

Or use the shared build script:

```bash
./scripts/build_containers.sh
```

## Environment variables

None required for local seed data. When connecting to Azure AI Search, set:

| Variable | Description |
|---|---|
| `AZURE_SEARCH_ENDPOINT` | Azure AI Search endpoint URL |
| `AZURE_SEARCH_ADMIN_KEY` | Admin API key |

## Key files

| File | Purpose |
|---|---|
| `app.py` | FastAPI application entry point, MCP routes |
| `catalog.py` | `CatalogService` — in-memory search, recommend, inventory logic |
| `templates/index.html` | Browser UI |
| `Dockerfile` | Container image definition |
| `requirements.txt` | Python dependencies |
