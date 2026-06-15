"""Deploy the core Container App services (shopping-agent, shopping-chat,
promotion-ingestion) using the Azure CLI after infrastructure has been
provisioned with `azd up`.

The pricing pipeline is deployed as three separate, explicit steps:
  1. scripts/deploy_pricing_mcp_server.py   - deploy the pricing MCP server
  2. scripts/register_pricing_toolbox.py    - register it as a Foundry toolbox
  3. scripts/deploy_campaign_agent.py       - deploy the hosted campaign agent

Environment variables required:
  AZURE_RESOURCE_GROUP  - target resource group
  AZURE_REGISTRY        - Azure Container Registry login server (e.g. myregistry.azurecr.io)
  AZURE_CONTAINER_APPS_ENVIRONMENT_NAME - target Container Apps environment name
  TAG                   - image tag to deploy (default: latest)

Optional hosted agent deployment:
  AZURE_AI_PROJECT_ENDPOINT
  AZURE_CONTAINER_REGISTRY_ENDPOINT
  AZURE_AI_HOSTED_AGENT_NAME
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

RESOURCE_GROUP = os.getenv("AZURE_RESOURCE_GROUP")
REGISTRY = os.getenv("AZURE_REGISTRY")
CONTAINER_APPS_ENVIRONMENT_NAME = os.getenv("AZURE_CONTAINER_APPS_ENVIRONMENT_NAME")
IDENTITY_NAME = os.getenv("AZURE_IDENTITY_NAME")
TAG = os.getenv("TAG", "latest")

APP_BICEP = Path(__file__).parent.parent / "infra" / "core" / "host" / "app.bicep"

APP_ENV_VARS = {
    "AZURE_SEARCH_ENDPOINT": os.getenv("AZURE_SEARCH_ENDPOINT"),
    "AZURE_SEARCH_SUPPLIER_INDEX_NAME": os.getenv("AZURE_SEARCH_SUPPLIER_INDEX_NAME"),
    "AZURE_SEARCH_CATEGORY_INDEX_NAME": os.getenv("AZURE_SEARCH_CATEGORY_INDEX_NAME"),
    "AZURE_SEARCH_ITEM_INDEX_NAME": os.getenv("AZURE_SEARCH_ITEM_INDEX_NAME"),
    "AZURE_SEARCH_ADMIN_KEY": os.getenv("AZURE_SEARCH_ADMIN_KEY"),
    "APPLICATIONINSIGHTS_CONNECTION_STRING": os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"),
    "AZURE_OPENAI_ENDPOINT": os.getenv("AZURE_OPENAI_ENDPOINT"),
    "AZURE_AI_PROJECT_ENDPOINT": os.getenv("AZURE_AI_PROJECT_ENDPOINT"),
    "AZURE_AI_PROJECT_ID": os.getenv("AZURE_AI_PROJECT_ID"),
    "AZURE_AI_PROJECT_NAME": os.getenv("AZURE_AI_PROJECT_NAME"),
    "AZURE_AI_MODEL_DEPLOYMENT_NAME": os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME"),
    "AZURE_OPENAI_CHAT_DEPLOYMENT_NAME": os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME"),
    "AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME": os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME"),
    "OPENAI_API_VERSION": os.getenv("OPENAI_API_VERSION"),
    "AZURE_OPENAI_API_VERSION": os.getenv("AZURE_OPENAI_API_VERSION"),
    "AZURE_SEARCH_KNOWLEDGE_BASE_NAME": os.getenv("AZURE_SEARCH_KNOWLEDGE_BASE_NAME"),
    "PRICING_MCP_URL": os.getenv("PRICING_MCP_URL"),
}

SERVICES = [
    {
        "app_name": "promotion-ingestion",
        "image": "promotion-ingestion",
        "port": 8081,
        "external": False,
    },
    {
        "app_name": "shopping-agent",
        "image": "shopping-agent",
        "port": 8090,
        "external": True,
    },
]


def run(cmd: list[str]) -> None:
    print(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def registry_name(login_server: str) -> str:
    """Strip .azurecr.io to get the bare ACR resource name."""
    return login_server.removesuffix(".azurecr.io")


def env_json() -> str:
    return json.dumps(
        [{"name": k, "value": v} for k, v in APP_ENV_VARS.items() if v]
    )


def deploy() -> None:
    if not RESOURCE_GROUP or not REGISTRY or not CONTAINER_APPS_ENVIRONMENT_NAME or not IDENTITY_NAME:
        print(
            "ERROR: AZURE_RESOURCE_GROUP, AZURE_REGISTRY, "
            "AZURE_CONTAINER_APPS_ENVIRONMENT_NAME and AZURE_IDENTITY_NAME must be set.",
            file=sys.stderr,
        )
        sys.exit(1)

    acr_name = registry_name(REGISTRY)
    env_vars = env_json()

    for svc in SERVICES:
        image_ref = f"{REGISTRY}/{svc['image']}:{TAG}"
        print(f"\n==> Deploying {svc['app_name']} with image {image_ref}")
        run([
            "az", "deployment", "group", "create",
            "--resource-group", RESOURCE_GROUP,
            "--template-file", str(APP_BICEP),
            "--parameters",
            f"name={svc['app_name']}",
            f"containerAppsEnvironmentName={CONTAINER_APPS_ENVIRONMENT_NAME}",
            f"containerRegistryName={acr_name}",
            f"identityName={IDENTITY_NAME}",
            f"imageName={image_ref}",
            f"targetPort={svc['port']}",
            f"external={'true' if svc['external'] else 'false'}",
            f"envJson={env_vars}",
        ])

    if os.getenv("AZURE_AI_PROJECT_ENDPOINT") and os.getenv("AZURE_CONTAINER_REGISTRY_ENDPOINT"):
        print("\n==> Deploying hosted agents from source code")
        run([sys.executable, str(Path(__file__).with_name("deploy_hosted_agents.py"))])
    else:
        print(
            "\nSkipping hosted agent deployment. Set AZURE_AI_PROJECT_ENDPOINT and "
            "AZURE_CONTAINER_REGISTRY_ENDPOINT to enable it."
        )

    print("\nAll agents deployed successfully.")


if __name__ == "__main__":
    deploy()
