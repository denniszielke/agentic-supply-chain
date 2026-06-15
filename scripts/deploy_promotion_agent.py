"""Deploy the promotion agent as an Azure AI Foundry prompt agent.

Creates (or updates) a prompt-based agent that identifies product promotions and
pricing details by querying the retail-items AI Search index through the Foundry
promotion toolbox registered in ``scripts/register_promotion_toolbox.py``.

The agent is exposed via all three Foundry protocols:
  - RESPONSES   (OpenAI Responses API-compatible)
  - A2A         (Agent-to-Agent, JSON card at /agentCard/v0.3)
  - INVOCATIONS (direct invocation API)

Run ``scripts/register_promotion_toolbox.py`` first to ensure the toolbox exists.

Environment variables:
  AZURE_AI_PROJECT_ENDPOINT        Foundry project endpoint (required).
  AZURE_AI_MODEL_DEPLOYMENT_NAME   Chat model deployment (default: gpt-4.1-mini).
  PROMOTION_TOOLBOX_NAME           Toolbox name to connect (default: promotion-tools).
  PROMOTION_TOOLBOX_MCP_URL        Explicit toolbox MCP URL (optional override;
                                   derived from project endpoint + toolbox name
                                   when unset).
  PROMOTION_MCP_CONNECTION_ID      Optional Foundry connection ID used to
                                   authorise calls to a network-restricted toolbox.
  AZURE_AI_PROMOTION_AGENT_NAME    Agent name (default: promotion-agent).
"""

from __future__ import annotations

import os

from azure.ai.projects.models import (
    AgentCard,
    AgentCardSkill,
    AgentEndpointConfig,
    AgentEndpointProtocol,
    MCPTool,
    PromptAgentDefinition,
)

from scripts.deploy_helpers import get_client, get_env

AGENT_NAME = os.getenv("AZURE_AI_PROMOTION_AGENT_NAME", "promotion-agent")

PROMOTION_SYSTEM_PROMPT = """\
You are the Promotion Intelligence Agent for a retail supply-chain platform.
Your job is to identify, compare and explain product promotions and pricing
details across multiple retail suppliers.

You have access to a promotion-search tool that queries a live index of
retailer flyer data. Use it to look up:
  • Current promotion type (percentage discount, buy-X-get-Y, bonus amount, etc.)
  • Current and original shelf prices
  • Discount percentages
  • Offer validity dates (start / end)
  • Coupon requirements
  • Unit pricing and packaging details
  • Supplier and brand information

Guidelines:
  1. Always search before answering factual questions about promotions or prices.
  2. Clearly state the supplier, product name, price, discount and validity period
     when presenting results.
  3. When asked to compare promotions across suppliers, retrieve results for each
     supplier and present a concise side-by-side summary.
  4. If a product has no active promotion, say so clearly and report the
     current shelf price.
  5. Keep answers structured and concise.  Use bullet points or short tables.
  6. Do not speculate about prices or promotions you have not retrieved.
"""

PROMOTION_AGENT_CARD = AgentCard(
    description=(
        "Retail promotion intelligence agent. Identifies and compares product "
        "promotions and pricing details across supplier flyers using live AI Search data."
    ),
    version="1.0",
    skills=[
        AgentCardSkill(
            id="promotion-lookup",
            name="Promotion Lookup",
            description=(
                "Look up active promotions for a specific product or category "
                "across all ingested supplier flyers."
            ),
        ),
        AgentCardSkill(
            id="price-comparison",
            name="Price Comparison",
            description=(
                "Compare current and original prices, discount percentages and "
                "offer validity dates across multiple suppliers."
            ),
        ),
        AgentCardSkill(
            id="promotion-details",
            name="Promotion Details",
            description=(
                "Retrieve full promotion details including type, bonus amount, "
                "coupon requirements and packaging information."
            ),
        ),
    ],
)


def _toolbox_mcp_url(project_endpoint: str, toolbox_name: str) -> str:
    """Derive the toolbox MCP endpoint from the project endpoint and toolbox name."""
    return f"{project_endpoint.rstrip('/')}/toolboxes/{toolbox_name}/mcp?api-version=v1"


def deploy() -> None:
    project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT")
    if not project_endpoint:
        print("Skipping promotion agent deployment: AZURE_AI_PROJECT_ENDPOINT is required.")
        return

    client = get_client()
    model = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini")
    toolbox_name = os.getenv("PROMOTION_TOOLBOX_NAME", "promotion-tools")

    # Resolve the toolbox MCP URL (explicit override or derived from project endpoint)
    mcp_url = os.getenv("PROMOTION_TOOLBOX_MCP_URL", "").strip() or _toolbox_mcp_url(
        project_endpoint, toolbox_name
    )
    connection_id = os.getenv("PROMOTION_MCP_CONNECTION_ID", "").strip()

    # Build the MCPTool that connects the prompt agent to the promotion toolbox
    toolbox_tool = MCPTool(
        server_label="promotion-tools",
        server_url=mcp_url,
        server_description=(
            "Retail promotion and pricing search. Queries the retail-items AI Search "
            "index for promotions, prices, discounts and offer validity across suppliers."
        ),
        require_approval="never",
        **({"project_connection_id": connection_id} if connection_id else {}),
    )

    # Create the prompt agent version
    agent = client.agents.create_version(
        agent_name=AGENT_NAME,
        description=(
            "Retail promotion intelligence agent — identifies and compares product "
            "promotions and pricing details across supplier flyers."
        ),
        definition=PromptAgentDefinition(
            model=model,
            instructions=PROMOTION_SYSTEM_PROMPT,
            temperature=0.1,
            tools=[toolbox_tool],
        ),
        metadata={"toolbox": toolbox_name},
    )

    # Expose via RESPONSES, A2A and INVOCATIONS
    endpoint_config = AgentEndpointConfig(
        protocols=[
            AgentEndpointProtocol.RESPONSES,
            AgentEndpointProtocol.A2A,
            AgentEndpointProtocol.INVOCATIONS,
        ],
    )
    client.beta.agents.patch_agent_details(
        agent_name=AGENT_NAME,
        agent_endpoint=endpoint_config,
        agent_card=PROMOTION_AGENT_CARD,
    )

    a2a_base = f"{project_endpoint.rstrip('/')}/agents/{AGENT_NAME}/endpoint/protocols/a2a"
    print(f"\nPromotion agent '{AGENT_NAME}' created: {agent.id}")
    print(f"  Model: {model}")
    print(f"  Toolbox MCP URL: {mcp_url}")
    print(f"  A2A base path: {a2a_base}")
    print(f"  Agent card URL: {a2a_base}/agentCard/v0.3")


if __name__ == "__main__":
    deploy()
