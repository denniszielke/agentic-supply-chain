"""Deploy all Container App services (shopping-agent, shopping-chat, promotion-ingestion)
using the Azure CLI after infrastructure has been provisioned with `azd up`.

Environment variables required:
  AZURE_RESOURCE_GROUP  - target resource group
  AZURE_REGISTRY        - Azure Container Registry hostname (e.g. myregistry.azurecr.io)
  TAG                   - image tag to deploy (default: latest)
"""
from __future__ import annotations

import os
import subprocess
import sys

RESOURCE_GROUP = os.getenv("AZURE_RESOURCE_GROUP")
REGISTRY = os.getenv("AZURE_REGISTRY")
TAG = os.getenv("TAG", "latest")

SERVICES = [
    {
        "app_name": "shopping-chat",
        "image": "shopping-chat",
        "port": 8080,
    },
    {
        "app_name": "promotion-ingestion",
        "image": "promotion-ingestion",
        "port": 8081,
    },
    {
        "app_name": "shopping-agent",
        "image": "shopping-agent",
        "port": 8090,
    },
]


def run(cmd: list[str]) -> None:
    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, check=True)
    if result.returncode != 0:
        sys.exit(result.returncode)


def deploy() -> None:
    if not RESOURCE_GROUP or not REGISTRY:
        print("ERROR: AZURE_RESOURCE_GROUP and AZURE_REGISTRY must be set.", file=sys.stderr)
        sys.exit(1)

    for svc in SERVICES:
        image_ref = f"{REGISTRY}/{svc['image']}:{TAG}"
        print(f"\n==> Deploying {svc['app_name']} with image {image_ref}")
        run([
            "az", "containerapp", "update",
            "--name", svc["app_name"],
            "--resource-group", RESOURCE_GROUP,
            "--image", image_ref,
        ])

    print("\nAll agents deployed successfully.")


if __name__ == "__main__":
    deploy()
