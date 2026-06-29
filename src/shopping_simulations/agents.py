"""Shopping Simulator — a Microsoft Agent Framework multi-agent workflow.

Given a shopping ask (a list of products or product categories) the workflow
fans out across suppliers, simulates one best-possible shopping bill per
supplier concurrently, then aggregates the bills into a final recommendation:

    selector  →  (supplier-bill slot × N, concurrent fan-out/fan-in)  →  aggregator

  1. **Supplier selector** — searches the supplier catalogue and picks the most
     relevant suppliers for the requested items.
  2. **Supplier-bill slots (concurrent)** — a fixed pool of ``N`` slot agents is
     fanned out from the selector; each slot is assigned one supplier id from the
     selector's list (by index) and builds the cheapest possible bill using ONLY
     that supplier's products, proposing alternatives for missing items and
     favouring attractive promotions. Unused slots stay idle.
  3. **Aggregator** — fan-in of all slot bills; picks the single supplier that
     covers all items cheapest, or — when no single supplier suffices —
     recommends the best two-stop tour, picking each item where its discount is
     best.

All retail data is grounded through the Foundry **shopping toolbox** over MCP
(``shopping-tools``, registered by ``scripts/register_shopping_toolbox.py``):
supplier-search, category-search and item-search. Models are served by the
Foundry project gateway via Entra ID (managed identity), so no API keys.

The workflow is served over the Agent Framework **DevUI** on the public port,
and emits OpenTelemetry traces to Application Insights for later use as a
Foundry external agent.

Environment variables:
  AZURE_AI_PROJECT_ENDPOINT             — Foundry project endpoint (required)
  AZURE_OPENAI_CHAT_DEPLOYMENT_NAME     — chat model deployment
  AZURE_AI_MODEL_DEPLOYMENT_NAME        — fallback model deployment
  SHOPPING_TOOLBOX_NAME                 — toolbox to consume (default: shopping-tools)
  TOOLBOX_MCP_ENDPOINT                  — explicit toolbox MCP URL (optional)
  SHOPPING_MCP_URL                      — direct MCP URL for local dev (optional)
  SHOPPING_SIM_MAX_SUPPLIERS            — concurrent supplier-bill slots (default: 5)
  APPLICATIONINSIGHTS_CONNECTION_STRING — telemetry sink (optional)
  PORT                                  — DevUI port (default: 8080)
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import httpx
from agent_framework import (
    Agent,
    Executor,
    WorkflowBuilder,
    WorkflowContext,
    handler,
)
from agent_framework.foundry import FoundryChatClient
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv
from opentelemetry import trace

# Allow standalone execution from the project root.
_src_root = Path(__file__).resolve().parents[2]
if str(_src_root) not in sys.path:
    sys.path.insert(0, str(_src_root))

_env_path = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(dotenv_path=_env_path if _env_path.exists() else None)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logging.getLogger("azure").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Tracer backed by the global provider that telemetry.setup_telemetry() wires to
# Application Insights; used to stamp custom attributes (e.g. supplier id) onto
# each executor's span. Agent and tool (MCP) spans nest underneath these.
_tracer = trace.get_tracer("shopping_simulator")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_PROJECT_ENDPOINT = os.environ["AZURE_AI_PROJECT_ENDPOINT"]
_MODEL = (
    os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME")
    or os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
    or "gpt-5.4-mini"
)
_TOOLBOX_NAME = os.getenv("SHOPPING_TOOLBOX_NAME", "shopping-tools")
_TOOLBOX_ENDPOINT = os.getenv("TOOLBOX_MCP_ENDPOINT") or (
    f"{_PROJECT_ENDPOINT.rstrip('/')}/toolboxes/{_TOOLBOX_NAME}/mcp?api-version=v1"
)
_DIRECT_MCP_URL = os.getenv("SHOPPING_MCP_URL", "").strip()
_MAX_SUPPLIERS = max(1, int(os.getenv("SHOPPING_SIM_MAX_SUPPLIERS", "5")))


# ---------------------------------------------------------------------------
# Identity / toolbox tool
# ---------------------------------------------------------------------------

_credential = DefaultAzureCredential()
_toolbox_token_provider = get_bearer_token_provider(_credential, "https://ai.azure.com/.default")


class _ToolboxAuth(httpx.Auth):
    """Inject a fresh Entra token on every Foundry toolbox MCP request."""

    def __init__(self, token_provider):
        self._get_token = token_provider

    def auth_flow(self, request):
        request.headers["Authorization"] = "Bearer " + self._get_token()
        yield request


def build_shopping_tool():
    """Build the shopping search MCP tool (Foundry toolbox or direct for dev)."""
    from agent_framework import MCPStreamableHTTPTool

    if _DIRECT_MCP_URL:
        logger.info("Using direct shopping MCP endpoint %s", _DIRECT_MCP_URL)
        return MCPStreamableHTTPTool(name="shopping", url=_DIRECT_MCP_URL, load_prompts=False)

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


def _chat_client() -> FoundryChatClient:
    return FoundryChatClient(
        project_endpoint=_PROJECT_ENDPOINT,
        model=_MODEL,
        credential=_credential,
    )


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SELECTOR_PROMPT = """\
You are the Supplier Selector. Given a shopper's request (a list of products or
product categories), use supplier-search and item-search to pick the few most
relevant suppliers that are likely to satisfy the list at attractive prices.

Output ONLY a comma-separated list of supplier brand names (no prose), most
relevant first. Pick at most {max_suppliers} suppliers.
"""

PROPOSAL_PROMPT = """\
You are a shopping-bill agent for ONE supplier: "{supplier}". Build the best
possible bill that satisfies the shopper's list using ONLY products available at
this supplier. Always ground claims with item-search / category-search.

Rules:
  - For each requested item or category, find the concrete product and its
    current price, discount and offer validity.
  - If an item is not available, propose the closest alternative and mark it.
  - Minimise the total: prefer products whose promotion is most attractive.
  - Mark any requested item you cannot cover at all as MISSING.

Return a concise bill: supplier, line items (item, product, price, discount,
note), items covered vs missing, and the total price in EUR.
"""

AGGREGATOR_PROMPT = """\
You are the Aggregator. You receive per-supplier shopping bills. Recommend:
  1. The single supplier that covers ALL requested items for the cheapest total.
  2. If no single supplier covers everything, recommend exactly TWO suppliers
     for a two-stop tour, assigning each item to the stop with the best discount,
     and minimising the combined price.

Output: the recommendation (one or two stops), coverage, combined total, and a
short rationale. Be decision-ready: lead with the recommendation, then evidence.
"""


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------

class SupplierSelector(Executor):
    """Pick the most relevant suppliers for the shopping ask."""

    def __init__(self) -> None:
        super().__init__(id="supplier-selector")
        self._agent = Agent(
            _chat_client(),
            instructions=SELECTOR_PROMPT.format(max_suppliers=_MAX_SUPPLIERS),
            tools=[build_shopping_tool()],
            name="supplier-selector",
            id="supplier-selector",
        )

    @handler
    async def select(self, request: str, ctx: WorkflowContext[dict]) -> None:
        with _tracer.start_as_current_span("supplier-selector.select") as span:
            span.set_attribute("gen_ai.agent.id", "supplier-selector")
            span.set_attribute("shopping.request", request)
            reply = await self._agent.run(request)
            suppliers = [s.strip() for s in reply.text.replace("\n", ",").split(",") if s.strip()]
            suppliers = suppliers[:_MAX_SUPPLIERS] or ["the most relevant supplier"]
            span.set_attribute("shopping.selected_suppliers", ", ".join(suppliers))
            span.set_attribute("shopping.selected_supplier_count", len(suppliers))
        logger.info("Selected suppliers: %s", suppliers)
        await ctx.send_message({"request": request, "suppliers": suppliers})


class SupplierBill(Executor):
    """One concurrent slot: builds the bill for the supplier at ``index``.

    The slot is fanned out from the selector and receives the full plan; it picks
    its supplier id by position from ``plan['suppliers']``. Slots beyond the
    number of selected suppliers emit an empty bill so the fan-in still fires.
    """

    def __init__(self, index: int) -> None:
        super().__init__(id=f"supplier-bill-{index}")
        self._index = index

    @handler
    async def propose(self, plan: dict, ctx: WorkflowContext[str]) -> None:
        request: str = plan["request"]
        suppliers: list[str] = plan["suppliers"]
        if self._index >= len(suppliers):
            await ctx.send_message("")  # idle slot — no supplier assigned
            return

        supplier = suppliers[self._index]
        with _tracer.start_as_current_span("supplier-bill.propose") as span:
            span.set_attribute("supplier.id", supplier)
            span.set_attribute("supplier.slot_index", self._index)
            span.set_attribute("gen_ai.agent.id", f"bill-{supplier}")
            agent = Agent(
                _chat_client(),
                instructions=PROPOSAL_PROMPT.format(supplier=supplier),
                tools=[build_shopping_tool()],
                name=f"bill-{supplier}",
                id=f"bill-{supplier}",
            )
            reply = await agent.run(f"Shopping list: {request}")
        await ctx.send_message(f"=== Bill from {supplier} ===\n{reply.text}")


class Aggregator(Executor):
    """Compare the bills and recommend the cheapest one- or two-stop plan."""

    def __init__(self) -> None:
        super().__init__(id="aggregator")
        self._agent = Agent(
            _chat_client(), instructions=AGGREGATOR_PROMPT, name="aggregator", id="aggregator"
        )

    @handler
    async def aggregate(self, bills: list[str], ctx: WorkflowContext[None, str]) -> None:
        joined = "\n\n".join(b for b in bills if b and b.strip())
        with _tracer.start_as_current_span("aggregator.aggregate") as span:
            span.set_attribute("gen_ai.agent.id", "aggregator")
            span.set_attribute("shopping.bill_count", sum(1 for b in bills if b and b.strip()))
            reply = await self._agent.run(f"Per-supplier bills:\n\n{joined}")
        await ctx.yield_output(reply.text)


def build_workflow():
    """Assemble selector → concurrent supplier-bill slots → aggregator."""
    selector = SupplierSelector()
    slots = [SupplierBill(i) for i in range(_MAX_SUPPLIERS)]
    aggregator = Aggregator()
    return (
        WorkflowBuilder(start_executor=selector)
        .add_fan_out_edges(selector, slots)
        .add_fan_in_edges(slots, aggregator)
        .build()
    )


workflow = build_workflow()
