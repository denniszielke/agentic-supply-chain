"""Step 1 of the campaign-agent deployment pipeline.

Deploy the **pricing MCP server** as a Container App. This is the confidential
internal pricing surface that the campaign planning agent reaches through a
Foundry toolbox. Run it after ``azd up`` has provisioned the infrastructure.

Usage::

    # build the image in ACR, then deploy
    python -m scripts.deploy_pricing_mcp_server --build

    # deploy only (image already in ACR)
    python -m scripts.deploy_pricing_mcp_server

The next two steps are:
  2. ``scripts/register_pricing_toolbox.py`` — publish this server as a toolbox.
  3. ``scripts/deploy_campaign_agent.py``    — deploy the hosted campaign agent.

Environment variables (all populated automatically from ``.env`` after ``azd up``):
  AZURE_RESOURCE_GROUP                   target resource group (required)
  AZURE_REGISTRY                         ACR login server (required)
  AZURE_CONTAINER_APPS_ENVIRONMENT_NAME  Container Apps environment (required)
  AZURE_IDENTITY_NAME                    user-assigned managed identity (required)
  TAG                                    image tag to deploy (default: latest)
  PRICING_MCP_EXTERNAL                   "true" to expose public ingress so the
                                         Foundry project can reach it directly
                                         (default: false / internal ingress)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from scripts.deploy_helpers import build_image, deploy_container_app, get_env

APP_NAME = os.getenv("PRICING_MCP_APP_NAME", "pricing-mcp-server")
PORT = int(os.getenv("PRICING_MCP_PORT", "8091"))
_DOCKERFILE = "src/pricing_mcp_server/Dockerfile"


def build() -> str:
    """Build the pricing-mcp-server image in ACR (tagged with timestamp + :latest).

    Returns the concrete image tag that was built so it can be passed straight
    to the deploy step.
    """
    registry = get_env("AZURE_REGISTRY")
    source_path = Path(__file__).resolve().parents[1]
    dockerfile = str(source_path / _DOCKERFILE)
    return build_image(registry, "pricing-mcp-server", source_path, dockerfile=dockerfile)


def deploy(tag: str | None = None) -> None:
    external = os.getenv("PRICING_MCP_EXTERNAL", "false").strip().lower() == "true"
    env_vars = {
        "PRICING_MCP_HOST": "0.0.0.0",
        "PRICING_MCP_PORT": str(PORT),
        "APPLICATIONINSIGHTS_CONNECTION_STRING": os.getenv(
            "APPLICATIONINSIGHTS_CONNECTION_STRING", ""
        ),
    }

    fqdn = deploy_container_app(
        app_name=APP_NAME,
        image_name="pricing-mcp-server",
        port=PORT,
        external=external,
        env_vars=env_vars,
        tag=tag,
    )

    if fqdn:
        mcp_url = f"https://{fqdn}/mcp"
        print(f"\nPricing MCP server deployed: {mcp_url}")
        print("Register it as a Foundry toolbox with:")
        print(f"  PRICING_MCP_URL={mcp_url} python -m scripts.register_pricing_toolbox")
    else:
        print(
            "\nPricing MCP server deployed, but no ingress FQDN was returned. "
            "Set PRICING_MCP_EXTERNAL=true or check the Container App ingress."
        )


if __name__ == "__main__":
    do_build = "--build" in sys.argv
    built_tag: str | None = None
    if do_build:
        built_tag = build()
    deploy(tag=built_tag)
