"""Step 2 of the campaign-agent deployment pipeline.

Create or update the Foundry **toolbox** that the hosted campaign agent consumes
at runtime, backed by the **remote** pricing MCP server deployed in step 1. This
adapts the sample at
https://github.com/denniszielke/msft-foundry-hosted-agents-sample/blob/main/src/deploy_toolbox.py
to register a remote MCP server (``MCPTool``) instead of a Bing web-search tool.

The seven pricing tools (list_categories, list_products, get_product_pricing,
get_category_margin_forecast, get_volume_forecast, simulate_price_change,
list_personas) become discoverable to any agent in the project through the
toolbox MCP endpoint ``{project}/toolboxes/{toolbox}/mcp?api-version=v1``.

Environment variables:
  AZURE_AI_PROJECT_ENDPOINT   Foundry project endpoint (required).
  PRICING_TOOLBOX_NAME        Toolbox name (default: pricing-tools).
  PRICING_MCP_URL             Streamable-HTTP MCP endpoint of the deployed
                              pricing server. If unset, it is derived from the
                              ``pricing-mcp-server`` Container App's ingress FQDN
                              using AZURE_RESOURCE_GROUP.
  PRICING_MCP_APP_NAME        Container App name to resolve the URL from
                              (default: pricing-mcp-server).
  PRICING_MCP_CONNECTION_ID   Optional Foundry connection id used to authorize
                              calls to a network-restricted MCP server.
"""

from __future__ import annotations

import os
import subprocess

from azure.ai.projects.models import MCPTool

from scripts.deploy_helpers import get_client, get_container_app_fqdn, get_env

TOOLBOX_NAME = os.getenv("PRICING_TOOLBOX_NAME", "pricing-tools")


def _resolve_mcp_url() -> str:
    """Return the remote MCP server URL, deriving it from the Container App."""
    url = os.getenv("PRICING_MCP_URL", "").strip()
    if url:
        return url

    resource_group = os.getenv("AZURE_RESOURCE_GROUP", "").strip()
    app_name = os.getenv("PRICING_MCP_APP_NAME", "pricing-mcp-server")
    if resource_group:
        try:
            fqdn = get_container_app_fqdn(resource_group, app_name)
        except (subprocess.CalledProcessError, FileNotFoundError):
            fqdn = ""
        if fqdn:
            return f"https://{fqdn}/mcp"
    return ""


def deploy() -> None:
    if not os.getenv("AZURE_AI_PROJECT_ENDPOINT"):
        print("Skipping toolbox registration: AZURE_AI_PROJECT_ENDPOINT is required.")
        return

    mcp_url = _resolve_mcp_url()
    if not mcp_url:
        print(
            "Skipping toolbox registration: set PRICING_MCP_URL, or set "
            "AZURE_RESOURCE_GROUP so the pricing-mcp-server URL can be derived."
        )
        return

    # Remote MCP server exposed as a toolbox tool. require_approval="never" lets
    # the hosted agent invoke the pricing tools without a human approval gate.
    connection_id = os.getenv("PRICING_MCP_CONNECTION_ID", "").strip()
    tool = MCPTool(
        server_label="pricing",
        server_url=mcp_url,
        server_description="Internal pricing, margin and volume forecasting for retail categories.",
        require_approval="never",
        **({"project_connection_id": connection_id} if connection_id else {}),
    )

    client = get_client()
    version = client.beta.toolboxes.create_version(
        name=TOOLBOX_NAME,
        tools=[tool],
        description="Pricing MCP server exposed as a Foundry toolbox.",
        metadata={"source": "pricing-mcp-server"},
    )
    client.beta.toolboxes.update(name=TOOLBOX_NAME, default_version=version.version)

    project_endpoint = get_env("AZURE_AI_PROJECT_ENDPOINT")
    consumer_endpoint = (
        f"{project_endpoint.rstrip('/')}/toolboxes/{TOOLBOX_NAME}/mcp?api-version=v1"
    )
    print(f"Toolbox '{TOOLBOX_NAME}' version '{version.version}' created.")
    print(f"  Remote MCP server: {mcp_url}")
    print(f"  Consumer endpoint: {consumer_endpoint}")


if __name__ == "__main__":
    deploy()
