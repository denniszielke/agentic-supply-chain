"""Campaign Planning Agent — Foundry hosted agent edition.

A retailer-side planning agent that decides *what to promote, at what price, for
which shopper* — balancing competitive pressure against internal margin.

It is built with the **agent-framework** and hosted in **Azure AI Foundry** as a
hosted agent (served over the RESPONSES protocol by ``ResponsesHostServer``). It
is grounded by two evidence sources:

  1. **Competitor promotions** — the shared Azure AI Search ``retail-items``
     index that powers the consumer shopping agent, surfaced through the
     ``search_competitor_promotions`` function tool.

  2. **Internal pricing** — the retailer's confidential procurement cost, weekly
     volume forecasts and margin model, exposed by the Pricing MCP server
     (``src/pricing_mcp_server``) and consumed here through a **Foundry
     toolbox** MCP endpoint, so the server is published, discovered and
     governed centrally rather than wired point-to-point.

Business reasoning is framed by the system prompt around three capabilities that
mirror the repository's skills (campaign planning, portfolio analysis and
internal pricing optimization).

Model calls are routed through Azure AI Foundry using Entra ID (no API keys).

Environment variables:
  AZURE_AI_PROJECT_ENDPOINT             — Foundry project endpoint (required)
  AZURE_OPENAI_CHAT_DEPLOYMENT_NAME     — chat model deployment
  AZURE_AI_MODEL_DEPLOYMENT_NAME        — fallback model deployment
  PRICING_TOOLBOX_NAME                  — Foundry toolbox wrapping the pricing
                                          MCP server (default: pricing-tools)
  TOOLBOX_MCP_ENDPOINT                  — explicit toolbox MCP URL (optional)
  PRICING_MCP_URL                       — direct pricing MCP URL for local dev,
                                          bypasses the toolbox (optional)
  AZURE_SEARCH_ENDPOINT                 — competitor promotion index (required)
  AZURE_SEARCH_ADMIN_KEY                — optional; else DefaultAzureCredential
  AZURE_SEARCH_ITEM_INDEX_NAME          — default: retail-items
  PORT                                  — host port (default: 8088)

Run the hosted agent server locally from the project root:

    python -m src.campaign_agent.agent
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

import httpx
from agent_framework import MCPStreamableHTTPTool, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv

# Allow standalone execution from the project root.
_src_root = Path(__file__).resolve().parents[2]
if str(_src_root) not in sys.path:
    sys.path.insert(0, str(_src_root))

_env_path = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(dotenv_path=_env_path if _env_path.exists() else None)

logging.basicConfig(level=logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_PROJECT_ENDPOINT = os.environ["AZURE_AI_PROJECT_ENDPOINT"]
_MODEL_DEPLOYMENT = (
    os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME")
    or os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
    or "gpt-4.1-mini"
)

_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "").strip()
_SEARCH_API_KEY = os.getenv("AZURE_SEARCH_ADMIN_KEY", "").strip() or None
_ITEM_INDEX = os.getenv("AZURE_SEARCH_ITEM_INDEX_NAME", "retail-items")

# The pricing MCP server is consumed through a Foundry toolbox by default so it
# is governed centrally. ``PRICING_MCP_URL`` bypasses the toolbox for local dev.
_TOOLBOX_NAME = os.getenv("PRICING_TOOLBOX_NAME", "pricing-tools")
_TOOLBOX_ENDPOINT = os.getenv("TOOLBOX_MCP_ENDPOINT") or (
    f"{_PROJECT_ENDPOINT.rstrip('/')}/toolboxes/{_TOOLBOX_NAME}/mcp?api-version=v1"
)
_DIRECT_PRICING_MCP_URL = os.getenv("PRICING_MCP_URL", "").strip()


CAMPAIGN_AGENT_SYSTEM_PROMPT = """\
You are the Campaign Planning Agent for a grocery retailer — a retailer-side
planner that decides what to promote, at what price, and for which shopper, so
that the business wins footfall against competitors **without eroding margin**.

You bring three capabilities (apply whichever the request calls for):
  - Campaign planning — design a margin-aware weekly flyer: find competitive
    gaps, pick 3–6 "hero" discounts per persona, set each promo price at the
    deepest cut that still keeps WEEKLY margin flat-to-accretive, balance it
    with full-margin "anchor" products, and forecast total campaign margin.
  - Portfolio analysis — assess category/assortment performance: rank
    categories by margin contribution and efficiency, overlay personas and
    competitive pressure, and classify each as Defend / Grow / Traffic / Fix.
  - Internal pricing optimization — find the margin-maximising price for a
    product or category by walking the price–volume curve, respecting the
    procurement-plus-logistics cost floor, and anchoring on the cheapest
    competitor price.

You reason over two evidence sources and must always ground claims in them:
  - search_competitor_promotions — what rival retailers are discounting, drawn
    from the shared retail promotion index.
  - the pricing toolbox tools (internal, confidential) — procurement cost,
    weekly volume forecasts, margin and price-change simulations from the
    Pricing MCP server: list_categories, list_products, get_product_pricing,
    get_category_margin_forecast, get_volume_forecast, simulate_price_change,
    list_personas.

Operating principles:
  1. Optimise WEEKLY GROSS MARGIN (unit margin × forecast volume), never unit
     margin alone and never headline price alone.
  2. Never propose a shelf price at or below procurement + logistics cost.
  3. Treat procurement cost as confidential: use it to reason, but report only
     prices, margin percentages and margin deltas — never raw cost.
  4. Target promotions at the personas that drive the most incremental margin in
     a category; weight volume forecasts by persona price sensitivity.
  5. Justify every recommendation with the specific tool output (numbers) behind
     it, and flag the key risks (perishability, competitor counter-move, cost
     floor proximity).

Be concise and decision-ready: lead with the recommendation, then the evidence.
"""


# ---------------------------------------------------------------------------
# Identity / credential
# ---------------------------------------------------------------------------

_credential = DefaultAzureCredential()
_toolbox_token_provider = get_bearer_token_provider(
    _credential, "https://ai.azure.com/.default"
)


class _ToolboxAuth(httpx.Auth):
    """Inject a fresh Entra token on every Foundry toolbox MCP request."""

    def __init__(self, token_provider):
        self._get_token = token_provider

    def auth_flow(self, request):
        request.headers["Authorization"] = "Bearer " + self._get_token()
        yield request


# ---------------------------------------------------------------------------
# Competitor promotion tool (Azure AI Search)
# ---------------------------------------------------------------------------

def _search_credential():
    """Return an Azure AI Search credential (key if provided, else Entra ID)."""
    if _SEARCH_API_KEY:
        from azure.core.credentials import AzureKeyCredential

        return AzureKeyCredential(_SEARCH_API_KEY)
    from azure.identity.aio import DefaultAzureCredential as AioDefaultAzureCredential

    return AioDefaultAzureCredential()


@tool
async def search_competitor_promotions(
    query: str,
    supplier: Optional[str] = None,
    top: int = 15,
) -> list[dict[str, Any]]:
    """Search competitor promotional offers from the shared retail index.

    Use this to see what rival retailers are currently discounting before
    setting our own promotion. Returns the matching offers with supplier, name,
    category, current price, original price and discount depth.

    Args:
        query: Free-text search, e.g. a product, brand or category
            ("Frischkäse", "Grillfleisch", "milchprodukte-eier").
        supplier: Optional supplier id to filter to one competitor
            (e.g. "competitor-a", "competitor-b").
        top: Maximum number of offers to return (default 15).
    """
    if not _SEARCH_ENDPOINT:
        return [{"error": "AZURE_SEARCH_ENDPOINT is not configured."}]

    from azure.search.documents.aio import SearchClient

    credential = _search_credential()
    filter_expr = None
    if supplier:
        safe = supplier.strip().replace("'", "''")
        filter_expr = f"supplier_id eq '{safe}'"

    client = SearchClient(
        endpoint=_SEARCH_ENDPOINT,
        index_name=_ITEM_INDEX,
        credential=credential,
    )
    fields = [
        "supplier_id", "name", "brand", "category_id",
        "pricing_current_price", "pricing_original_price",
        "pricing_discount_percentage", "pricing_unit_price",
        "pricing_unit_reference", "promotion_type",
        "offer_validity_start_date", "offer_validity_end_date",
    ]
    results: list[dict[str, Any]] = []
    try:
        response = await client.search(
            search_text=query,
            filter=filter_expr,
            select=",".join(fields),
            top=max(1, top),
        )
        async for doc in response:
            results.append({f: doc.get(f) for f in fields})
    finally:
        await client.close()
        close = getattr(credential, "close", None)
        if close is not None:
            await credential.close()
    return results


# ---------------------------------------------------------------------------
# Pricing MCP tool (Foundry toolbox, or direct for local dev)
# ---------------------------------------------------------------------------

def build_pricing_tool() -> MCPStreamableHTTPTool:
    """Build the pricing MCP tool.

    By default the Pricing MCP server is consumed through a **Foundry toolbox**
    (Entra-authenticated, governed centrally). When ``PRICING_MCP_URL`` is set
    the agent connects directly to that MCP endpoint instead — convenient for
    local development against ``python -m src.pricing_mcp_server.server``.
    """
    if _DIRECT_PRICING_MCP_URL:
        logger.info("Using direct pricing MCP endpoint %s", _DIRECT_PRICING_MCP_URL)
        return MCPStreamableHTTPTool(
            name="pricing",
            url=_DIRECT_PRICING_MCP_URL,
            load_prompts=False,
        )

    logger.info("Using Foundry toolbox pricing endpoint %s", _TOOLBOX_ENDPOINT)
    http_client = httpx.AsyncClient(
        auth=_ToolboxAuth(_toolbox_token_provider),
        headers={"Foundry-Features": "Toolboxes=V1Preview"},
        timeout=120.0,
    )
    return MCPStreamableHTTPTool(
        name="pricing",
        url=_TOOLBOX_ENDPOINT,
        http_client=http_client,
        load_prompts=False,
    )


# ---------------------------------------------------------------------------
# Agent assembly
# ---------------------------------------------------------------------------

_pricing_tool = build_pricing_tool()

_chat_client = FoundryChatClient(
    project_endpoint=_PROJECT_ENDPOINT,
    model=_MODEL_DEPLOYMENT,
    credential=_credential,
)

agent = _chat_client.as_agent(
    name="campaign-planner",
    instructions=CAMPAIGN_AGENT_SYSTEM_PROMPT,
    tools=[search_competitor_promotions, _pricing_tool],
)


if __name__ == "__main__":
    ResponsesHostServer(agent).run()
