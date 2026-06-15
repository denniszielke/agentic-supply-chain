"""Register the promotion toolbox in Azure AI Foundry.

Creates (or updates) a Foundry toolbox backed by an Azure AI Search tool that
queries the retail-items index.  The toolbox exposes promotion data —
``promotion_type``, ``pricing_current_price``, ``pricing_discount_percentage``,
``offer_validity_*``, and so on — as an MCP endpoint that the prompt-based
promotion agent can call at runtime.

Run this **before** ``scripts/deploy_promotion_agent.py``.

Environment variables:
  AZURE_AI_PROJECT_ENDPOINT        Foundry project endpoint (required).
  AZURE_SEARCH_CONNECTION_NAME     Name of the AI Search connection in the
                                   Foundry project (required).  Visible in
                                   Foundry → Settings → Connections.
  PROMOTION_TOOLBOX_NAME           Toolbox name (default: promotion-tools).
  AZURE_SEARCH_ITEM_INDEX_NAME     AI Search index to search over
                                   (default: retail-items).
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

TOOLBOX_NAME = os.getenv("PROMOTION_TOOLBOX_NAME", "promotion-tools")


def deploy() -> None:
    if not os.getenv("AZURE_AI_PROJECT_ENDPOINT"):
        print("Skipping toolbox registration: AZURE_AI_PROJECT_ENDPOINT is required.")
        return

    connection_name = os.getenv("AZURE_SEARCH_CONNECTION_NAME", "").strip()
    if not connection_name:
        print(
            "Skipping toolbox registration: set AZURE_SEARCH_CONNECTION_NAME to the "
            "name of the AI Search connection in your Foundry project "
            "(Foundry → Settings → Connections)."
        )
        return

    item_index_name = os.getenv("AZURE_SEARCH_ITEM_INDEX_NAME", "retail-items")

    tool = AzureAISearchTool(
        azure_ai_search=AzureAISearchToolResource(
            indexes=[
                AISearchIndexResource(
                    project_connection_id=connection_name,
                    index_name=item_index_name,
                    query_type=AzureAISearchQueryType.VECTOR_SEMANTIC_HYBRID,
                    top_k=10,
                )
            ]
        ),
        name="promotion-search",
        description=(
            "Search the retail items index for product promotions, current prices, "
            "discount percentages, offer validity dates and promotion types across "
            "all suppliers."
        ),
    )

    client = get_client()
    version = client.beta.toolboxes.create_version(
        name=TOOLBOX_NAME,
        tools=[tool],
        description=(
            "Promotion search toolbox backed by the Azure AI Search retail-items index. "
            "Surfaces promotion type, pricing, discounts and offer validity for every "
            "ingested supplier flyer."
        ),
        metadata={"source": "retail-items-search", "index": item_index_name},
    )
    client.beta.toolboxes.update(name=TOOLBOX_NAME, default_version=version.version)

    project_endpoint = get_env("AZURE_AI_PROJECT_ENDPOINT")
    consumer_endpoint = (
        f"{project_endpoint.rstrip('/')}/toolboxes/{TOOLBOX_NAME}/mcp?api-version=v1"
    )
    print(f"Toolbox '{TOOLBOX_NAME}' version '{version.version}' created.")
    print(f"  AI Search connection: {connection_name}")
    print(f"  Index: {item_index_name}")
    print(f"  Consumer endpoint: {consumer_endpoint}")


if __name__ == "__main__":
    deploy()
