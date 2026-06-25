"""Deploy the shopping harness agent as an Azure AI Foundry hosted agent.

Deploys the **shopping harness agent** (``src/shopping_harness/agent.py``) as a
Foundry hosted agent (RESPONSES protocol). It consumes the shopping search
toolbox registered in ``scripts/register_shopping_toolbox.py``
(``SHOPPING_TOOLBOX_NAME``), so run this only after that toolbox exists.

The agent is exposed via all three Foundry protocols:
  - RESPONSES   (OpenAI Responses API-compatible)
  - A2A         (Agent-to-Agent, JSON card at /agentCard/v0.3)
  - INVOCATIONS (direct invocation API)

Environment variables:
  AZURE_AI_PROJECT_ENDPOINT           Foundry project endpoint (required).
  AZURE_CONTAINER_REGISTRY_ENDPOINT   ACR login server for the agent image (required).
  AZURE_AI_SHOPPING_HARNESS_AGENT_NAME  Hosted agent name (default: shopping-harness).
  SHOPPING_TOOLBOX_NAME               Toolbox the agent consumes (default: shopping-tools).
  TOOLBOX_MCP_ENDPOINT                Explicit toolbox MCP URL (optional override).
  SHOPPING_MCP_URL                    Direct MCP URL to bypass the toolbox (optional).
"""

from __future__ import annotations

import os

from scripts.deploy_helpers import AgentCard, AgentCardSkill, deploy_hosted_agent, get_client


SHOPPING_HARNESS_AGENT_CARD = AgentCard(
    version="1.0",
    description=(
        "Shopping harness agent that helps a shopper decide what to buy, where, and at "
        "what price by searching live supplier, category and item retail data."
    ),
    skills=[
        AgentCardSkill(
            id="supplier-search",
            name="Supplier Search",
            description="Find supermarkets and discounters with their store locations and branches.",
        ),
        AgentCardSkill(
            id="category-search",
            name="Category Search",
            description="Resolve a shopping need to the right product category and discover alternatives.",
        ),
        AgentCardSkill(
            id="item-search",
            name="Item Search",
            description="Look up concrete products with current price, discount, promotion type and offer validity.",
        ),
    ],
)


def deploy() -> None:
    project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT")
    registry = os.getenv("AZURE_CONTAINER_REGISTRY_ENDPOINT")
    if not project_endpoint or not registry:
        print(
            "Skipping shopping harness deployment: AZURE_AI_PROJECT_ENDPOINT and "
            "AZURE_CONTAINER_REGISTRY_ENDPOINT are required."
        )
        return

    client = get_client()
    deploy_hosted_agent(
        client,
        agent_name=os.getenv("AZURE_AI_SHOPPING_HARNESS_AGENT_NAME", "shopping-harness"),
        description="Shopping harness hosted agent",
        registry=registry,
        project_endpoint=project_endpoint,
        dockerfile_rel="src/shopping_harness/Dockerfile",
        extra_env={
            "SHOPPING_TOOLBOX_NAME": os.getenv("SHOPPING_TOOLBOX_NAME", "shopping-tools"),
            "TOOLBOX_MCP_ENDPOINT": os.getenv("TOOLBOX_MCP_ENDPOINT", ""),
            "SHOPPING_MCP_URL": os.getenv("SHOPPING_MCP_URL", ""),
        },
        agent_card=SHOPPING_HARNESS_AGENT_CARD,
    )


if __name__ == "__main__":
    deploy()
