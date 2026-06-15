"""Campaign Planning Agent — LangChain deep agent edition.

A retailer-side planning agent that decides *what to promote, at what price, for
which shopper* — balancing competitive pressure against internal margin. It is
built with `deepagents.create_deep_agent` and is grounded by two evidence
sources:

  1. **Competitor promotions** — the same Azure AI Search ``retail-items`` index
     that powers the consumer shopping agent, surfaced here through the
     ``search_competitor_promotions`` tool.

  2. **Internal pricing** — ALDI SÜD's confidential procurement cost, weekly
     volume forecasts and margin model, reached only through the Pricing MCP
     server (``src/pricing_mcp_server``) over the Model Context Protocol.

Business reasoning is packaged as **skills** (progressive-disclosure SKILL.md
files under ``skills/``): campaign planning, portfolio analysis and internal
pricing optimization. The agent loads the relevant skill on demand.

Model calls are routed through the Azure OpenAI / Foundry model gateway using
Entra ID (no API keys), mirroring the rest of the solution.

Environment variables:
  AZURE_OPENAI_ENDPOINT                 — model gateway endpoint (required)
  AZURE_OPENAI_CHAT_DEPLOYMENT_NAME     — chat model deployment (required)
  AZURE_OPENAI_API_VERSION              — default: 2025-03-01-preview
  AZURE_SEARCH_ENDPOINT                 — competitor promotion index (required)
  AZURE_SEARCH_ADMIN_KEY                — optional; else DefaultAzureCredential
  AZURE_SEARCH_ITEM_INDEX_NAME          — default: retail-items
  PRICING_MCP_URL                       — default: http://127.0.0.1:8091/mcp

Run a one-shot query from the project root:

    python -m src.campaign_agent.agent "Plan next week's dairy campaign against ALDI Nord"
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_openai import AzureChatOpenAI

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_mcp_adapters.client import MultiServerMCPClient

# Allow standalone execution from the project root.
_src_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_src_root))

_env_path = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(dotenv_path=_env_path if _env_path.exists() else None)

logging.basicConfig(level=logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

_PACKAGE_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_AOAI_ENDPOINT = os.environ["AZURE_OPENAI_ENDPOINT"]
_CHAT_DEPLOYMENT = (
    os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME")
    or os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
    or "gpt-4.1-mini"
)
_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-03-01-preview")

_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "").strip()
_SEARCH_API_KEY = os.getenv("AZURE_SEARCH_ADMIN_KEY", "").strip() or None
_ITEM_INDEX = os.getenv("AZURE_SEARCH_ITEM_INDEX_NAME", "retail-items")

_PRICING_MCP_URL = os.getenv("PRICING_MCP_URL", "http://127.0.0.1:8091/mcp")

_TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"


CAMPAIGN_AGENT_SYSTEM_PROMPT = """\
You are the Campaign Planning Agent for ALDI SÜD — a retailer-side planner that
decides what to promote, at what price, and for which shopper, so that the
business wins footfall against competitors **without eroding margin**.

You have access to skills (load them when relevant):
  - campaign-planning — design margin-aware promotional campaigns and flyers
  - portfolio-analysis — assess category/assortment performance and headroom
  - pricing-optimization — find the margin-maximising price for a product/category

You reason over two evidence sources and must always ground claims in them:
  - search_competitor_promotions — what rivals (ALDI Nord, REWE, Edeka, Lidl,
    Kaufland, Netto, Penny …) are discounting, drawn from the shared retail
    promotion index.
  - the pricing tools (internal, confidential) — procurement cost, weekly volume
    forecasts, margin and price-change simulations from the Pricing MCP server.

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
# Model
# ---------------------------------------------------------------------------

def build_model() -> AzureChatOpenAI:
    """Build the chat model, authenticating to the gateway with Entra ID."""
    token_provider = get_bearer_token_provider(DefaultAzureCredential(), _TOKEN_SCOPE)
    return AzureChatOpenAI(
        azure_endpoint=_AOAI_ENDPOINT,
        azure_deployment=_CHAT_DEPLOYMENT,
        api_version=_API_VERSION,
        azure_ad_token_provider=token_provider,
        streaming=True,
        temperature=0.0,
    )


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
            (e.g. "aldi-nord", "rewe", "edeka-pienka", "lidl").
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
# Pricing MCP tools
# ---------------------------------------------------------------------------

async def load_pricing_tools() -> list:
    """Load the internal pricing tools from the Pricing MCP server.

    Returns an empty list (with a warning) if the server is unreachable so the
    agent can still start with competitor data only.
    """
    client = MultiServerMCPClient(
        {
            "pricing": {
                "transport": "streamable_http",
                "url": _PRICING_MCP_URL,
            }
        }
    )
    try:
        return await client.get_tools(server_name="pricing")
    except Exception as exc:  # noqa: BLE001 - degrade gracefully
        logger.warning(
            "Could not reach pricing MCP server at %s (%s). "
            "Start it with `python -m src.pricing_mcp_server.server`.",
            _PRICING_MCP_URL,
            exc,
        )
        return []


# ---------------------------------------------------------------------------
# Agent assembly
# ---------------------------------------------------------------------------

async def build_agent():
    """Assemble the campaign planning deep agent with all tools and skills."""
    pricing_tools = await load_pricing_tools()
    tools = [search_competitor_promotions, *pricing_tools]

    backend = FilesystemBackend(root_dir=str(_PACKAGE_DIR), virtual_mode=True)

    return create_deep_agent(
        model=build_model(),
        tools=tools,
        system_prompt=CAMPAIGN_AGENT_SYSTEM_PROMPT,
        skills=["/skills/"],
        backend=backend,
        name="campaign-planner",
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def _run(query: str) -> None:
    agent = await build_agent()
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": query}]},
        config={"configurable": {"thread_id": str(uuid.uuid4())}},
    )
    messages = result.get("messages", [])
    if messages:
        final = messages[-1]
        content = getattr(final, "content", final)
        print(content if isinstance(content, str) else str(content))


def main() -> None:
    query = " ".join(sys.argv[1:]).strip() or (
        "Plan next week's promotional campaign for the dairy category "
        "(milchprodukte-eier) against ALDI Nord, targeting value-driven families."
    )
    asyncio.run(_run(query))


if __name__ == "__main__":
    main()
