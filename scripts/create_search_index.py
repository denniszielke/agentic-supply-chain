from __future__ import annotations

import logging
import os

from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
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


def _semantic_search(
    config_name: str,
    title_field: str | None,
    content_fields: list[str],
    keyword_fields: list[str],
) -> SemanticSearch:
    return SemanticSearch(
        default_configuration_name=config_name,
        configurations=[
            SemanticConfiguration(
                name=config_name,
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name=title_field) if title_field else None,
                    content_fields=[SemanticField(field_name=f) for f in content_fields],
                    keywords_fields=[SemanticField(field_name=f) for f in keyword_fields],
                ),
            )
        ],
    )


# ── Supplier ──────────────────────────────────────────────────────────────────

def _build_supplier_fields() -> list:
    """Fields for the supplier index.

    Represents a single flyer context instance (store, region, validity window,
    opening hours, contact, and ingestion metadata). All fields are flat scalars
    or string collections — no composite (ComplexField) types — so the index can
    be surfaced directly through the agentic-retrieval knowledge base.
    """
    return [
        SearchField(name="id", type=SearchFieldDataType.String, key=True),
        SearchField(name="supplier_id", type=SearchFieldDataType.String, filterable=True),
        SearchField(name="brand", type=SearchFieldDataType.String, searchable=True, filterable=True, facetable=True),
        SearchField(name="store_name", type=SearchFieldDataType.String, searchable=True, filterable=True),
        SearchField(name="region", type=SearchFieldDataType.String, searchable=True, filterable=True, facetable=True),
        SearchField(name="address_street", type=SearchFieldDataType.String, searchable=True),
        SearchField(name="address_city", type=SearchFieldDataType.String, searchable=True, filterable=True, facetable=True),
        SearchField(name="address_postal_code", type=SearchFieldDataType.String, searchable=True, filterable=True),
        SearchField(name="address_country", type=SearchFieldDataType.String, searchable=True, filterable=True, facetable=True),
        SearchField(name="address_geo_lat", type=SearchFieldDataType.Double, filterable=True),
        SearchField(name="address_geo_lon", type=SearchFieldDataType.Double, filterable=True),
        SearchField(
            name="opening_hours",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
            searchable=True,
            filterable=True,
        ),
        SearchField(name="contact_phone", type=SearchFieldDataType.String, searchable=True),
        SearchField(name="contact_website", type=SearchFieldDataType.String, searchable=True),
    ]


# ── Category ──────────────────────────────────────────────────────────────────

def _build_category_fields() -> list:
    """Fields for the category index.

    Flat category representation. Hierarchy is captured via embeddings rather
    than explicit parent references, enabling cross-retailer semantic comparison.
    """
    return [
        SearchField(name="id", type=SearchFieldDataType.String, key=True),
        SearchField(name="category_id", type=SearchFieldDataType.String, filterable=True),
        SearchField(name="name", type=SearchFieldDataType.String, searchable=True, filterable=True),
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
    context (flyer and timeframe). Product attributes, pricing, and promotion
    data are stored as flat ``<group>_<field>`` scalars — no composite types —
    so every field can be surfaced through the agentic-retrieval knowledge base.
    """
    return [
        SearchField(name="id", type=SearchFieldDataType.String, key=True),
        SearchField(name="item_id", type=SearchFieldDataType.String, filterable=True),
        SearchField(name="supplier_id", type=SearchFieldDataType.String, filterable=True),
        SearchField(name="name", type=SearchFieldDataType.String, searchable=True, filterable=True),
        SearchField(name="brand", type=SearchFieldDataType.String, searchable=True, filterable=True, facetable=True),
        SearchField(name="description_text", type=SearchFieldDataType.String, searchable=True),
        SearchField(name="category_id", type=SearchFieldDataType.String, filterable=True),
        SearchField(name="source_ref", type=SearchFieldDataType.String, searchable=True, filterable=True),
        # attributes_*
        SearchField(name="attributes_origin", type=SearchFieldDataType.String, searchable=True, filterable=True, facetable=True),
        SearchField(name="attributes_quality_grade", type=SearchFieldDataType.String, searchable=True, filterable=True),
        SearchField(name="attributes_bio", type=SearchFieldDataType.Boolean, filterable=True, facetable=True),
        SearchField(name="attributes_animal_welfare", type=SearchFieldDataType.String, searchable=True, filterable=True),
        # packaging_*
        SearchField(name="packaging_unit_type", type=SearchFieldDataType.String, searchable=True, filterable=True, facetable=True),
        SearchField(name="packaging_quantity", type=SearchFieldDataType.Double, filterable=True, sortable=True),
        SearchField(name="packaging_packaging_type", type=SearchFieldDataType.String, searchable=True, filterable=True, facetable=True),
        # pricing_*
        SearchField(name="pricing_current_price", type=SearchFieldDataType.Double, filterable=True, sortable=True),
        SearchField(name="pricing_currency", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchField(name="pricing_original_price", type=SearchFieldDataType.Double, filterable=True, sortable=True),
        SearchField(name="pricing_discount_percentage", type=SearchFieldDataType.Double, filterable=True, sortable=True),
        SearchField(name="pricing_unit_price", type=SearchFieldDataType.Double, filterable=True, sortable=True),
        SearchField(name="pricing_unit_reference", type=SearchFieldDataType.String, filterable=True, facetable=True),
        # promotion_*
        SearchField(name="promotion_type", type=SearchFieldDataType.String, searchable=True, filterable=True, facetable=True),
        SearchField(name="promotion_bonus_amount", type=SearchFieldDataType.Double, filterable=True, sortable=True),
        SearchField(name="promotion_coupon_required", type=SearchFieldDataType.Boolean, filterable=True, facetable=True),
        # conditions_*
        SearchField(name="conditions_deposit", type=SearchFieldDataType.Double, filterable=True, sortable=True),
        SearchField(name="conditions_availability", type=SearchFieldDataType.String, searchable=True, filterable=True),
        # offer_validity_*
        SearchField(name="offer_validity_start_date", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
        SearchField(name="offer_validity_end_date", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
        SearchField(
            name="embedding",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            vector_search_dimensions=EMBEDDING_DIMENSIONS,
            vector_search_profile_name="hnsw",
        ),
    ]


# ── Index management ──────────────────────────────────────────────────────────

def _upsert_index(
    index_client: SearchIndexClient,
    name: str,
    fields: list,
    with_vector_search: bool = False,
    semantic_search: SemanticSearch | None = None,
) -> None:
    kwargs = {"vector_search": _hnsw_vector_search()} if with_vector_search else {}
    if semantic_search is not None:
        kwargs["semantic_search"] = semantic_search
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
    _upsert_index(
        index_client,
        supplier_index_name,
        _build_supplier_fields(),
        with_vector_search=False,
        semantic_search=_semantic_search(
            config_name="supplier-semantic",
            title_field="store_name",
            content_fields=["region"],
            keyword_fields=["brand"],
        ),
    )
    _upsert_index(
        index_client,
        category_index_name,
        _build_category_fields(),
        with_vector_search=True,
        semantic_search=_semantic_search(
            config_name="category-semantic",
            title_field="name",
            content_fields=["description_text"],
            keyword_fields=["semantic_tags"],
        ),
    )
    _upsert_index(
        index_client,
        item_index_name,
        _build_item_fields(),
        with_vector_search=True,
        semantic_search=_semantic_search(
            config_name="item-semantic",
            title_field="name",
            content_fields=["description_text"],
            keyword_fields=["brand"],
        ),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    create_or_update_indexes()


if __name__ == "__main__":
    main()
