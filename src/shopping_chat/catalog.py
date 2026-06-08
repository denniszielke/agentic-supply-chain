from __future__ import annotations

from typing import List

from src.shared.models import Item


class CatalogService:
    def __init__(self, items: List[Item]) -> None:
        self._items = items

    def search_by_description(self, query: str) -> List[Item]:
        query_lower = query.lower().strip()
        if not query_lower:
            return []
        return [
            item
            for item in self._items
            if query_lower in item.description_text.lower() or query_lower in item.name.lower()
        ]

    def recommend_by_category(self, category_id: str, limit: int = 5) -> List[Item]:
        results = [item for item in self._items if item.category_id == category_id]
        return sorted(results, key=lambda item: item.pricing.current_price)[:limit]

    def inventory_by_supplier(self, supplier_id: str) -> List[Item]:
        return [item for item in self._items if item.supplier_id == supplier_id]
