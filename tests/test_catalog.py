import unittest

from src.shopping_chat.catalog import CatalogService
from src.shared.models import Item


class CatalogServiceTests(unittest.TestCase):
    def test_search_by_description(self):
        service = CatalogService(
            [
                Item(
                    item_id="1",
                    supplier_id="s1",
                    name="Bio Avocado",
                    description_text="Fresh avocado from Spain",
                    category_id="vegetables",
                    pricing_current_price=1.2,
                )
            ]
        )

        result = service.search_by_description("avocado")
        self.assertEqual(1, len(result))
        self.assertEqual("1", result[0].item_id)


if __name__ == "__main__":
    unittest.main()
