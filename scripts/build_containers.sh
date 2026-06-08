#!/usr/bin/env bash
# Build all service container images using Azure Container Registry (ACR) build.
# Usage:
#   ./scripts/build_containers.sh <AZURE_ENV_NAME> [TAG]
#
# Examples:
#   ./scripts/build_containers.sh myenv
#   ./scripts/build_containers.sh myenv 20240608120000

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

AZURE_ENV_NAME="${1:-}"

if [[ -z "${AZURE_ENV_NAME}" ]]; then
    echo "ERROR: No environment name provided - aborting"
    exit 1
fi

RESOURCE_GROUP="rg-${AZURE_ENV_NAME}"
IMAGE_TAG="${2:-$(date '+%Y%m%d%H%M%S')}"

if [[ $(az group exists --name "${RESOURCE_GROUP}") == false ]]; then
    echo "ERROR: resource group ${RESOURCE_GROUP} does not exist - aborting"
    exit 1
fi

AZURE_SUBSCRIPTION_ID=$(az account show --query id -o tsv)
ACR_NAME=$(az resource list -g "${RESOURCE_GROUP}" --resource-type "Microsoft.ContainerRegistry/registries" --query "[0].name" -o tsv)

if [[ -z "${ACR_NAME}" ]]; then
    echo "ERROR: No container registry found in resource group ${RESOURCE_GROUP} - aborting"
    exit 1
fi

echo "==> Using registry: ${ACR_NAME}, tag: ${IMAGE_TAG}"

build_image() {
    local name="$1"
    local dockerfile="$2"
    echo "==> Building ${name}:${IMAGE_TAG} from ${dockerfile}"
    az acr build \
        --subscription "${AZURE_SUBSCRIPTION_ID}" \
        --registry "${ACR_NAME}" \
        --image "${name}:${IMAGE_TAG}" \
        --platform linux/amd64 \
        --file "${REPO_ROOT}/${dockerfile}" \
        "${REPO_ROOT}"
}

build_image "shopping-chat"       "src/shopping_chat/Dockerfile"
build_image "promotion-ingestion" "src/promotion_ingestion/Dockerfile"
build_image "shopping-agent"      "src/shopping_agent/Dockerfile"

echo "All images built successfully."
echo "Registry: ${ACR_NAME}.azurecr.io, Tag: ${IMAGE_TAG}"
