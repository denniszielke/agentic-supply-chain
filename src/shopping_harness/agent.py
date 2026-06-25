"""Shopping Harness Agent — Foundry hosted agent edition.

A consumer-side shopping assistant that grounds every answer in live retail data
by calling a single **Foundry toolbox** over its MCP endpoint. The toolbox
(``shopping-tools``, registered by ``scripts/register_shopping_toolbox.py``)
wraps the three Azure AI Search indexes that describe the retail landscape:

  * **supplier-search**  — ``retail-suppliers``  (stores, locations, opening data)
  * **category-search**  — ``retail-categories`` (the product taxonomy)
  * **item-search**      — ``retail-items``      (products, prices, promotions)

The agent is built with the **agent-framework** and hosted in **Azure AI
Foundry** as a *hosted agent*: it is served over the RESPONSES protocol by
``ResponsesHostServer`` (the "harness") and routes model calls through the
Foundry project gateway using Entra ID — no API keys.

The design follows two references:
  * Foundry toolbox over MCP from the agent-framework SDK
    (denniszielke/msft-foundry-hosted-agents-sample · trip-scout/agent.py).
  * The managed hosted-agent harness shape
    (microsoft/Agent-Framework-Samples · maf_harness_managed_hosted_agent).

The toolbox is *not* wired point-to-point: it is published, discovered and
governed centrally in the Foundry project. For local development, set
``SHOPPING_MCP_URL`` to connect directly to a locally running MCP server and
bypass the toolbox.

Environment variables:
  AZURE_AI_PROJECT_ENDPOINT          — Foundry project endpoint (required)
  AZURE_OPENAI_CHAT_DEPLOYMENT_NAME  — chat model deployment
  AZURE_AI_MODEL_DEPLOYMENT_NAME     — fallback model deployment
  SHOPPING_TOOLBOX_NAME              — Foundry toolbox to consume
                                       (default: shopping-tools)
  TOOLBOX_MCP_ENDPOINT               — explicit toolbox MCP URL (optional)
  SHOPPING_MCP_URL                   — direct MCP URL for local dev, bypasses
                                       the toolbox (optional)
  PORT                               — host port (default: 8088)

Run the hosted agent server locally from the project root:

    python -m src.shopping_harness.agent
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import httpx
from agent_framework import MCPStreamableHTTPTool
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

# The shopping search tools are consumed through a Foundry toolbox by default so
# they are governed centrally. ``SHOPPING_MCP_URL`` bypasses the toolbox for
# local development against a directly reachable MCP server.
_TOOLBOX_NAME = os.getenv("SHOPPING_TOOLBOX_NAME", "shopping-tools")
_TOOLBOX_ENDPOINT = os.getenv("TOOLBOX_MCP_ENDPOINT") or (
    f"{_PROJECT_ENDPOINT.rstrip('/')}/toolboxes/{_TOOLBOX_NAME}/mcp?api-version=v1"
)
_DIRECT_MCP_URL = os.getenv("SHOPPING_MCP_URL", "").strip()


SHOPPING_HARNESS_SYSTEM_PROMPT = """\
You are the Shopping Harness Agent for a grocery shopper. Your job is to help a
shopper decide what to buy, where, and at what price — always grounding your
answer in live retail data, never in assumptions.

You reach all retail data through a single Foundry toolbox that exposes three
search tools. Always retrieve before you answer:
  - supplier-search — find supermarkets/discounters and their store locations,
    branches and opening details (the "where").
  - category-search — resolve a shopping need to the right product category in
    the taxonomy, and discover alternative or related categories (the "what
    kind").
  - item-search — find concrete products with their current price, original
    price, discount, unit price, packaging, promotion type, supplier and offer
    validity dates (the "what" and "how much").

Operating guidelines:
  1. Always call a search tool before stating any factual claim about a supplier,
     category, product or price. Never invent prices, discounts or stores.
  2. Resolve ambiguous items through category-search first, then look up concrete
     products with item-search.
  3. When comparing across suppliers, retrieve results per supplier and present a
     concise side-by-side comparison (supplier, product, price, discount,
     validity).
  4. Prefer the best total value for the shopper's list: weigh current price,
     discount depth, unit price and offer validity together — not headline
     discount alone.
  5. If a product has no active promotion, say so and report its current shelf
     price.
  6. Cite the supplier, product name, price and validity period for every result
     you surface.

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
# Shopping search tool (Foundry toolbox, or direct MCP for local dev)
# ---------------------------------------------------------------------------

def build_shopping_tool() -> MCPStreamableHTTPTool:
    """Build the shopping search MCP tool.

    By default the supplier/category/item search tools are consumed through a
    **Foundry toolbox** (Entra-authenticated, governed centrally). When
    ``SHOPPING_MCP_URL`` is set the agent connects directly to that MCP endpoint
    instead — convenient for local development.
    """
    if _DIRECT_MCP_URL:
        logger.info("Using direct shopping MCP endpoint %s", _DIRECT_MCP_URL)
        return MCPStreamableHTTPTool(
            name="shopping",
            url=_DIRECT_MCP_URL,
            load_prompts=False,
        )

    logger.info("Using Foundry toolbox shopping endpoint %s", _TOOLBOX_ENDPOINT)
    http_client = httpx.AsyncClient(
        auth=_ToolboxAuth(_toolbox_token_provider),
        headers={"Foundry-Features": "Toolboxes=V1Preview"},
        timeout=120.0,
    )
    return MCPStreamableHTTPTool(
        name="shopping",
        url=_TOOLBOX_ENDPOINT,
        http_client=http_client,
        load_prompts=False,
    )


# ---------------------------------------------------------------------------
# Agent assembly
# ---------------------------------------------------------------------------

_shopping_tool = build_shopping_tool()

_chat_client = FoundryChatClient(
    project_endpoint=_PROJECT_ENDPOINT,
    model=_MODEL_DEPLOYMENT,
    credential=_credential,
)

agent = _chat_client.as_agent(
    name="shopping-harness",
    instructions=SHOPPING_HARNESS_SYSTEM_PROMPT,
    tools=[_shopping_tool],
)


if __name__ == "__main__":
    ResponsesHostServer(agent).run()
