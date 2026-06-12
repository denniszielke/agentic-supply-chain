"""Create Azure AI Search knowledge sources and a knowledge base for agentic retrieval.

Creates three knowledge sources (one per index) and assembles them into a single
knowledge base that can be queried via the Azure AI Search agentic retrieval API.

Environment variables required:
  AZURE_SEARCH_ENDPOINT                 - e.g. https://<service>.search.windows.net
  AZURE_OPENAI_ENDPOINT                 - Azure OpenAI resource endpoint
  AZURE_AI_MODEL_DEPLOYMENT_NAME        - Chat model deployment name (e.g. gpt-4.1-mini)

Optional (with defaults matching create_search_index.py):
  AZURE_SEARCH_ADMIN_KEY                - Admin API key (falls back to DefaultAzureCredential)
  AZURE_SEARCH_SUPPLIER_INDEX_NAME      - default: retail-suppliers
  AZURE_SEARCH_CATEGORY_INDEX_NAME      - default: retail-categories
  AZURE_SEARCH_ITEM_INDEX_NAME          - default: retail-items
  AZURE_SEARCH_KNOWLEDGE_BASE_NAME      - default: supply-chain-kb
"""
from __future__ import annotations

import logging
import os

from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    AzureOpenAIVectorizerParameters,
    KnowledgeBase,
    KnowledgeBaseAzureOpenAIModel,
    KnowledgeSourceReference,
    SearchIndexFieldReference,
    SearchIndexKnowledgeSource,
    SearchIndexKnowledgeSourceParameters,
)
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _get_index_client() -> SearchIndexClient:
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
    if not endpoint:
        raise RuntimeError("AZURE_SEARCH_ENDPOINT is required")
    api_key = os.getenv("AZURE_SEARCH_ADMIN_KEY", "").strip()
    credential = AzureKeyCredential(api_key) if api_key else DefaultAzureCredential()
    return SearchIndexClient(endpoint=endpoint, credential=credential)


# ── Knowledge source helpers ──────────────────────────────────────────────────

def _upsert_knowledge_source(
    client: SearchIndexClient,
    name: str,
    index_name: str,
    description: str,
    source_data_fields: list[str],
) -> None:
    """Create or update a searchIndex knowledge source wrapping an existing index."""
    ks = SearchIndexKnowledgeSource(
        name=name,
        description=description,
        search_index_parameters=SearchIndexKnowledgeSourceParameters(
            search_index_name=index_name,
            source_data_fields=[SearchIndexFieldReference(name=f) for f in source_data_fields],
        ),
    )
    client.create_or_update_knowledge_source(ks)
    logger.info("Knowledge source '%s' created/updated.", name)
    print(f"Knowledge source '{name}' created/updated (index: {index_name}).")


# ── Knowledge base ────────────────────────────────────────────────────────────

def _upsert_knowledge_base(
    client: SearchIndexClient,
    kb_name: str,
    knowledge_source_names: list[str],
    aoai_endpoint: str,
    model_deployment_name: str,
) -> None:
    """Create or update the knowledge base that aggregates all three knowledge sources."""
    aoai_params = AzureOpenAIVectorizerParameters(
        resource_url=aoai_endpoint,
        deployment_name=model_deployment_name,
        model_name=model_deployment_name,
    )

    kb = KnowledgeBase(
        name=kb_name,
        description=(
            "Agentic retrieval knowledge base for the supply-chain shopping assistant. "
            "Covers retail suppliers (store locations and opening hours), product categories "
            "(semantic groupings), and promotional offers (prices and discounts)."
        ),
        knowledge_sources=[KnowledgeSourceReference(name=n) for n in knowledge_source_names],
        models=[KnowledgeBaseAzureOpenAIModel(azure_open_ai_parameters=aoai_params)],
    )
    client.create_or_update_knowledge_base(kb)
    logger.info("Knowledge base '%s' created/updated.", kb_name)
    print(f"Knowledge base '{kb_name}' created/updated.")


# ── Entry point ───────────────────────────────────────────────────────────────

def create_or_update_knowledgebase(
    supplier_index_name: str | None = None,
    category_index_name: str | None = None,
    item_index_name: str | None = None,
    kb_name: str | None = None,
    aoai_endpoint: str | None = None,
    model_deployment_name: str | None = None,
) -> None:
    supplier_index_name = supplier_index_name or os.getenv("AZURE_SEARCH_SUPPLIER_INDEX_NAME", "retail-suppliers")
    category_index_name = category_index_name or os.getenv("AZURE_SEARCH_CATEGORY_INDEX_NAME", "retail-categories")
    item_index_name = item_index_name or os.getenv("AZURE_SEARCH_ITEM_INDEX_NAME", "retail-items")
    kb_name = kb_name or os.getenv("AZURE_SEARCH_KNOWLEDGE_BASE_NAME", "supply-chain-kb")
    aoai_endpoint = aoai_endpoint or os.getenv("AZURE_OPENAI_ENDPOINT")
    model_deployment_name = model_deployment_name or os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini")

    if not aoai_endpoint:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT is required")

    client = _get_index_client()

    # Knowledge source names derived from index names for clarity
    supplier_ks = f"{supplier_index_name}-ks"
    category_ks = f"{category_index_name}-ks"
    item_ks = f"{item_index_name}-ks"

    _upsert_knowledge_source(
        client,
        name=supplier_ks,
        index_name=supplier_index_name,
        description="Retail supplier store locations, opening hours, regional coverage, and flyer validity windows.",
        source_data_fields=[
            "id", "supplier_id", "brand", "store_name", "region",
            "opening_hours", "address_city", "address_country",
            "contact_phone", "contact_website",
        ],
    )
    _upsert_knowledge_source(
        client,
        name=category_ks,
        index_name=category_index_name,
        description="Normalized product categories with semantic tags enabling cross-retailer category queries.",
        source_data_fields=["id", "category_id", "name", "description_text", "semantic_tags"],
    )
    _upsert_knowledge_source(
        client,
        name=item_ks,
        index_name=item_index_name,
        description="Concrete promotional offers: products, prices, discounts, and packaging across all suppliers.",
        source_data_fields=[
            "id", "item_id", "name", "brand", "supplier_id", "category_id",
            "description_text",
            "pricing_current_price", "pricing_currency", "pricing_original_price",
            "pricing_discount_percentage", "pricing_unit_price", "pricing_unit_reference",
            "packaging_quantity", "packaging_unit_type", "packaging_packaging_type",
            "promotion_type", "promotion_bonus_amount", "promotion_coupon_required",
            "conditions_deposit", "conditions_availability",
            "offer_validity_start_date", "offer_validity_end_date",
        ],
    )

    _upsert_knowledge_base(
        client,
        kb_name=kb_name,
        knowledge_source_names=[supplier_ks, category_ks, item_ks],
        aoai_endpoint=aoai_endpoint,
        model_deployment_name=model_deployment_name,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    create_or_update_knowledgebase()


if __name__ == "__main__":
    main()
