"""Register the shopping toolbox in Azure AI Foundry.

Creates (or updates) a Foundry toolbox backed by three Azure AI Search tools —
one per retail index — so the hosted **shopping harness agent**
(``src/shopping_harness/agent.py``) can search suppliers, categories and items
through a single, centrally governed MCP endpoint.

The toolbox exposes three discoverable tools:

  * ``supplier-search``  → ``retail-suppliers``  (stores, locations, branches)
  * ``category-search``  → ``retail-categories`` (the product taxonomy)
  * ``item-search``      → ``retail-items``       (products, prices, promotions)

Run this **before** ``scripts/deploy_shopping_harness.py``.

Environment variables:
  AZURE_AI_PROJECT_ENDPOINT          Foundry project endpoint (required).
  AZURE_SEARCH_CONNECTION_NAME       Name of the AI Search connection in the
                                     Foundry project. Optional — if unset (or not
                                     found), the script auto-discovers the
                                     project's Azure AI Search connection. Falls
                                     back to AZURE_AI_SEARCH_CONNECTION_NAME.
                                     Visible in Foundry → Settings → Connections.
  SHOPPING_TOOLBOX_NAME              Toolbox name (default: shopping-tools).
  AZURE_SEARCH_SUPPLIER_INDEX_NAME   Supplier index (default: retail-suppliers).
  AZURE_SEARCH_CATEGORY_INDEX_NAME   Category index (default: retail-categories).
  AZURE_SEARCH_ITEM_INDEX_NAME       Item index (default: retail-items).
"""

from __future__ import annotations

import os

from azure.ai.projects.models import (
    AISearchIndexResource,
    AzureAISearchQueryType,
    AzureAISearchTool,
    AzureAISearchToolResource,
)

from scripts.deploy_helpers import get_client, get_env

TOOLBOX_NAME = os.getenv("SHOPPING_TOOLBOX_NAME", "shopping-tools")


def _resolve_search_connection(client) -> str:
    """Return the AI Search connection name to back the toolbox tools.

    Resolution order:
      1. ``AZURE_SEARCH_CONNECTION_NAME`` (explicit override), if it actually
         exists in the project.
      2. The first Azure AI Search connection discovered in the project.
      3. ``AZURE_AI_SEARCH_CONNECTION_NAME`` / ``AZURE_SEARCH_CONNECTION_NAME``
         as a last-resort literal (may be stale).

    Auto-discovery avoids the common failure where the azd-provided name
    (e.g. ``azure-ai-search-connection``) does not match the connection actually
    created in the project, which surfaces at runtime as
    ``Connection ... can't be found in this workspace``.
    """
    explicit = (
        os.getenv("AZURE_SEARCH_CONNECTION_NAME", "").strip()
        or os.getenv("AZURE_AI_SEARCH_CONNECTION_NAME", "").strip()
    )

    search_connections: list[str] = []
    try:
        for conn in client.connections.list():
            conn_type = str(getattr(conn, "type", "") or "")
            conn_name = getattr(conn, "name", None)
            if not conn_name:
                continue
            if explicit and conn_name == explicit:
                return conn_name
            if "AZURE_AI_SEARCH" in conn_type.upper():
                search_connections.append(conn_name)
    except Exception as exc:  # pragma: no cover - discovery is best-effort
        print(f"  (connection discovery failed: {exc})")

    if explicit:
        if search_connections:
            print(
                f"  Note: '{explicit}' not found; using discovered AI Search "
                f"connection '{search_connections[0]}'."
            )
            return search_connections[0]
        return explicit

    if search_connections:
        return search_connections[0]

    return "azure-ai-search-connection"


def _search_tool(
    *,
    name: str,
    description: str,
    connection_name: str,
    index_name: str,
    top_k: int = 10,
) -> AzureAISearchTool:
    """Build an AzureAISearchTool over a single retail index.

    Uses the ``SEMANTIC`` query type because the retail indexes have semantic
    configurations (with a default configuration) but no **integrated
    vectorizer** on their vector profiles — the supplier index has no vector
    field at all. ``VECTOR_SEMANTIC_HYBRID`` therefore fails at query time with
    "requires a vector field with integrated vectorizer, but none was found".
    Semantic ranking works across all three indexes without query vectorization.
    """
    return AzureAISearchTool(
        azure_ai_search=AzureAISearchToolResource(
            indexes=[
                AISearchIndexResource(
                    project_connection_id=connection_name,
                    index_name=index_name,
                    query_type=AzureAISearchQueryType.SEMANTIC,
                    top_k=top_k,
                )
            ]
        ),
        name=name,
        description=description,
    )


def deploy() -> None:
    if not os.getenv("AZURE_AI_PROJECT_ENDPOINT"):
        print("Skipping toolbox registration: AZURE_AI_PROJECT_ENDPOINT is required.")
        return

    client = get_client()
    connection_name = _resolve_search_connection(client)

    supplier_index = os.getenv("AZURE_SEARCH_SUPPLIER_INDEX_NAME", "retail-suppliers")
    category_index = os.getenv("AZURE_SEARCH_CATEGORY_INDEX_NAME", "retail-categories")
    item_index = os.getenv("AZURE_SEARCH_ITEM_INDEX_NAME", "retail-items")

    tools = [
        _search_tool(
            name="supplier-search",
            description=(
                "Search retail suppliers — supermarkets and discounters — along with "
                "their store locations, branches and opening details."
            ),
            connection_name=connection_name,
            index_name=supplier_index,
        ),
        _search_tool(
            name="category-search",
            description=(
                "Search the product category taxonomy to resolve a shopping need to "
                "the right category and discover alternative or related categories."
            ),
            connection_name=connection_name,
            index_name=category_index,
        ),
        _search_tool(
            name="item-search",
            description=(
                "Search retail items for concrete products with current price, original "
                "price, discount percentage, unit price, packaging, promotion type, "
                "supplier and offer validity dates across all ingested supplier flyers."
            ),
            connection_name=connection_name,
            index_name=item_index,
        ),
    ]

    version = client.beta.toolboxes.create_version(
        name=TOOLBOX_NAME,
        tools=tools,
        description=(
            "Shopping search toolbox backed by the Azure AI Search retail indexes. "
            "Exposes supplier, category and item search for the shopping harness agent."
        ),
        metadata={
            "source": "retail-search",
            "supplier_index": supplier_index,
            "category_index": category_index,
            "item_index": item_index,
        },
    )
    client.beta.toolboxes.update(name=TOOLBOX_NAME, default_version=version.version)

    project_endpoint = get_env("AZURE_AI_PROJECT_ENDPOINT")
    consumer_endpoint = (
        f"{project_endpoint.rstrip('/')}/toolboxes/{TOOLBOX_NAME}/mcp?api-version=v1"
    )
    print(f"Toolbox '{TOOLBOX_NAME}' version '{version.version}' created.")
    print(f"  AI Search connection: {connection_name}")
    print(f"  Supplier index: {supplier_index}")
    print(f"  Category index: {category_index}")
    print(f"  Item index: {item_index}")
    print(f"  Consumer endpoint: {consumer_endpoint}")


if __name__ == "__main__":
    deploy()
