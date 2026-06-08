from __future__ import annotations

from typing import List

from src.shared.models import Item, Pricing


def seed_items() -> List[Item]:
    return [
        Item(
            item_id="i-1",
            supplier_id="rewe-berlin-week-24",
            name="Bio Avocado",
            brand="REWE Bio",
            description_text="Frische Bio Avocado aus Spanien, Klasse I",
            category_id="vegetables",
            pricing=Pricing(current_price=1.29, original_price=1.79, discount_percentage=27.9),
        ),
        Item(
            item_id="i-2",
            supplier_id="aldi-berlin-week-24",
            name="Milk Vollmilch 3.5%",
            brand="Milsani",
            description_text="1L Vollmilch, regional",
            category_id="dairy",
            pricing=Pricing(current_price=0.95, original_price=1.09, discount_percentage=12.8),
        ),
        Item(
            item_id="i-3",
            supplier_id="aldi-berlin-week-24",
            name="Rinderhack 500g",
            brand="Meine Metzgerei",
            description_text="Frisches Rinderhackfleisch 500g",
            category_id="meat",
            pricing=Pricing(current_price=3.49, original_price=4.49, discount_percentage=22.3),
        ),
    ]
