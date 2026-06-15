from __future__ import annotations

from typing import List

from src.shared.models import Item


def seed_items() -> List[Item]:
    return [
        Item(
            item_id="i-1",
            supplier_id="competitor-a-week-24",
            name="Bio Avocado",
            brand="Naturgut Bio",
            description_text="Frische Bio Avocado aus Spanien, Klasse I",
            category_id="vegetables",
            pricing_current_price=1.29,
            pricing_original_price=1.79,
            pricing_discount_percentage=27.9,
        ),
        Item(
            item_id="i-2",
            supplier_id="competitor-b-week-24",
            name="Milk Vollmilch 3.5%",
            brand="Milsani",
            description_text="1L Vollmilch, regional",
            category_id="dairy",
            pricing_current_price=0.95,
            pricing_original_price=1.09,
            pricing_discount_percentage=12.8,
        ),
        Item(
            item_id="i-3",
            supplier_id="competitor-b-week-24",
            name="Rinderhack 500g",
            brand="Meine Metzgerei",
            description_text="Frisches Rinderhackfleisch 500g",
            category_id="meat",
            pricing_current_price=3.49,
            pricing_original_price=4.49,
            pricing_discount_percentage=22.3,
        ),
    ]
