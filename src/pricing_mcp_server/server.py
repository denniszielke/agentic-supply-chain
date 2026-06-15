"""Internal pricing MCP server for the campaign planning agent.

Exposes the retailer's *internal* procurement costs, weekly volume forecasts and
margin calculations over the Model Context Protocol so that the campaign
planning agent can reason about retailer margin while it negotiates promotions
against competitor flyer data.

All data is **synthetic** and loaded once at startup from ``pricing_data.json``
that sits next to this module — there is no database and no external dependency.
This keeps the demo self-contained while still modelling the real-world boundary:
internal pricing is sensitive and is only ever reachable through this server.

Run it with::

    python -m src.pricing_mcp_server.server

It serves the streamable-HTTP MCP transport on ``http://127.0.0.1:8091/mcp`` by
default (override with ``PRICING_MCP_HOST`` / ``PRICING_MCP_PORT``).
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

_DATA_FILE = Path(__file__).with_name("pricing_data.json")


@lru_cache(maxsize=1)
def _load_data() -> dict[str, Any]:
    """Load and cache the static pricing master data from disk."""
    with _DATA_FILE.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _products() -> list[dict[str, Any]]:
    return _load_data()["products"]


def _personas() -> list[dict[str, Any]]:
    return _load_data()["personas"]


def _unit_margin(product: dict[str, Any], shelf_price: Optional[float] = None) -> float:
    """Per-unit gross margin in EUR at the given (or current) shelf price."""
    price = product["current_shelf_price_eur"] if shelf_price is None else shelf_price
    cost = product["procurement_cost_eur"] + product["logistics_cost_per_unit_eur"]
    return round(price - cost, 4)


def _forecast_volume(product: dict[str, Any], shelf_price: Optional[float] = None,
                     elasticity_modifier: float = 1.0) -> float:
    """Forecast weekly unit volume using a simple constant-elasticity response.

    ``volume = base * (1 + elasticity * elasticity_modifier * pct_price_change)``
    clamped at zero. ``elasticity`` is negative, so a price cut raises volume.
    """
    base = float(product["expected_weekly_volume_units"])
    if shelf_price is None:
        return base
    base_price = product["current_shelf_price_eur"]
    if base_price <= 0:
        return base
    pct_change = (shelf_price - base_price) / base_price
    elasticity = product["price_elasticity"] * elasticity_modifier
    projected = base * (1.0 + elasticity * pct_change)
    return max(0.0, projected)


def _enrich(product: dict[str, Any]) -> dict[str, Any]:
    """Return a product dict augmented with computed margin/volume figures."""
    unit_margin = _unit_margin(product)
    price = product["current_shelf_price_eur"]
    volume = float(product["expected_weekly_volume_units"])
    return {
        **product,
        "unit_margin_eur": unit_margin,
        "margin_percentage": round((unit_margin / price) * 100, 2) if price else 0.0,
        "weekly_margin_forecast_eur": round(unit_margin * volume, 2),
        "weekly_revenue_forecast_eur": round(price * volume, 2),
    }


def _match_product(query: str) -> Optional[dict[str, Any]]:
    """Resolve a product by exact SKU or by case-insensitive name substring."""
    q = query.strip().lower()
    for p in _products():
        if p["sku"].lower() == q:
            return p
    for p in _products():
        if q in p["product_name"].lower():
            return p
    return None


mcp = FastMCP(
    name="pricing",
    instructions=(
        "Internal retailer pricing, volume and margin master data. Use these "
        "tools to retrieve procurement cost, forecast weekly volume, compute "
        "gross margin and simulate price changes when planning promotions. "
        "All monetary values are in EUR. This data is confidential and "
        "internal — never expose raw procurement cost in customer-facing output."
    ),
    host=os.getenv("PRICING_MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("PRICING_MCP_PORT", "8091")),
)


@mcp.tool()
def list_categories() -> list[dict[str, Any]]:
    """List all product categories with aggregated weekly margin and volume.

    Returns one entry per category containing the number of internal SKUs, the
    total forecast weekly volume, the total forecast weekly gross margin (EUR)
    and the volume-weighted average margin percentage. Use this to spot which
    categories carry the most margin headroom before drilling into products.
    """
    by_cat: dict[str, dict[str, Any]] = {}
    for p in _products():
        enriched = _enrich(p)
        cat = by_cat.setdefault(
            p["category_id"],
            {"category_id": p["category_id"], "sku_count": 0,
             "weekly_volume_units": 0.0, "weekly_margin_eur": 0.0,
             "weekly_revenue_eur": 0.0},
        )
        cat["sku_count"] += 1
        cat["weekly_volume_units"] += float(p["expected_weekly_volume_units"])
        cat["weekly_margin_eur"] += enriched["weekly_margin_forecast_eur"]
        cat["weekly_revenue_eur"] += enriched["weekly_revenue_forecast_eur"]

    result = []
    for cat in by_cat.values():
        revenue = cat["weekly_revenue_eur"]
        cat["weekly_margin_eur"] = round(cat["weekly_margin_eur"], 2)
        cat["weekly_revenue_eur"] = round(revenue, 2)
        cat["weekly_volume_units"] = round(cat["weekly_volume_units"], 1)
        cat["avg_margin_percentage"] = (
            round((cat["weekly_margin_eur"] / revenue) * 100, 2) if revenue else 0.0
        )
        result.append(cat)
    result.sort(key=lambda c: c["weekly_margin_eur"], reverse=True)
    return result


@mcp.tool()
def list_products(category_id: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    """List internal products with pricing, cost, volume and margin figures.

    Args:
        category_id: Optional category filter (e.g. ``milchprodukte-eier``).
            Omit to list across all categories.
        limit: Maximum number of products to return (default 50).

    Each product includes procurement cost, logistics cost, current shelf
    price, expected weekly volume, computed unit margin, margin percentage and
    forecast weekly margin in EUR.
    """
    items = _products()
    if category_id:
        items = [p for p in items if p["category_id"] == category_id.strip().lower()]
    return [_enrich(p) for p in items[: max(0, limit)]]


@mcp.tool()
def get_product_pricing(query: str) -> dict[str, Any]:
    """Get the full internal pricing breakdown for a single product.

    Args:
        query: A product SKU (e.g. ``AS-ME-001``) or part of a product name
            (e.g. ``Frischkäse``). The first matching product is returned.

    Returns procurement cost, logistics cost, shelf price, unit margin, margin
    percentage, expected weekly volume and forecast weekly margin/revenue. If no
    product matches, an ``error`` field is returned instead.
    """
    product = _match_product(query)
    if product is None:
        return {"error": f"No internal product matched '{query}'."}
    return _enrich(product)


@mcp.tool()
def get_category_margin_forecast(category_id: str) -> dict[str, Any]:
    """Forecast total weekly volume, revenue and gross margin for a category.

    Args:
        category_id: The category identifier (e.g. ``fleisch-wurst``).

    Returns the SKU count, total forecast weekly volume, total forecast weekly
    revenue, total forecast weekly gross margin (EUR) and the volume-weighted
    average margin percentage, plus the per-product breakdown.
    """
    cat = category_id.strip().lower()
    products = [p for p in _products() if p["category_id"] == cat]
    if not products:
        return {"error": f"No products found for category '{category_id}'."}
    enriched = [_enrich(p) for p in products]
    volume = sum(float(p["expected_weekly_volume_units"]) for p in products)
    revenue = sum(e["weekly_revenue_forecast_eur"] for e in enriched)
    margin = sum(e["weekly_margin_forecast_eur"] for e in enriched)
    return {
        "category_id": cat,
        "sku_count": len(products),
        "weekly_volume_units": round(volume, 1),
        "weekly_revenue_forecast_eur": round(revenue, 2),
        "weekly_margin_forecast_eur": round(margin, 2),
        "avg_margin_percentage": round((margin / revenue) * 100, 2) if revenue else 0.0,
        "products": enriched,
    }


@mcp.tool()
def get_volume_forecast(query: str, proposed_shelf_price: Optional[float] = None,
                        persona_id: Optional[str] = None) -> dict[str, Any]:
    """Forecast weekly unit volume for a product, optionally at a new price.

    Uses a constant price-elasticity demand model. When ``proposed_shelf_price``
    is supplied the volume is re-forecast relative to the current price; a price
    cut increases volume and vice versa. When ``persona_id`` is supplied the
    persona's elasticity modifier scales the response (price-sensitive personas
    react more strongly).

    Args:
        query: Product SKU or name fragment.
        proposed_shelf_price: Optional new shelf price in EUR to evaluate.
        persona_id: Optional persona to weight the elasticity (see
            ``list_personas``).
    """
    product = _match_product(query)
    if product is None:
        return {"error": f"No internal product matched '{query}'."}
    modifier = 1.0
    if persona_id:
        persona = next((p for p in _personas() if p["persona_id"] == persona_id), None)
        if persona is None:
            return {"error": f"Unknown persona '{persona_id}'."}
        modifier = persona["elasticity_modifier"]
    baseline = _forecast_volume(product)
    projected = _forecast_volume(product, proposed_shelf_price, modifier)
    return {
        "sku": product["sku"],
        "product_name": product["product_name"],
        "current_shelf_price_eur": product["current_shelf_price_eur"],
        "proposed_shelf_price_eur": proposed_shelf_price,
        "price_elasticity": product["price_elasticity"],
        "elasticity_modifier": modifier,
        "baseline_weekly_volume_units": round(baseline, 1),
        "forecast_weekly_volume_units": round(projected, 1),
        "volume_change_units": round(projected - baseline, 1),
    }


@mcp.tool()
def simulate_price_change(query: str, proposed_shelf_price: float,
                          persona_id: Optional[str] = None) -> dict[str, Any]:
    """Simulate the margin impact of moving a product to a new shelf price.

    Combines the elasticity volume model with the margin calculation to show
    the trade-off between a lower price (more units, thinner unit margin) and
    the current price. Returns baseline vs proposed weekly volume, unit margin,
    weekly gross margin and the absolute and percentage margin delta.

    Args:
        query: Product SKU or name fragment.
        proposed_shelf_price: The new shelf price in EUR to evaluate.
        persona_id: Optional persona whose elasticity modifier weights demand.
    """
    product = _match_product(query)
    if product is None:
        return {"error": f"No internal product matched '{query}'."}
    modifier = 1.0
    if persona_id:
        persona = next((p for p in _personas() if p["persona_id"] == persona_id), None)
        if persona is None:
            return {"error": f"Unknown persona '{persona_id}'."}
        modifier = persona["elasticity_modifier"]

    base_price = product["current_shelf_price_eur"]
    base_volume = _forecast_volume(product)
    base_unit_margin = _unit_margin(product)
    base_margin = base_unit_margin * base_volume

    new_volume = _forecast_volume(product, proposed_shelf_price, modifier)
    new_unit_margin = _unit_margin(product, proposed_shelf_price)
    new_margin = new_unit_margin * new_volume

    return {
        "sku": product["sku"],
        "product_name": product["product_name"],
        "persona_id": persona_id,
        "baseline": {
            "shelf_price_eur": base_price,
            "weekly_volume_units": round(base_volume, 1),
            "unit_margin_eur": round(base_unit_margin, 4),
            "weekly_margin_eur": round(base_margin, 2),
        },
        "proposed": {
            "shelf_price_eur": proposed_shelf_price,
            "weekly_volume_units": round(new_volume, 1),
            "unit_margin_eur": round(new_unit_margin, 4),
            "weekly_margin_eur": round(new_margin, 2),
        },
        "weekly_margin_delta_eur": round(new_margin - base_margin, 2),
        "weekly_margin_delta_percentage": (
            round(((new_margin - base_margin) / base_margin) * 100, 2)
            if base_margin else 0.0
        ),
        "recommendation": (
            "accretive" if new_margin > base_margin else "dilutive"
        ),
    }


@mcp.tool()
def list_personas() -> list[dict[str, Any]]:
    """List shopping personas with price sensitivity and category affinities.

    Each persona carries an ``elasticity_modifier`` (>1 = more price sensitive)
    and the list of ``category_affinity`` ids it shops most. Use this to weight
    volume forecasts and to target promotions at the personas that drive the
    most incremental margin in a given category.
    """
    return _personas()


def main() -> None:
    """Entry point — serve the pricing data over streamable-HTTP MCP."""
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
