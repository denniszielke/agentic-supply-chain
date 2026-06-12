"""Delete all documents from the three Azure AI Search indexes.

Leaves the index schemas intact; only the stored documents are removed.

Environment variables required:
  AZURE_SEARCH_ENDPOINT                 - e.g. https://<service>.search.windows.net

Optional (with defaults matching create_search_index.py):
  AZURE_SEARCH_ADMIN_KEY                - Admin API key (falls back to DefaultAzureCredential)
  AZURE_SEARCH_SUPPLIER_INDEX_NAME      - default: retail-suppliers
  AZURE_SEARCH_CATEGORY_INDEX_NAME      - default: retail-categories
  AZURE_SEARCH_ITEM_INDEX_NAME          - default: retail-items
"""
from __future__ import annotations

import logging
import os

from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _get_search_client(index_name: str) -> SearchClient:
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
    if not endpoint:
        raise RuntimeError("AZURE_SEARCH_ENDPOINT is required")
    api_key = os.getenv("AZURE_SEARCH_ADMIN_KEY", "").strip()
    credential = AzureKeyCredential(api_key) if api_key else DefaultAzureCredential()
    return SearchClient(endpoint=endpoint, index_name=index_name, credential=credential)


def _delete_all_documents(index_name: str) -> None:
    """Fetch all document keys from *index_name* and delete them in batches."""
    client = _get_search_client(index_name)

    # Retrieve all document keys (field name 'id' is the key in all three indexes).
    results = client.search(search_text="*", select=["id"], top=1000)
    keys = [doc["id"] for doc in results]

    if not keys:
        print(f"Index '{index_name}': no documents found, nothing to delete.")
        return

    # Azure AI Search accepts up to 1 000 actions per batch.
    batch_size = 1000
    total_deleted = 0
    for i in range(0, len(keys), batch_size):
        batch = [{"id": k} for k in keys[i : i + batch_size]]
        result = client.delete_documents(documents=batch)
        failed = [r for r in result if not r.succeeded]
        if failed:
            for r in failed:
                logger.warning("Failed to delete key '%s' from '%s': %s", r.key, index_name, r.error_message)
        total_deleted += len(batch) - len(failed)

    print(f"Index '{index_name}': deleted {total_deleted} document(s).")
    logger.info("Index '%s': deleted %d document(s).", index_name, total_deleted)


def delete_all_index_data(
    supplier_index_name: str | None = None,
    category_index_name: str | None = None,
    item_index_name: str | None = None,
) -> None:
    supplier_index_name = supplier_index_name or os.getenv("AZURE_SEARCH_SUPPLIER_INDEX_NAME", "retail-suppliers")
    category_index_name = category_index_name or os.getenv("AZURE_SEARCH_CATEGORY_INDEX_NAME", "retail-categories")
    item_index_name = item_index_name or os.getenv("AZURE_SEARCH_ITEM_INDEX_NAME", "retail-items")

    for index_name in (supplier_index_name, category_index_name, item_index_name):
        _delete_all_documents(index_name)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    delete_all_index_data()


if __name__ == "__main__":
    main()
