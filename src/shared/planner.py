from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

from src.shared.models import Item


@dataclass(frozen=True)
class ShoppingRequest:
    product: str
    quantity: int = 1


@dataclass(frozen=True)
class PlannedLine:
    product: str
    supplier_id: str
    chosen_item_id: str
    quantity: int
    unit_price: float
    estimated_cost: float


@dataclass(frozen=True)
class ShoppingPlan:
    total_cost: float
    lines: List[PlannedLine]


def _match_item(product: str, items: Iterable[Item]) -> Item | None:
    product_lower = product.lower()
    scored = [
        i
        for i in items
        if product_lower in i.name.lower() or product_lower in i.description_text.lower()
    ]
    if not scored:
        return None
    return sorted(scored, key=lambda i: i.pricing.current_price)[0]


def build_shopping_plan(requests: List[ShoppingRequest], catalog: List[Item]) -> ShoppingPlan:
    plan_lines: List[PlannedLine] = []
    for request in requests:
        best_item = _match_item(request.product, catalog)
        if not best_item:
            continue
        unit = best_item.pricing.current_price
        plan_lines.append(
            PlannedLine(
                product=request.product,
                supplier_id=best_item.supplier_id,
                chosen_item_id=best_item.item_id,
                quantity=max(request.quantity, 1),
                unit_price=unit,
                estimated_cost=unit * max(request.quantity, 1),
            )
        )

    total = round(sum(line.estimated_cost for line in plan_lines), 2)
    return ShoppingPlan(total_cost=total, lines=plan_lines)
