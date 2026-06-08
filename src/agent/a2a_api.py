from __future__ import annotations

from typing import List

from fastapi import FastAPI
from pydantic import BaseModel

from src.agent.shopping_agent import ShoppingPlannerAgent
from src.shared.planner import ShoppingRequest
from src.shared.seed_data import seed_items

app = FastAPI(title="shopping-planner-agent")
agent = ShoppingPlannerAgent(seed_items())


class ShoppingListLine(BaseModel):
    product: str
    quantity: int = 1


class PlanningRequest(BaseModel):
    shopping_list: List[ShoppingListLine]


@app.get("/a2a/capabilities")
def capabilities():
    return {
        "agent": "shopping-planner-agent",
        "protocol": "a2a-http",
        "capabilities": ["shopping-plan", "promotion-aware-selection", "supplier-fit"],
    }


@app.post("/a2a/plan")
def plan(req: PlanningRequest):
    shopping_list = [ShoppingRequest(product=line.product, quantity=line.quantity) for line in req.shopping_list]
    plan_result = agent.plan(shopping_list)
    return {
        "total_cost": plan_result.total_cost,
        "lines": [
            {
                "product": line.product,
                "supplier_id": line.supplier_id,
                "item_id": line.chosen_item_id,
                "quantity": line.quantity,
                "unit_price": line.unit_price,
                "estimated_cost": line.estimated_cost,
            }
            for line in plan_result.lines
        ],
    }
