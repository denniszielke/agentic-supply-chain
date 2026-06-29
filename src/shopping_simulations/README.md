# Shopping Simulator Workflow

A **multi-agent workflow** that, given a shopping ask (a list of products or
product categories), simulates the best possible shopping bill **per supplier in
parallel** and recommends the cheapest one- or two-stop shopping tour.

Built with the **[Microsoft Agent Framework](https://github.com/microsoft/agent-framework)**
workflow engine, served on the **[DevUI](https://learn.microsoft.com/en-us/agent-framework/devui/?pivots=programming-language-python)**
from an Azure **Container App** (public ingress), with OpenTelemetry traces
published to **Application Insights** for use as a Foundry
[external agent](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/register-external-agent).

## How it works

```
selector  →  (supplier proposal × N, in parallel)  →  aggregator
```

| Stage | Executor | Responsibility |
| --- | --- | --- |
| 1 | `supplier-selector` | Searches suppliers and picks the most relevant ones for the ask. |
| 2 | `supplier-proposals` | Runs **one agent per supplier in parallel**, each building the cheapest bill from that supplier only — proposing alternatives for missing items and favouring attractive promotions. |
| 3 | `aggregator` | Picks the single supplier that covers all items cheapest, or — when none does — the best **two-stop** tour, assigning each item to the stop with the best discount. |

All retail data is grounded through the Foundry **shopping toolbox**
(`shopping-tools`, registered by `scripts/register_shopping_toolbox.py`):
`supplier-search`, `category-search` and `item-search`. Models are served by the
Foundry project gateway via Entra ID (managed identity) — no API keys. For local
development, set `SHOPPING_MCP_URL` to bypass the toolbox.

## Run locally

```bash
export AZURE_AI_PROJECT_ENDPOINT="https://<project>.services.ai.azure.com/api/projects/<name>"
export AZURE_OPENAI_CHAT_DEPLOYMENT_NAME="gpt-4.1-mini"
# optional, telemetry to App Insights:
# export APPLICATIONINSIGHTS_CONNECTION_STRING="InstrumentationKey=...;..."
# optional, local MCP server instead of the toolbox:
# export SHOPPING_MCP_URL="http://127.0.0.1:8092/mcp"
python -m src.shopping_simulations.server
```

Open the DevUI at <http://localhost:8080> and submit a shopping list, e.g.
`Milch, Hackfleisch, Tomaten, Kaffee`.

> The server binds to loopback (`127.0.0.1`) by default, so the DevUI runs
> without auth locally. On a non-loopback bind (e.g. the Container App, where
> `HOST=0.0.0.0`) DevUI **requires bearer auth**: set `DEVUI_AUTH_TOKEN` to pin a
> token, or one is auto-generated and printed to the logs. Call the API with
> `Authorization: Bearer <token>`.

## Deploy to a Container App

Prerequisite: the shopping toolbox is registered (run
`python -m scripts.register_shopping_toolbox` once — see the harness README).

```bash
# build the image in ACR, then deploy:
python -m scripts.deploy_shopping_simulator --build

# deploy only (image already in ACR):
python -m scripts.deploy_shopping_simulator
```

The deploy script:

- builds the image from [`Dockerfile`](./Dockerfile) and deploys an
  **externally ingressed** Container App serving the DevUI on port `8080`;
- wires `AZURE_AI_PROJECT_ENDPOINT`, `APPLICATIONINSIGHTS_CONNECTION_STRING`,
  `SHOPPING_TOOLBOX_NAME` and the identity client id into the container;
- grants the user-assigned managed identity **Cognitive Services User** (to
  consume Foundry models) and **Monitoring Metrics Publisher** (to publish
  telemetry to Application Insights).

It prints the public DevUI URL: `https://<fqdn>/`.

## Register as a Foundry external agent

Once telemetry is flowing to Application Insights, register the workflow so its
traces appear in the Foundry portal (matched by `gen_ai.agent.id`):

```python
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import ExternalAgentDefinition
from azure.identity import DefaultAzureCredential

project = AIProjectClient(
    endpoint="<AZURE_AI_PROJECT_ENDPOINT>",
    credential=DefaultAzureCredential(),
    allow_preview=True,
)
project.agents.create_version(
    agent_name="shopping-simulator",
    description="Multi-agent shopping simulator workflow (external).",
    definition=ExternalAgentDefinition(otel_agent_id="shopping-simulator-v1"),
)
```

The container stamps every span with `gen_ai.agent.id = OTEL_AGENT_ID` (default
`shopping-simulator-v1`), which must match the registration's `otel_agent_id`.

## Environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `AZURE_AI_PROJECT_ENDPOINT` | Foundry project endpoint | _required_ |
| `AZURE_OPENAI_CHAT_DEPLOYMENT_NAME` | Chat model deployment | `gpt-4.1-mini` |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Fallback model deployment | `gpt-4.1-mini` |
| `SHOPPING_TOOLBOX_NAME` | Foundry toolbox the agents consume | `shopping-tools` |
| `TOOLBOX_MCP_ENDPOINT` | Explicit toolbox MCP URL (overrides derived) | _optional_ |
| `SHOPPING_MCP_URL` | Direct MCP URL for local dev (bypasses toolbox) | _optional_ |
| `SHOPPING_SIM_MAX_SUPPLIERS` | Concurrent supplier-bill slots | `5` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Telemetry sink (App Insights) | _optional_ |
| `AGENT_NAME` | Telemetry agent name | `shopping-simulator` |
| `OTEL_AGENT_ID` | `gen_ai.agent.id` stamped on spans | `shopping-simulator-v1` |
| `SHOPPING_SIM_APP_NAME` | Container App name (deploy) | `shopping-simulator` |
| `SHOPPING_SIM_EXTERNAL` | `true` for public ingress (deploy) | `true` |
| `DEVUI_AUTH_TOKEN` | Bearer token required on non-loopback binds (auto-generated if unset) | _optional_ |
| `HOST` | DevUI bind host (`0.0.0.0` in the container) | `127.0.0.1` |
| `PORT` | DevUI port | `8080` |
