"""Simulated **SAP Joule** agent — standalone A2A server on Azure Container Apps.

This service deliberately lives **outside** Azure AI Foundry. It runs as its own
Container App (its own runtime, its own synthetic data) and speaks the open
**A2A** protocol so that other agents — notably the Campaign Planning Agent —
can hand it a scoped, auditable sub-task across the vendor boundary. It stands in
for an SAP Joule agent fronting **ERP / supply-side** master data.

Although Foundry never *hosts* this agent, it is still **registered in the Foundry
control plane** with a managed **agent identity blueprint** and reached over A2A
through a project connection (see ``scripts/register_joule_agent.py``). This models
the "agents are digital employees, governed by one identity fabric regardless of
where they run" story.

The single business question it answers: *given a product (SKU or name) and an
optional forecast weekly volume, can the supply chain fulfil it?* — joining stock
on hand, safety stock, in-transit units, weekly replenishment, open purchase
orders and supplier lead times. All data is **synthetic** and loaded once from
``joule_data.json`` next to this module — no database, no external dependency.

Built on the official **`a2a-sdk`** (``AgentExecutor`` + ``A2AStarletteApplication``):

    python -m src.joule_agent.server

Serves the A2A Agent Card at ``/.well-known/agent-card.json`` and the JSON-RPC
endpoint at ``/`` (``message/send`` / ``message/stream``), plus a ``/health``
probe. Bind address: ``JOULE_AGENT_HOST`` / ``JOULE_AGENT_PORT`` (default
``0.0.0.0:8092``). The Agent Card advertises ``JOULE_PUBLIC_URL`` when set so the
deployed card points at the public Container App URL.
"""

from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from a2a.utils import new_agent_text_message
from starlette.requests import Request
from starlette.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

_DATA_FILE = Path(__file__).with_name("joule_data.json")

HOST = os.getenv("JOULE_AGENT_HOST", "0.0.0.0")
PORT = int(os.getenv("JOULE_AGENT_PORT", "8092"))
# Public URL advertised in the Agent Card (the Container App ingress URL once
# deployed). Falls back to the local bind address for development.
PUBLIC_URL = os.getenv("JOULE_PUBLIC_URL", f"http://localhost:{PORT}").rstrip("/")


# ---------------------------------------------------------------------------
# Synthetic supply data + fulfilment logic
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _load_data() -> dict[str, Any]:
    with _DATA_FILE.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _products() -> list[dict[str, Any]]:
    return _load_data()["products"]


def _snapshot_date() -> str:
    return _load_data().get("meta", {}).get("snapshot_date", "")


def _match_product(query: str) -> Optional[dict[str, Any]]:
    """Resolve a product by exact SKU or case-insensitive name substring."""
    q = query.strip().lower()
    if not q:
        return None
    for p in _products():
        if p["sku"].lower() == q:
            return p
    for p in _products():
        if q in p["product_name"].lower():
            return p
    return None


def _extract_sku(text: str) -> Optional[str]:
    """Pull an ``AS-XX-000`` style SKU out of free text, if present."""
    m = re.search(r"\b([A-Za-z]{2}-[A-Za-z]{2}-\d{3})\b", text)
    return m.group(1) if m else None


def _extract_required_units(text: str) -> Optional[int]:
    """Pull a requested weekly volume (the largest integer) out of free text."""
    numbers = re.findall(r"\d[\d.,]*", text)
    best: Optional[int] = None
    for raw in numbers:
        cleaned = raw.replace(".", "").replace(",", "")
        if not cleaned.isdigit():
            continue
        value = int(cleaned)
        if best is None or value > best:
            best = value
    return best


def _available_now(product: dict[str, Any]) -> int:
    """Stock available to promise = on-hand minus safety stock (floored at 0)."""
    return max(0, product["stock_on_hand_units"] - product["safety_stock_units"])


def _incoming_within(product: dict[str, Any], horizon_days: int) -> tuple[int, Optional[str]]:
    """Sum open-PO quantities arriving within ``horizon_days`` of the snapshot.

    Returns (incoming_units, earliest_eta) where earliest_eta is the nearest PO
    delivery date overall (even if beyond the horizon), or None when there are
    no open POs.
    """
    from datetime import date

    snapshot = _snapshot_date()
    try:
        ref = date.fromisoformat(snapshot)
    except ValueError:
        ref = None

    incoming = 0
    earliest: Optional[str] = None
    for po in product.get("open_purchase_orders", []):
        eta = po.get("expected_delivery_date")
        if earliest is None or (eta and eta < earliest):
            earliest = eta
        if ref is not None and eta:
            try:
                days = (date.fromisoformat(eta) - ref).days
            except ValueError:
                continue
            if 0 <= days <= horizon_days:
                incoming += int(po.get("quantity_units", 0))
    return incoming, earliest


def assess_fulfilment(product: dict[str, Any], required_weekly_units: Optional[int]) -> dict[str, Any]:
    """Core supply assessment for one product against a forecast weekly volume."""
    available = _available_now(product)
    weekly_inbound = int(product.get("weekly_inbound_units", 0))
    in_transit = int(product.get("in_transit_units", 0))
    incoming_week, earliest_eta = _incoming_within(product, horizon_days=7)

    # Supply we can commit for the promo week: stock available to promise, plus
    # in-transit units, plus open POs landing inside the week.
    committable = available + in_transit + incoming_week

    result: dict[str, Any] = {
        "sku": product["sku"],
        "product_name": product["product_name"],
        "category_id": product["category_id"],
        "dc_plant": product["dc_plant"],
        "snapshot_date": _snapshot_date(),
        "supplier": product["supplier"],
        "stock_on_hand_units": product["stock_on_hand_units"],
        "safety_stock_units": product["safety_stock_units"],
        "available_to_promise_units": available,
        "in_transit_units": in_transit,
        "weekly_inbound_units": weekly_inbound,
        "open_purchase_orders": product.get("open_purchase_orders", []),
        "earliest_restock_date": earliest_eta,
        "baseline_weekly_demand_units": product.get("baseline_weekly_demand_units"),
    }

    if required_weekly_units is not None:
        shortfall = max(0, required_weekly_units - committable)
        result.update(
            {
                "required_weekly_units": required_weekly_units,
                "committable_units": committable,
                "can_fulfil": shortfall == 0,
                "projected_shortfall_units": shortfall,
                "recommendation": (
                    "fulfillable"
                    if shortfall == 0
                    else "expedite_or_reduce_promo"
                ),
            }
        )
    return result


def _format_assessment(a: dict[str, Any]) -> str:
    sup = a["supplier"]
    lines = [
        f"Supply assessment for {a['product_name']} (SKU {a['sku']}) "
        f"as of {a['snapshot_date']}:",
        f"- DC/plant: {a['dc_plant']}",
        f"- Supplier: {sup['name']} ({sup['vendor_id']}, {sup['country']}), "
        f"lead time {sup['lead_time_days']} days",
        f"- Stock on hand: {a['stock_on_hand_units']:,} EA "
        f"(safety stock {a['safety_stock_units']:,})",
        f"- Available to promise: {a['available_to_promise_units']:,} EA",
        f"- In transit: {a['in_transit_units']:,} EA; "
        f"weekly inbound {a['weekly_inbound_units']:,} EA",
    ]
    pos = a.get("open_purchase_orders", [])
    if pos:
        lines.append(f"- Open purchase orders ({len(pos)}):")
        for po in pos:
            lines.append(
                f"    {po['po_number']}: {po['quantity_units']:,} EA, "
                f"ETA {po['expected_delivery_date']} [{po['status']}]"
            )
    if a.get("earliest_restock_date"):
        lines.append(f"- Earliest restock: {a['earliest_restock_date']}")

    if "required_weekly_units" in a:
        verdict = "CAN fulfil" if a["can_fulfil"] else "CANNOT fully fulfil"
        lines.append(
            f"- Requested weekly volume: {a['required_weekly_units']:,} EA -> "
            f"{verdict} (committable {a['committable_units']:,} EA)."
        )
        if not a["can_fulfil"]:
            lines.append(
                f"  Projected shortfall {a['projected_shortfall_units']:,} EA — "
                f"recommend expediting POs from {sup['name']} or trimming the promo."
            )
    lines.append("")
    lines.append("structured:")
    lines.append(json.dumps(a, ensure_ascii=False))
    return "\n".join(lines)


def _list_catalog() -> str:
    lines = ["Supply catalog (SAP Joule synthetic ERP snapshot):"]
    for p in _products():
        lines.append(
            f"- {p['sku']}: {p['product_name']} "
            f"(stock {p['stock_on_hand_units']:,} EA, "
            f"supplier {p['supplier']['name']})"
        )
    return "\n".join(lines)


def answer(query: str) -> str:
    """Turn a free-text A2A request into a supply answer."""
    text = (query or "").strip()
    if not text:
        return (
            "I am the SAP Joule supply agent. Ask me whether the supply chain can "
            "fulfil a promotion, e.g. 'Can we fulfil 30000 units of AS-FW-002 next "
            "week?' or 'stock for Rinderhackfleisch'. Say 'list' to see the catalog."
        )

    lowered = text.lower()
    if "list" in lowered and ("catalog" in lowered or "products" in lowered or "sku" in lowered or lowered == "list"):
        return _list_catalog()

    sku = _extract_sku(text)
    product = _match_product(sku) if sku else None
    if product is None:
        # Try to match a product name fragment from the catalog.
        for p in _products():
            if p["product_name"].lower() in lowered or any(
                tok in lowered for tok in p["product_name"].lower().split() if len(tok) > 4
            ):
                product = p
                break

    if product is None:
        return (
            f"I could not resolve a product from '{text}'. Provide a SKU like "
            f"AS-FW-002 or a product name. Say 'list catalog' to see available SKUs."
        )

    required = _extract_required_units(text)
    assessment = assess_fulfilment(product, required)
    return _format_assessment(assessment)


# ---------------------------------------------------------------------------
# A2A wiring
# ---------------------------------------------------------------------------


class JouleSupplyExecutor(AgentExecutor):
    """A2A executor that answers supply / fulfilment questions."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_input = context.get_user_input()
        logger.info("Joule supply request: %s", user_input)
        reply = answer(user_input)
        await event_queue.enqueue_event(new_agent_text_message(reply))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # The agent answers synchronously from static data — nothing to cancel.
        raise NotImplementedError("cancel is not supported by the Joule supply agent")


def _skills() -> list[AgentSkill]:
    return [
        AgentSkill(
            id="fulfilment-check",
            name="Promotion Fulfilment Check",
            description=(
                "Given a SKU (or product name) and a forecast weekly volume, decide "
                "whether the supply chain can fulfil it from stock on hand, in-transit "
                "units, weekly replenishment and open purchase orders."
            ),
            tags=["supply-chain", "erp", "fulfilment", "sap"],
            examples=[
                "Can we fulfil 30000 units of AS-FW-002 next week?",
                "Is there enough supply for Rinderhackfleisch for a promotion of 25000 units?",
            ],
        ),
        AgentSkill(
            id="stock-lookup",
            name="Stock & Supplier Lookup",
            description=(
                "Return current stock on hand, safety stock, in-transit units, "
                "supplier and lead time, and open purchase orders for a product."
            ),
            tags=["supply-chain", "erp", "inventory", "sap"],
            examples=["stock for AS-ME-001", "supplier and lead time for Gouda jung 400g"],
        ),
    ]


def build_agent_card() -> AgentCard:
    return AgentCard(
        name="SAP Joule Supply Agent",
        description=(
            "Simulated SAP Joule agent fronting ERP supply-side data. Answers whether "
            "the supply chain can fulfil a planned promotion for a given product and "
            "forecast volume, joining stock, replenishment, open POs and supplier lead "
            "times. Synthetic data; runs outside Foundry, reachable over A2A."
        ),
        url=f"{PUBLIC_URL}/",
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text"],
        default_output_modes=["text"],
        skills=_skills(),
    )


def build_app():
    handler = DefaultRequestHandler(
        agent_executor=JouleSupplyExecutor(),
        task_store=InMemoryTaskStore(),
    )
    a2a_app = A2AStarletteApplication(agent_card=build_agent_card(), http_handler=handler)
    app = a2a_app.build()

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app.add_route("/health", health, methods=["GET"])
    return app


app = build_app()


def main() -> None:
    logger.info(
        "Starting SAP Joule supply A2A agent on http://%s:%d (card url=%s)",
        HOST,
        PORT,
        f"{PUBLIC_URL}/",
    )
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
