# shopping_agent — Shopping Planner Agent (A2A)

## Overview

`shopping_agent` is a hosted FastAPI service that exposes an **Agent-to-Agent (A2A)** HTTP interface. Given a shopping list it searches the current promotions and prices across all indexed suppliers to produce an optimised shopping plan.

### A2A capabilities

| Endpoint | Method | Description |
|---|---|---|
| `/a2a/capabilities` | GET | Describe the agent's available capabilities |
| `/a2a/plan` | POST | Receive a shopping list and return an optimised plan |

#### Example request — `/a2a/plan`

```json
{
  "shopping_list": [
    { "product": "avocado", "quantity": 3 },
    { "product": "milk",    "quantity": 2 }
  ]
}
```

#### Example response

```json
{
  "total_cost": 5.77,
  "lines": [
    {
      "product": "avocado",
      "supplier_id": "rewe-berlin-week-24",
      "item_id": "i-1",
      "quantity": 3,
      "unit_price": 1.29,
      "estimated_cost": 3.87
    },
    {
      "product": "milk",
      "supplier_id": "aldi-berlin-week-24",
      "item_id": "i-2",
      "quantity": 2,
      "unit_price": 0.95,
      "estimated_cost": 1.90
    }
  ]
}
```

## Running locally

```bash
# from repository root
uvicorn src.shopping_agent.a2a_api:app --reload --port 8090
```

## Container build

```bash
docker build -t shopping-agent -f src/shopping_agent/Dockerfile .
docker run -p 8090:8090 shopping-agent
```

Or use the shared build script:

```bash
./scripts/build_containers.sh "${AZURE_ENV_NAME}"
```

## Environment variables

| Variable | Description |
|---|---|
| `AZURE_SEARCH_ENDPOINT` | Azure AI Search endpoint (optional — when pulling live catalog) |
| `AZURE_SEARCH_ADMIN_KEY` | Admin API key |

## Key files

| File | Purpose |
|---|---|
| `a2a_api.py` | FastAPI application, A2A HTTP routes |
| `shopping_agent.py` | `ShoppingPlannerAgent` class |
| `Dockerfile` | Container image definition |
| `requirements.txt` | Python dependencies |
