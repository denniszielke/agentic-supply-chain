"""Step 3 of the campaign-agent deployment pipeline.

Deploy the **campaign planning agent** as an Azure AI Foundry hosted agent
(RESPONSES protocol). It consumes the pricing MCP server through the Foundry
toolbox registered in step 2 (``PRICING_TOOLBOX_NAME``), so run this only after
``scripts/deploy_pricing_mcp_server.py`` and ``scripts/register_pricing_toolbox.py``.

Environment variables:
  AZURE_AI_PROJECT_ENDPOINT           Foundry project endpoint (required).
  AZURE_CONTAINER_REGISTRY_ENDPOINT   ACR login server for the agent image (required).
  AZURE_AI_CAMPAIGN_AGENT_NAME        Hosted agent name (default: campaign-agent).
  PRICING_TOOLBOX_NAME                Toolbox the agent consumes (default: pricing-tools).
  TOOLBOX_MCP_ENDPOINT                Explicit toolbox MCP URL (optional override).
  PRICING_MCP_URL                     Direct MCP URL to bypass the toolbox (optional).
"""

from __future__ import annotations

import os

from scripts.deploy_helpers import deploy_hosted_agent, get_client


def deploy() -> None:
    project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT")
    registry = os.getenv("AZURE_CONTAINER_REGISTRY_ENDPOINT")
    if not project_endpoint or not registry:
        print(
            "Skipping campaign agent deployment: AZURE_AI_PROJECT_ENDPOINT and "
            "AZURE_CONTAINER_REGISTRY_ENDPOINT are required."
        )
        return

    client = get_client()
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
    )


if __name__ == "__main__":
    deploy()
