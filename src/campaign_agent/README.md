# Campaign Planning Agent

A retailer-side planning agent that decides **what to promote, at what price,
for which shopper** — balancing competitive pressure against internal margin.

Built with the **[agent-framework](https://github.com/microsoft/agent-framework)**
and hosted in **Azure AI Foundry** as a **hosted agent**: it is served over the
RESPONSES protocol by `ResponsesHostServer` and routes model calls through the
Foundry project gateway using Entra ID (no API keys).

## How it thinks

The agent grounds every recommendation in two evidence sources:

| Source | Tool(s) | Boundary |
| --- | --- | --- |
| **Competitor promotions** | `search_competitor_promotions` | Public — the same `retail-items` Azure AI Search index that powers the consumer shopping agent |
| **Internal pricing** | Pricing MCP tools (`get_product_pricing`, `simulate_price_change`, …) | Confidential — reachable through the [Pricing MCP server](../pricing_mcp_server/), consumed via a **Foundry toolbox** |

The pricing MCP server is **not** wired point-to-point. It is published as a
**Foundry toolbox** (`pricing-tools`) and the agent connects to it through the
project's toolbox MCP endpoint
(`{project}/toolboxes/{toolbox}/mcp?api-version=v1`), authenticated with a fresh
Entra bearer token per request. This keeps the confidential server published,
discovered and governed centrally. For local development, set `PRICING_MCP_URL`
to bypass the toolbox and connect to a locally running MCP server directly.

Business reasoning is framed by the system prompt around three capabilities:

- **campaign-planning** — design margin-aware promotional campaigns and flyers
- **portfolio-analysis** — assess category/assortment performance and headroom
- **pricing-optimization** — find the margin-maximising price for a product/category

## Core principle

Optimise **weekly gross margin** (`unit_margin × forecast_volume`) — never the
headline discount and never unit margin alone. Procurement cost is treated as
confidential: it informs the reasoning but is never echoed into output.

## Run locally

1. Start the pricing MCP server (separate terminal):

   ```bash
   python -m src.pricing_mcp_server.server
   ```

2. Start the hosted agent server from the project root. Point it at the local
   MCP server with `PRICING_MCP_URL` so it skips the Foundry toolbox:

   ```bash
   export AZURE_AI_PROJECT_ENDPOINT="https://<project>.services.ai.azure.com/api/projects/<name>"
   export AZURE_OPENAI_CHAT_DEPLOYMENT_NAME="gpt-4.1-mini"
   export AZURE_SEARCH_ENDPOINT="https://<search>.search.windows.net"
   export PRICING_MCP_URL="http://127.0.0.1:8091/mcp"
   python -m src.campaign_agent.agent
   ```

   The agent listens on `PORT` (default `8088`) and speaks the RESPONSES
   protocol. Send requests with any Foundry / agent-framework RESPONSES client.

## Deploy to Foundry

Deploy the pipeline as three discrete, independently re-runnable steps:

1. Deploy the pricing MCP server as an (internal) Container App:

   ```bash
   python -m scripts.deploy_pricing_mcp_server
   # prints the MCP URL, e.g. https://pricing-mcp-server.<env>.azurecontainerapps.io/mcp
   ```

2. Register the pricing MCP server as a Foundry toolbox (`pricing-tools`). The
   URL is derived from the Container App FQDN (`AZURE_RESOURCE_GROUP`); set
   `PRICING_MCP_URL` to override it:

   ```bash
   python -m scripts.register_pricing_toolbox
   ```

3. Build and deploy this agent as a Foundry hosted agent:

   ```bash
   python -m scripts.deploy_campaign_agent
   ```

   This builds the image from [`Dockerfile`](./Dockerfile), creates a hosted
   agent version (RESPONSES protocol) and enables the RESPONSES / A2A /
   INVOCATIONS endpoints. The deployed agent reads `PRICING_TOOLBOX_NAME` (and
   no `PRICING_MCP_URL`), so it consumes pricing through the toolbox.

   To deploy the shopping and campaign agents together instead, use
   `python -m scripts.deploy_hosted_agents`.

## Environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `AZURE_AI_PROJECT_ENDPOINT` | Foundry project endpoint | _required_ |
| `AZURE_OPENAI_CHAT_DEPLOYMENT_NAME` | Chat model deployment | `gpt-4.1-mini` |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Fallback model deployment | `gpt-4.1-mini` |
| `AZURE_SEARCH_ENDPOINT` | Competitor promotion index | _required_ |
| `AZURE_SEARCH_ADMIN_KEY` | Search key (else `DefaultAzureCredential`) | _optional_ |
| `AZURE_SEARCH_ITEM_INDEX_NAME` | Item index name | `retail-items` |
| `PRICING_TOOLBOX_NAME` | Foundry toolbox wrapping the pricing MCP server | `pricing-tools` |
| `TOOLBOX_MCP_ENDPOINT` | Explicit toolbox MCP URL (overrides derived) | _optional_ |
| `PRICING_MCP_URL` | Direct pricing MCP URL for local dev (bypasses toolbox) | _optional_ |
| `PORT` | Hosted agent server port | `8088` |

## Register with Agent 365 (observability)

Foundry hosted agents export OpenTelemetry traces to the
[Agent 365](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/agent-365-integration)
control plane.  For this to work the agent's **managed identity** must hold the
`Agent365.Observability.OtelWrite` app role.  Without it you will see HTTP 403
errors from the `microsoft.opentelemetry.a365` exporter at runtime.

### Prerequisites

- Azure CLI (`az`) logged in with at least **Application Administrator** or
  **Global Administrator** role.
- The agent is deployed and its managed-identity service-principal Object ID is
  known (printed by `deploy_campaign_agent.py`, or look it up with
  `az ml online-endpoint show`).
- Your tenant has a valid **Microsoft 365 Copilot** license and Agent 365 has
  been enabled by a Global Administrator in the
  [Microsoft 365 admin center](https://admin.microsoft.com/).
- Register the `Microsoft.BotService` resource provider if not already done:

  ```bash
  az provider register --namespace Microsoft.BotService --wait
  az provider show --namespace Microsoft.BotService --query registrationState
  ```

### Steps

1. **Find the `Agent365Observability` service-principal ID** in your tenant:

   ```bash
   az rest --method GET \
     --uri "https://graph.microsoft.com/v1.0/servicePrincipals?\$filter=displayName eq 'Agent365Observability'" \
     --query "value[0].id" -o tsv
   ```

   Save this value as `<AGENT365_SP_ID>`.

2. **Get the agent's managed-identity Object ID** (the `principalId`):

   ```bash
   # If deployed via deploy_campaign_agent.py the principal ID is printed on deploy.
   # Otherwise look it up:
   az ml online-endpoint show \
     --name campaign-planner \
     --resource-group <AZURE_RESOURCE_GROUP> \
     --workspace-name <FOUNDRY_PROJECT_NAME> \
     --query identity.principalId -o tsv
   ```

   Save this as `<AGENT_PRINCIPAL_ID>`.

3. **Assign the `OtelWrite` app role** (fixed role GUID shown below):

   ```bash
   az rest --method POST \
     --uri "https://graph.microsoft.com/v1.0/servicePrincipals/<AGENT_PRINCIPAL_ID>/appRoleAssignments" \
     --body '{
       "principalId": "e3d914ac-0e53-4e9f-95d0-10b2139b2d29",
       "resourceId": "bc541349-5c7a-4f6a-90b5-64257db03675",
       "appRoleId": "8f71190c-00c8-461d-a63b-f74abde9ba52"
     }'

az rest --method POST --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$AGENT_PRINCIPAL_ID/appRoleAssignments" --headers "Content-Type=application/json" --body "{"principalId":"$AGENT_PRINCIPAL_ID","resourceId":"$A365_SP_ID","appRoleId":"$OTEL_WRITE_ROLE_ID"}"

   ```

   

4. **Verify** the assignment was created:

   ```bash
   az rest --method GET \
     --uri "https://graph.microsoft.com/v1.0/servicePrincipals/<AGENT_PRINCIPAL_ID>/appRoleAssignments" \
     --query "value[?appRoleId=='8f71190c-00c8-461d-a63b-f74abde9ba52']"
   ```

   You should see one entry with `"principalDisplayName"` matching the agent's
   managed identity.  After a few minutes the exporter stops logging 403 errors
   and telemetry begins flowing into Agent 365.
