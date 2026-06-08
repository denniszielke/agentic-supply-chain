import unittest

from src.shared.models import Item, Pricing
from src.shared.planner import ShoppingRequest, build_shopping_plan


class ShoppingPlannerTests(unittest.TestCase):
    def test_picks_lowest_price_match(self):
        items = [
            Item(
                item_id="a",
                supplier_id="store-a",
                name="Milk 1L",
                description_text="Whole milk",
                category_id="dairy",
                pricing=Pricing(current_price=1.29),
            ),
            Item(
                item_id="b",
                supplier_id="store-b",
                name="Milk 1L",
                description_text="Whole milk promo",
                category_id="dairy",
                pricing=Pricing(current_price=0.99),
            ),
        ]

        plan = build_shopping_plan([ShoppingRequest(product="milk", quantity=2)], items)

        self.assertEqual(1, len(plan.lines))
        self.assertEqual("store-b", plan.lines[0].supplier_id)
        self.assertEqual(1.98, plan.total_cost)


if __name__ == "__main__":
    unittest.main()
