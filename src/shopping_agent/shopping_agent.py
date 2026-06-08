from __future__ import annotations

from typing import List

from src.shared.models import Item
from src.shared.planner import ShoppingRequest, build_shopping_plan


class ShoppingPlannerAgent:
    def __init__(self, offers: List[Item]) -> None:
        self._offers = offers

    def plan(self, shopping_list: List[ShoppingRequest]):
        return build_shopping_plan(shopping_list, self._offers)
