# Campaign Planning Agent

A retailer-side planning agent that decides **what to promote, at what price,
for which shopper** — balancing competitive pressure against internal margin.

Built with [LangChain **deep agents**](https://docs.langchain.com/oss/python/deepagents/overview)
(`deepagents.create_deep_agent`) and integrated with the Azure / Foundry model
gateway using Entra ID (no API keys).

## How it thinks

The agent grounds every recommendation in two evidence sources:

| Source | Tool(s) | Boundary |
| --- | --- | --- |
| **Competitor promotions** | `search_competitor_promotions` | Public — the same `retail-items` Azure AI Search index that powers the consumer shopping agent |
| **Internal pricing** | Pricing MCP tools (`get_product_pricing`, `simulate_price_change`, …) | Confidential — reachable **only** through the [Pricing MCP server](../pricing_mcp_server/) |

Business reasoning is packaged as **skills** (progressive-disclosure `SKILL.md`
files under [`skills/`](./skills/)) that the agent loads on demand:

- **campaign-planning** — design margin-aware promotional campaigns and flyers
- **portfolio-analysis** — assess category/assortment performance and headroom
- **pricing-optimization** — find the margin-maximising price for a product/category

## Core principle

Optimise **weekly gross margin** (`unit_margin × forecast_volume`) — never the
headline discount and never unit margin alone. Procurement cost is treated as
confidential: it informs the reasoning but is never echoed into output.

## Run

1. Start the pricing MCP server (separate terminal):

   ```bash
   python -m src.pricing_mcp_server.server
   ```

2. Run a one-shot planning query from the project root:

   ```bash
   python -m src.campaign_agent.agent "Plan next week's dairy campaign against ALDI Nord for value families"
   ```

## Environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `AZURE_OPENAI_ENDPOINT` | Model gateway endpoint | _required_ |
| `AZURE_OPENAI_CHAT_DEPLOYMENT_NAME` | Chat model deployment | `gpt-4.1-mini` |
| `AZURE_OPENAI_API_VERSION` | API version | `2025-03-01-preview` |
| `AZURE_SEARCH_ENDPOINT` | Competitor promotion index | _required_ |
| `AZURE_SEARCH_ADMIN_KEY` | Search key (else `DefaultAzureCredential`) | _optional_ |
| `AZURE_SEARCH_ITEM_INDEX_NAME` | Item index name | `retail-items` |
| `PRICING_MCP_URL` | Pricing MCP endpoint | `http://127.0.0.1:8091/mcp` |

If the pricing MCP server is unreachable the agent still starts with competitor
data only and logs a warning.
