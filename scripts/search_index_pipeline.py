from __future__ import annotations

import logging
import os

from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    ComplexField,
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    VectorSearch,
    VectorSearchProfile,
)
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

EMBEDDING_DIMENSIONS = int(os.getenv("AZURE_OPENAI_EMBEDDING_DIMENSIONS", "1536"))


def _get_index_client() -> SearchIndexClient:
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
    if not endpoint:
        raise RuntimeError("AZURE_SEARCH_ENDPOINT is required")
    api_key = os.getenv("AZURE_SEARCH_ADMIN_KEY", "").strip()
    credential = AzureKeyCredential(api_key) if api_key else DefaultAzureCredential()
    return SearchIndexClient(endpoint=endpoint, credential=credential)


def _hnsw_vector_search() -> VectorSearch:
    return VectorSearch(
        profiles=[VectorSearchProfile(name="hnsw", algorithm_configuration_name="hnsw")],
        algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
    )


# ── Supplier ──────────────────────────────────────────────────────────────────

def _build_supplier_fields() -> list:
    """Fields for the supplier index.

    Represents a single flyer context instance (store, region, validity window,
    opening hours, contact, and ingestion metadata).
    """
    return [
        SearchField(name="id", type=SearchFieldDataType.String, key=True),
        SearchField(name="supplier_id", type=SearchFieldDataType.String, filterable=True),
        SearchField(name="brand", type=SearchFieldDataType.String, searchable=True, filterable=True, facetable=True),
        SearchField(name="store_name", type=SearchFieldDataType.String, searchable=True, filterable=True),
        ComplexField(
            name="address",
            fields=[
                SearchField(name="street", type=SearchFieldDataType.String, searchable=True),
                SearchField(name="city", type=SearchFieldDataType.String, searchable=True, filterable=True, facetable=True),
                SearchField(name="postal_code", type=SearchFieldDataType.String, searchable=True, filterable=True),
                SearchField(name="country", type=SearchFieldDataType.String, searchable=True, filterable=True, facetable=True),
                ComplexField(
                    name="geo",
                    fields=[
                        SearchField(name="lat", type=SearchFieldDataType.Double, filterable=True),
                        SearchField(name="lon", type=SearchFieldDataType.Double, filterable=True),
                    ],
                ),
            ],
        ),
        ComplexField(
            name="opening_hours",
            collection=True,
            fields=[
                SearchField(name="day", type=SearchFieldDataType.String, searchable=True, filterable=True),
                SearchField(name="open", type=SearchFieldDataType.String),
                SearchField(name="close", type=SearchFieldDataType.String),
            ],
        ),
        SearchField(name="region", type=SearchFieldDataType.String, searchable=True, filterable=True, facetable=True),
        ComplexField(
            name="contact",
            fields=[
                SearchField(name="phone", type=SearchFieldDataType.String, searchable=True),
                SearchField(name="website", type=SearchFieldDataType.String, searchable=True),
            ],
        ),
        ComplexField(
            name="offer_validity",
            fields=[
                SearchField(name="start_date", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
                SearchField(name="end_date", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
            ],
        ),
        ComplexField(
            name="ingestion_metadata",
            fields=[
                SearchField(name="source_document", type=SearchFieldDataType.String, searchable=True),
                SearchField(name="ingestion_timestamp", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
            ],
        ),
    ]


# ── Category ──────────────────────────────────────────────────────────────────

def _build_category_fields() -> list:
    """Fields for the category index.

    Represents a normalized semantic grouping of items, independent of flyer
    layouts, enabling cross-retailer queries.
    """
    return [
        SearchField(name="id", type=SearchFieldDataType.String, key=True),
        SearchField(name="category_id", type=SearchFieldDataType.String, filterable=True),
        SearchField(name="name", type=SearchFieldDataType.String, searchable=True, filterable=True),
        SearchField(name="parent_category_id", type=SearchFieldDataType.String, filterable=True),
        SearchField(name="description_text", type=SearchFieldDataType.String, searchable=True),
        SearchField(
            name="semantic_tags",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
            searchable=True,
            filterable=True,
            facetable=True,
        ),
        SearchField(
            name="embedding",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            vector_search_dimensions=EMBEDDING_DIMENSIONS,
            vector_search_profile_name="hnsw",
        ),
    ]


# ── Item ──────────────────────────────────────────────────────────────────────

def _build_item_fields() -> list:
    """Fields for the item index.

    Represents a concrete offer instance of a product within a specific supplier
    context (flyer and timeframe). Combines product attributes, pricing, and
    promotion data into a single searchable object.
    """
    return [
        SearchField(name="id", type=SearchFieldDataType.String, key=True),
        SearchField(name="item_id", type=SearchFieldDataType.String, filterable=True),
        SearchField(name="supplier_id", type=SearchFieldDataType.String, filterable=True),
        SearchField(name="name", type=SearchFieldDataType.String, searchable=True, filterable=True),
        SearchField(name="brand", type=SearchFieldDataType.String, searchable=True, filterable=True, facetable=True),
        SearchField(name="description_text", type=SearchFieldDataType.String, searchable=True),
        SearchField(name="category_id", type=SearchFieldDataType.String, filterable=True),
        ComplexField(
            name="attributes",
            fields=[
                SearchField(name="origin", type=SearchFieldDataType.String, searchable=True, filterable=True, facetable=True),
                SearchField(name="quality_grade", type=SearchFieldDataType.String, searchable=True, filterable=True),
                SearchField(name="bio", type=SearchFieldDataType.Boolean, filterable=True, facetable=True),
                SearchField(name="animal_welfare", type=SearchFieldDataType.String, searchable=True, filterable=True),
            ],
        ),
        ComplexField(
            name="packaging",
            fields=[
                SearchField(name="unit_type", type=SearchFieldDataType.String, searchable=True, filterable=True, facetable=True),
                SearchField(name="quantity", type=SearchFieldDataType.Double, filterable=True, sortable=True),
                SearchField(name="packaging_type", type=SearchFieldDataType.String, searchable=True, filterable=True, facetable=True),
            ],
        ),
        ComplexField(
            name="pricing",
            fields=[
                SearchField(name="current_price", type=SearchFieldDataType.Double, filterable=True, sortable=True),
                SearchField(name="currency", type=SearchFieldDataType.String, filterable=True, facetable=True),
                SearchField(name="original_price", type=SearchFieldDataType.Double, filterable=True, sortable=True),
                SearchField(name="discount_percentage", type=SearchFieldDataType.Double, filterable=True, sortable=True),
                SearchField(name="unit_price", type=SearchFieldDataType.Double, filterable=True, sortable=True),
                SearchField(name="unit_reference", type=SearchFieldDataType.String, filterable=True, facetable=True),
            ],
        ),
        ComplexField(
            name="promotion",
            fields=[
                SearchField(name="type", type=SearchFieldDataType.String, searchable=True, filterable=True, facetable=True),
                SearchField(name="bonus_amount", type=SearchFieldDataType.Double, filterable=True, sortable=True),
                SearchField(name="coupon_required", type=SearchFieldDataType.Boolean, filterable=True, facetable=True),
            ],
        ),
        ComplexField(
            name="conditions",
            fields=[
                SearchField(name="deposit", type=SearchFieldDataType.Double, filterable=True, sortable=True),
                SearchField(name="availability", type=SearchFieldDataType.String, searchable=True, filterable=True),
            ],
        ),
        SearchField(
            name="embedding",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            vector_search_dimensions=EMBEDDING_DIMENSIONS,
            vector_search_profile_name="hnsw",
        ),
    ]


# ── Index management ──────────────────────────────────────────────────────────

def _upsert_index(index_client: SearchIndexClient, name: str, fields: list, with_vector_search: bool = False) -> None:
    kwargs = {"vector_search": _hnsw_vector_search()} if with_vector_search else {}
    index = SearchIndex(name=name, fields=fields, **kwargs)
    result = index_client.create_or_update_index(index)
    logger.info("Index '%s' created/updated.", result.name)
    print(f"Index '{result.name}' created/updated.")


def create_or_update_indexes(
    supplier_index_name: str | None = None,
    category_index_name: str | None = None,
    item_index_name: str | None = None,
) -> None:
    supplier_index_name = supplier_index_name or os.getenv("AZURE_SEARCH_SUPPLIER_INDEX_NAME", "retail-suppliers")
    category_index_name = category_index_name or os.getenv("AZURE_SEARCH_CATEGORY_INDEX_NAME", "retail-categories")
    item_index_name = item_index_name or os.getenv("AZURE_SEARCH_ITEM_INDEX_NAME", "retail-items")

    index_client = _get_index_client()
    _upsert_index(index_client, supplier_index_name, _build_supplier_fields(), with_vector_search=False)
    _upsert_index(index_client, category_index_name, _build_category_fields(), with_vector_search=True)
    _upsert_index(index_client, item_index_name, _build_item_fields(), with_vector_search=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    create_or_update_indexes()


if __name__ == "__main__":
    main()
