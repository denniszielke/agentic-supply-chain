"""Deploy all Container App services (shopping-agent, shopping-chat, promotion-ingestion)
using the Azure CLI after infrastructure has been provisioned with `azd up`.

Environment variables required:
  AZURE_RESOURCE_GROUP  - target resource group
  AZURE_REGISTRY        - Azure Container Registry hostname (e.g. myregistry.azurecr.io)
  AZURE_CONTAINER_APPS_ENVIRONMENT_NAME - target Container Apps environment name
  TAG                   - image tag to deploy (default: latest)

Optional hosted agent deployment:
  AZURE_AI_PROJECT_ENDPOINT
  AZURE_CONTAINER_REGISTRY_ENDPOINT
  AZURE_AI_HOSTED_AGENT_NAME
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RESOURCE_GROUP = os.getenv("AZURE_RESOURCE_GROUP")
REGISTRY = os.getenv("AZURE_REGISTRY")
CONTAINER_APPS_ENVIRONMENT_NAME = os.getenv("AZURE_CONTAINER_APPS_ENVIRONMENT_NAME")
TAG = os.getenv("TAG", "latest")

APP_ENV_VARS = {
    "AZURE_SEARCH_ENDPOINT": os.getenv("AZURE_SEARCH_ENDPOINT"),
    "AZURE_SEARCH_INDEX_NAME": os.getenv("AZURE_SEARCH_INDEX_NAME"),
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
}

SERVICES = [
    {
        "app_name": "shopping-chat",
        "image": "shopping-chat",
        "port": 8080,
        "ingress": "external",
    },
    {
        "app_name": "promotion-ingestion",
        "image": "promotion-ingestion",
        "port": 8081,
        "ingress": None,
    },
    {
        "app_name": "shopping-agent",
        "image": "shopping-agent",
        "port": 8090,
        "ingress": "external",
    },
]


def run(cmd: list[str]) -> None:
    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, check=True)
    if result.returncode != 0:
        sys.exit(result.returncode)


def exists_container_app(name: str) -> bool:
    result = subprocess.run(
        ["az", "containerapp", "show", "--name", name, "--resource-group", RESOURCE_GROUP],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def env_pairs() -> list[str]:
    return [f"{key}={value}" for key, value in APP_ENV_VARS.items() if value]


def deploy() -> None:
    if not RESOURCE_GROUP or not REGISTRY or not CONTAINER_APPS_ENVIRONMENT_NAME:
        print(
            "ERROR: AZURE_RESOURCE_GROUP, AZURE_REGISTRY and "
            "AZURE_CONTAINER_APPS_ENVIRONMENT_NAME must be set.",
            file=sys.stderr,
        )
        sys.exit(1)

    for svc in SERVICES:
        image_ref = f"{REGISTRY}/{svc['image']}:{TAG}"
        print(f"\n==> Deploying {svc['app_name']} with image {image_ref}")
        if exists_container_app(svc["app_name"]):
            cmd = [
                "az",
                "containerapp",
                "update",
                "--name",
                svc["app_name"],
                "--resource-group",
                RESOURCE_GROUP,
                "--image",
                image_ref,
            ]
            if env_pairs():
                cmd.extend(["--set-env-vars", *env_pairs()])
            run(cmd)
            continue

        cmd = [
            "az",
            "containerapp",
            "create",
            "--name",
            svc["app_name"],
            "--resource-group",
            RESOURCE_GROUP,
            "--environment",
            CONTAINER_APPS_ENVIRONMENT_NAME,
            "--registry-server",
            REGISTRY,
            "--image",
            image_ref,
        ]
        if svc["ingress"]:
            cmd.extend(["--ingress", svc["ingress"], "--target-port", str(svc["port"])])
        if env_pairs():
            cmd.extend(["--env-vars", *env_pairs()])
        run(cmd)

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
