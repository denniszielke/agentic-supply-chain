"""AG-UI server for the Shopping Tour Agent.

Hosts an `agent-framework` agent behind the AG-UI protocol (Server-Sent Events)
and serves an attractive, streaming single-page web UI from the same container.

The agent keeps three live sidebar panels in sync via AG-UI *shared state*:

  * Shopping List      — the shopper's items and their match status
  * Selected Suppliers — the ≤ 2 stores chosen for the tour
  * Bill Projection    — projected total, number of stops, and savings

State is pushed deterministically through the ``update_plan`` tool, which
returns :func:`agent_framework_ag_ui.state_update`. After the tool runs the
endpoint emits a ``STATE_SNAPSHOT`` event that the browser renders.

Endpoints:
  GET  /          — the chat web UI
  POST /agent     — the AG-UI protocol endpoint (SSE stream)
  GET  /healthz   — liveness probe

Environment variables: see ``shopping_agent.py`` (the agent configuration is
shared). Additionally honours ``HOST`` and ``PORT`` (default 0.0.0.0:8090).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, List, Optional

import uvicorn
from agent_framework import Agent, Content, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework_ag_ui import add_agent_framework_fastapi_endpoint, state_update
from azure.core.credentials import AzureKeyCredential
from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

# Allow `python -m src.shopping_agent.server` and uvicorn module loading.
_src_root = Path(__file__).resolve().parents[2]
if str(_src_root) not in sys.path:
    sys.path.insert(0, str(_src_root))

from src.shared.prompts import SHOPPING_AGENT_UI_INSTRUCTIONS  # noqa: E402
from src.shopping_agent.shopping_agent import (  # noqa: E402
    _ITEM_INDEX,
    _MODEL,
    _PROJECT_ENDPOINT,
    _SEARCH_API_KEY,
    _SEARCH_ENDPOINT,
    make_providers,
)

_SUPPLIER_INDEX = os.getenv("AZURE_SEARCH_SUPPLIER_INDEX_NAME", "retail-suppliers")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logging.getLogger("azure").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


class _DropCurrentStateMetadataWarning(logging.Filter):
    """Silence the benign 'Dropping metadata key 'current_state'' warning.

    The AG-UI framework copies the shared sidebar state into optional session
    metadata, which Azure caps at 512 chars. When our sidebar JSON exceeds that
    the copy is dropped — but this does NOT affect the LLM-visible state context
    (injected separately from ``flow.current_state``), so the warning is noise.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return "Dropping metadata key 'current_state'" not in record.getMessage()


logging.getLogger("agent_framework_ag_ui._agent_run").addFilter(_DropCurrentStateMetadataWarning())

_TEMPLATES_DIR = Path(__file__).parent / "templates"


# ---------------------------------------------------------------------------
# Shared-state schema used by the UI sidebar
# ---------------------------------------------------------------------------

class ShoppingListItem(BaseModel):
    """A single entry in the live shopping-list panel."""

    name: str = Field(description="Product the shopper wants, e.g. 'Vollmilch 1l'.")
    quantity: str = Field(default="1", description="Requested amount, e.g. '2' or '500 g'.")
    status: str = Field(
        default="planned",
        description=(
            "One of: 'planned' (named, not yet matched), 'matched' (offer found), "
            "'unavailable' (no current offer), 'upcoming' (offer starts in the future), "
            "'non_food' (unusual non-food find)."
        ),
    )
    supplier: Optional[str] = Field(default=None, description="Brand/store of the chosen offer.")
    price: Optional[float] = Field(default=None, description="Promo price in EUR for the matched offer.")
    note: Optional[str] = Field(default=None, description="Short context: pack size, unit price, deal, or date.")


class SelectedSupplier(BaseModel):
    """A store included in the optimised tour."""

    brand: str = Field(description="Retailer brand, e.g. 'ALDI SÜD'.")
    store_name: Optional[str] = Field(default=None, description="Specific store name.")
    region: Optional[str] = Field(default=None, description="City / region of the store.")
    item_count: int = Field(default=0, description="Number of list items covered at this store.")


class BillProjection(BaseModel):
    """The projected cost of the planned shopping tour."""

    total: float = Field(default=0.0, description="Projected total spend in the currency below.")
    currency: str = Field(default="EUR", description="Currency code, normally EUR.")
    stops: int = Field(default=0, description="Number of stores the shopper must visit (≤ 2).")
    savings: Optional[float] = Field(default=None, description="Savings vs. the naïve cheapest-per-item plan.")
    note: Optional[str] = Field(default=None, description="Short headline about the projection.")


@tool
def update_plan(
    shopping_list: List[ShoppingListItem],
    suppliers: List[SelectedSupplier],
    bill: BillProjection,
) -> Content:
    """Refresh the live sidebar (shopping list, selected suppliers, bill projection).

    Call this whenever the plan changes. Always pass the COMPLETE current lists —
    the three panels are replaced wholesale, not merged. Call this BEFORE writing
    your chat explanation so the UI updates instantly.
    """
    # The framework may deliver the arguments as plain dicts rather than as
    # instantiated Pydantic models, so coerce them through the models here.
    return state_update(
        text="Shopping plan sidebar updated.",
        state={
            "shopping_list": [ShoppingListItem.model_validate(item).model_dump() for item in shopping_list],
            "suppliers": [SelectedSupplier.model_validate(supplier).model_dump() for supplier in suppliers],
            "bill": BillProjection.model_validate(bill).model_dump(),
        },
    )


def _search_credential():
    """Credential for the Azure AI Search data-plane client."""
    if _SEARCH_API_KEY:
        return AzureKeyCredential(_SEARCH_API_KEY)
    return _credential


async def _resolve_supplier_id(supplier: str) -> Optional[str]:
    """Map a retailer brand or id (e.g. 'ALDI SÜD') to its supplier_id ('aldi-sued')."""
    async with SearchClient(
        endpoint=_SEARCH_ENDPOINT,
        index_name=_SUPPLIER_INDEX,
        credential=_search_credential(),
    ) as client:
        results = await client.search(
            search_text=supplier,
            select=["supplier_id", "brand"],
            top=1,
        )
        async for doc in results:
            sid = doc.get("supplier_id")
            if sid:
                return sid
    return None


@tool
async def get_supplier_discounts(
    supplier: str,
    min_discount_percentage: float = 20.0,
    top: int = 15,
) -> str:
    """Retrieve the most heavily discounted products currently on offer at ONE specific supplier.

    Use this whenever the shopper asks what is especially cheap / on sale / the
    best deals at a named store (e.g. "Was ist gerade bei ALDI SÜD besonders
    günstig?"). Returns offers sorted by discount, highest first.

    Args:
        supplier: Retailer brand or id, e.g. 'ALDI SÜD', 'REWE', 'Lidl', 'rewe'.
        min_discount_percentage: Only return offers with at least this discount (default 20%).
        top: Maximum number of offers to return (default 15).
    """
    supplier_id = await _resolve_supplier_id(supplier)
    if supplier_id is None:
        return f"No supplier matching '{supplier}' was found in the catalog."

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    filter_expr = (
        f"supplier_id eq '{supplier_id}' "
        f"and pricing_discount_percentage ge {float(min_discount_percentage)} "
        f"and offer_validity_end_date ge {now}"
    )
    select = [
        "name", "brand", "category_id",
        "pricing_current_price", "pricing_original_price", "pricing_discount_percentage",
        "pricing_unit_price", "pricing_unit_reference",
        "packaging_quantity", "packaging_unit_type",
        "promotion_type", "conditions_deposit",
        "offer_validity_start_date", "offer_validity_end_date",
    ]

    offers: list[dict] = []
    async with SearchClient(
        endpoint=_SEARCH_ENDPOINT,
        index_name=_ITEM_INDEX,
        credential=_search_credential(),
    ) as client:
        results = await client.search(
            search_text="*",
            filter=filter_expr,
            order_by=["pricing_discount_percentage desc"],
            select=select,
            top=int(top),
        )
        async for doc in results:
            offers.append({k: doc.get(k) for k in select})

    if not offers:
        return (
            f"No active offers with a discount of at least {min_discount_percentage:.0f}% "
            f"were found at {supplier} (supplier_id={supplier_id}) right now."
        )

    lines = [
        f"Top {len(offers)} discounted offers at {supplier} "
        f"(supplier_id={supplier_id}, ≥ {min_discount_percentage:.0f}% off, active today):",
    ]
    for o in offers:
        disc = o.get("pricing_discount_percentage")
        price = o.get("pricing_current_price")
        orig = o.get("pricing_original_price")
        pack = " ".join(
            str(p) for p in (o.get("packaging_quantity"), o.get("packaging_unit_type")) if p
        )
        unit = (
            f", {o['pricing_unit_price']} EUR/{o['pricing_unit_reference']}"
            if o.get("pricing_unit_price") and o.get("pricing_unit_reference")
            else ""
        )
        deposit = f", +{o['conditions_deposit']} EUR Pfand" if o.get("conditions_deposit") else ""
        promo = f" [{o['promotion_type']}]" if o.get("promotion_type") else ""
        lines.append(
            f"- {o.get('name')}"
            + (f" ({o['brand']})" if o.get("brand") else "")
            + f": {price} EUR"
            + (f" (was {orig} EUR" if orig else "")
            + (f", -{disc:.0f}%)" if disc is not None else (")" if orig else ""))
            + (f", {pack}" if pack else "")
            + unit
            + deposit
            + promo
            + f" — valid until {o.get('offer_validity_end_date')}"
        )
    return "\n".join(lines)


DEFAULT_STATE: dict = {
    "shopping_list": [],
    "suppliers": [],
    "bill": {"total": 0.0, "currency": "EUR", "stops": 0, "savings": None, "note": None},
}

STATE_SCHEMA: dict = {
    "shopping_list": {"type": "array"},
    "suppliers": {"type": "array"},
    "bill": {"type": "object"},
}


# ---------------------------------------------------------------------------
# Agent + context providers (built once, kept alive for the app lifetime)
# ---------------------------------------------------------------------------

_credential = DefaultAzureCredential()
_kb_provider, _item_provider, _category_provider, _embedding_client = make_providers(_credential)

_agent = Agent(
    client=FoundryChatClient(
        project_endpoint=_PROJECT_ENDPOINT,
        model=_MODEL,
        credential=_credential,
    ),
    name="ShoppingTourAgent",
    instructions=SHOPPING_AGENT_UI_INSTRUCTIONS,
    tools=[update_plan, get_supplier_discounts],
    context_providers=[_kb_provider, _item_provider, _category_provider],
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Enter the agent + provider async contexts for the lifetime of the app."""
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(_kb_provider)
        await stack.enter_async_context(_item_provider)
        await stack.enter_async_context(_category_provider)
        await stack.enter_async_context(_agent)
        logger.info("Shopping Tour Agent ready (model=%s).", _MODEL)
        try:
            yield
        finally:
            if _embedding_client is not None:
                close = getattr(_embedding_client, "close", None)
                if close is not None:
                    result = close()
                    if asyncio.iscoroutine(result):
                        await result
            await _credential.close()


app = FastAPI(title="Shopping Tour Agent — AG-UI", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return (_TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


add_agent_framework_fastapi_endpoint(
    app=app,
    agent=_agent,
    path="/agent",
    state_schema=STATE_SCHEMA,
    default_state=DEFAULT_STATE,
)


def main() -> None:
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8090"))
    logger.info("Starting Shopping Tour Agent AG-UI server on http://%s:%d", host, port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
