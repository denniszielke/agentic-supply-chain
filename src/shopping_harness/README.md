# Shopping Harness Agent

A consumer-side shopping assistant that helps a shopper decide **what to buy,
where, and at what price** — grounding every answer in live retail data through
a single **Foundry toolbox**.

Built with the **[agent-framework](https://github.com/microsoft/agent-framework)**
and hosted in **Azure AI Foundry** as a **hosted agent**: it is served over the
RESPONSES protocol by `ResponsesHostServer` (the "harness") and routes model
calls through the Foundry project gateway using Entra ID (no API keys).

The design combines two references:

- Foundry toolbox over MCP from the agent-framework SDK
  ([trip-scout/agent.py](https://github.com/denniszielke/msft-foundry-hosted-agents-sample/blob/main/src/agents/trip-scout/agent.py)).
- The managed hosted-agent harness shape
  ([maf_harness_managed_hosted_agent](https://github.com/microsoft/Agent-Framework-Samples/tree/main/09.Cases/maf_harness_managed_hosted_agent)).

## How it thinks

The agent reaches **all** retail data through one Foundry toolbox
(`shopping-tools`) that exposes three search tools, one per Azure AI Search
index:

| Tool | Index | Answers |
| --- | --- | --- |
| `supplier-search` | `retail-suppliers` | *where* — stores, locations, branches |
| `category-search` | `retail-categories` | *what kind* — the product taxonomy and alternatives |
| `item-search` | `retail-items` | *what & how much* — products, prices, discounts, validity |

The toolbox is **not** wired point-to-point. It is published as a **Foundry
toolbox** and the agent connects to it through the project's toolbox MCP
endpoint (`{project}/toolboxes/{toolbox}/mcp?api-version=v1`), authenticated with
a fresh Entra bearer token per request. This keeps the search tools published,
discovered and governed centrally. For local development, set `SHOPPING_MCP_URL`
to bypass the toolbox and connect to a locally running MCP server directly.

## Core principle

Always **search before answering**: never invent suppliers, categories, prices
or discounts. Resolve ambiguous items through `category-search`, then look up
concrete products with `item-search`, and prefer the best total value (price,
discount depth, unit price and offer validity together).

## Run locally

Start the hosted agent server from the project root. It consumes the Foundry
toolbox by default; point it at a local MCP server with `SHOPPING_MCP_URL` to
skip the toolbox:

```bash
export AZURE_AI_PROJECT_ENDPOINT="https://<project>.services.ai.azure.com/api/projects/<name>"
export AZURE_OPENAI_CHAT_DEPLOYMENT_NAME="gpt-4.1-mini"
# optional, for local dev against a directly reachable MCP server:
# export SHOPPING_MCP_URL="http://127.0.0.1:8092/mcp"
python -m src.shopping_harness.agent
```

The agent listens on `PORT` (default `8088`) and speaks the RESPONSES protocol.
Send requests with any Foundry / agent-framework RESPONSES client:

```bash
curl -sS -H "Content-Type: application/json" -X POST http://localhost:8088/responses \
  -d '{"input":"Where can I buy Hackfleisch on offer this week, and at what price?","stream":false}'
```

## Deploy to Foundry

Deploy the pipeline as two discrete, independently re-runnable steps:

1. Register the shopping search toolbox (`shopping-tools`). It wraps the three
   retail AI Search indexes through the project's AI Search connection:

   ```bash
   python -m scripts.register_shopping_toolbox
   ```

   Required: `AZURE_AI_PROJECT_ENDPOINT`, `AZURE_SEARCH_CONNECTION_NAME`
   (the AI Search connection name from Foundry → Settings → Connections).

   Prints the consumer endpoint:
   `{project}/toolboxes/shopping-tools/mcp?api-version=v1`

2. Build and deploy this agent as a Foundry hosted agent:

   ```bash
   python -m scripts.deploy_shopping_harness
   ```

   This builds the image from [`Dockerfile`](./Dockerfile), creates a hosted
   agent version (RESPONSES protocol) and enables the RESPONSES / A2A /
   INVOCATIONS endpoints. The deployed agent reads `SHOPPING_TOOLBOX_NAME` (and
   no `SHOPPING_MCP_URL`), so it consumes the search tools through the toolbox.

## Environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `AZURE_AI_PROJECT_ENDPOINT` | Foundry project endpoint | _required_ |
| `AZURE_CONTAINER_REGISTRY_ENDPOINT` | ACR login server (deploy) | _required (deploy)_ |
| `AZURE_OPENAI_CHAT_DEPLOYMENT_NAME` | Chat model deployment | `gpt-4.1-mini` |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Fallback model deployment | `gpt-4.1-mini` |
| `AZURE_SEARCH_CONNECTION_NAME` | Foundry AI Search connection (toolbox registration) | _required (register)_ |
| `AZURE_SEARCH_SUPPLIER_INDEX_NAME` | Supplier index | `retail-suppliers` |
| `AZURE_SEARCH_CATEGORY_INDEX_NAME` | Category index | `retail-categories` |
| `AZURE_SEARCH_ITEM_INDEX_NAME` | Item index | `retail-items` |
| `SHOPPING_TOOLBOX_NAME` | Foundry toolbox the agent consumes | `shopping-tools` |
| `TOOLBOX_MCP_ENDPOINT` | Explicit toolbox MCP URL (overrides derived) | _optional_ |
| `SHOPPING_MCP_URL` | Direct MCP URL for local dev (bypasses toolbox) | _optional_ |
| `AZURE_AI_SHOPPING_HARNESS_AGENT_NAME` | Hosted agent name | `shopping-harness` |
| `PORT` | Hosted agent server port | `8088` |
