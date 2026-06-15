from __future__ import annotations

import os

from scripts.deploy_helpers import AgentCard, AgentCardSkill, deploy_hosted_agent, get_client


SHOPPING_AGENT_CARD = AgentCard(
    version="1.0",
    description="Shopping planner agent that optimises a shopping list across current supermarket promotions.",
    skills=[
        AgentCardSkill(
            id="shopping-optimisation",
            name="Shopping List Optimisation",
            description="Find the best-value combination of stores and products for a given shopping list based on weekly promotions.",
        ),
        AgentCardSkill(
            id="promotion-search",
            name="Promotion Search",
            description="Search current promotions across indexed suppliers for specific products or categories.",
        ),
    ],
)

CAMPAIGN_AGENT_CARD = AgentCard(
    version="1.0",
    description=(
        "Campaign planning agent for retail marketing teams. Reasons about margin "
        "optimisation vs. competitor promotions, per product category and shopping persona."
    ),
    skills=[
        AgentCardSkill(
            id="margin-optimisation",
            name="Margin Optimisation",
            description="Analyse internal procurement cost and weekly volume forecasts to recommend margin-preserving promotion strategies.",
        ),
        AgentCardSkill(
            id="competitor-analysis",
            name="Competitor Promotion Analysis",
            description="Compare current competitor promotions from the AI Search index and identify pricing gaps or opportunities.",
        ),
        AgentCardSkill(
            id="persona-targeting",
            name="Shopping Persona Targeting",
            description="Tailor campaign recommendations to specific shopping personas (e.g. budget-conscious, premium-seeker).",
        ),
    ],
)


def deploy() -> None:
    project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT")
    registry = os.getenv("AZURE_CONTAINER_REGISTRY_ENDPOINT")
    if not project_endpoint or not registry:
        print(
            "Skipping hosted agent deployment: AZURE_AI_PROJECT_ENDPOINT and "
            "AZURE_CONTAINER_REGISTRY_ENDPOINT are required."
        )
        return

    client = get_client()

    # Shopping planner hosted agent.
    deploy_hosted_agent(
        client,
        agent_name=os.getenv("AZURE_AI_HOSTED_AGENT_NAME", "shopping-agent"),
        description="Shopping planner hosted agent",
        registry=registry,
        project_endpoint=project_endpoint,
        dockerfile_rel="src/shopping_agent/Dockerfile",
        agent_card=SHOPPING_AGENT_CARD,
    )

    # Campaign planning hosted agent.
    deploy_hosted_agent(
        client,
        agent_name=os.getenv("AZURE_AI_CAMPAIGN_AGENT_NAME", "campaign-agent"),
        description="Campaign planning hosted agent",
        registry=registry,
        project_endpoint=project_endpoint,
        dockerfile_rel="src/campaign_agent/Dockerfile",
        extra_env={
            "PRICING_TOOLBOX_NAME": os.getenv("PRICING_TOOLBOX_NAME", "pricing-tools"),
            "TOOLBOX_MCP_ENDPOINT": os.getenv("TOOLBOX_MCP_ENDPOINT", ""),
            "PRICING_MCP_URL": os.getenv("PRICING_MCP_URL", ""),
        },
        agent_card=CAMPAIGN_AGENT_CARD,
    )


if __name__ == "__main__":
    deploy()
