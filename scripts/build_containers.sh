#!/usr/bin/env bash
# Build (and optionally deploy) all service container images using Azure
# Container Registry (ACR) build.
#
# Usage:
#   ./scripts/build_containers.sh <AZURE_ENV_NAME> [TAG] [--deploy]
#
# Examples:
#   ./scripts/build_containers.sh myenv                  # build images only
#   ./scripts/build_containers.sh myenv 20240608120000   # build with explicit tag
#   ./scripts/build_containers.sh myenv latest --deploy   # build, then deploy
#
# Deployment targets the `shopping-agent` AG-UI container app and wires all
# required environment variables (Azure AI Foundry, OpenAI, AI Search, and the
# user-assigned managed identity client id) into the container.
#
# Deployment can also be enabled with the DEPLOY=true environment variable.

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

# Optional --deploy flag (any position after the first two args) or DEPLOY=true.
DEPLOY="${DEPLOY:-false}"
for arg in "${@:2}"; do
    if [[ "${arg}" == "--deploy" ]]; then
        DEPLOY="true"
    fi
done

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
    local dockerfile="$2"   # relative to REPO_ROOT, e.g. src/pricing_mcp_server/Dockerfile
    echo "==> Building ${name}:${IMAGE_TAG} (and :latest) from ${dockerfile}"
    az acr build \
        --subscription "${AZURE_SUBSCRIPTION_ID}" \
        --registry "${ACR_NAME}" \
        --image "${name}:${IMAGE_TAG}" \
        --image "${name}:latest" \
        --platform linux/amd64 \
        --file "${dockerfile}" \
        "${REPO_ROOT}"
}

build_image "pricing-mcp-server"  "src/pricing_mcp_server/Dockerfile"
build_image "shopping-agent"      "src/shopping_agent/Dockerfile"
build_image "campaign-agent"      "src/campaign_agent/Dockerfile"

echo "All images built successfully."
echo "Registry: ${ACR_NAME}.azurecr.io, Tag: ${IMAGE_TAG}"

# ---------------------------------------------------------------------------
# Optional deployment
# ---------------------------------------------------------------------------

if [[ "${DEPLOY}" != "true" ]]; then
    echo "==> Skipping deployment (pass --deploy or set DEPLOY=true to deploy)."
    exit 0
fi

echo "==> Deploying shopping-agent container app to ${RESOURCE_GROUP}"

# Load azd outputs (written to ./.env by the azd postdeploy hook) so we can
# forward the Foundry / OpenAI / Search configuration into the container.
if [[ -f "${REPO_ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.env"
    set +a
fi

LOCATION=$(az group show -n "${RESOURCE_GROUP}" --query location -o tsv)
ENVIRONMENT_NAME="${AZURE_CONTAINER_APPS_ENVIRONMENT_NAME:-$(az resource list -g "${RESOURCE_GROUP}" --resource-type "Microsoft.App/managedEnvironments" --query "[0].name" -o tsv)}"
IDENTITY_NAME="${AZURE_IDENTITY_NAME:-$(az resource list -g "${RESOURCE_GROUP}" --resource-type "Microsoft.ManagedIdentity/userAssignedIdentities" --query "[0].name" -o tsv)}"

if [[ -z "${ENVIRONMENT_NAME}" || -z "${IDENTITY_NAME}" ]]; then
    echo "ERROR: could not resolve container apps environment or managed identity - aborting deploy"
    exit 1
fi

# Client id of the user-assigned identity — DefaultAzureCredential needs this to
# authenticate as the managed identity inside the container.
IDENTITY_CLIENT_ID=$(az identity show -g "${RESOURCE_GROUP}" -n "${IDENTITY_NAME}" --query clientId -o tsv)

# Fall back to fetching a Search admin key if not already provided via .env.
if [[ -z "${AZURE_SEARCH_ADMIN_KEY:-}" && -n "${AZURE_AI_SEARCH_SERVICE_NAME:-}" ]]; then
    AZURE_SEARCH_ADMIN_KEY=$(az search admin-key show -g "${RESOURCE_GROUP}" --service-name "${AZURE_AI_SEARCH_SERVICE_NAME}" --query primaryKey -o tsv 2>/dev/null || echo "")
fi

# Assemble the env var JSON array expected by infra/core/host/app.bicep.
ENV_PAIRS=()
add_env() {
    local name="$1" value="$2"
    if [[ -n "${value}" ]]; then
        ENV_PAIRS+=("{\"name\":\"${name}\",\"value\":\"${value}\"}")
    fi
}

add_env AZURE_AI_PROJECT_ENDPOINT             "${AZURE_AI_PROJECT_ENDPOINT:-}"
add_env AZURE_OPENAI_ENDPOINT                 "${AZURE_OPENAI_ENDPOINT:-}"
add_env AZURE_SEARCH_ENDPOINT                 "${AZURE_SEARCH_ENDPOINT:-}"
add_env AZURE_SEARCH_ADMIN_KEY                "${AZURE_SEARCH_ADMIN_KEY:-}"
add_env AZURE_SEARCH_KNOWLEDGE_BASE_NAME      "${AZURE_SEARCH_KNOWLEDGE_BASE_NAME:-supply-chain-kb}"
add_env AZURE_SEARCH_SUPPLIER_INDEX_NAME      "${AZURE_SEARCH_SUPPLIER_INDEX_NAME:-retail-suppliers}"
add_env AZURE_SEARCH_CATEGORY_INDEX_NAME      "${AZURE_SEARCH_CATEGORY_INDEX_NAME:-retail-categories}"
add_env AZURE_SEARCH_ITEM_INDEX_NAME          "${AZURE_SEARCH_ITEM_INDEX_NAME:-retail-items}"
add_env AZURE_OPENAI_CHAT_DEPLOYMENT_NAME     "${AZURE_OPENAI_CHAT_DEPLOYMENT_NAME:-gpt-4.1-mini}"
add_env AZURE_AI_MODEL_DEPLOYMENT_NAME        "${AZURE_AI_MODEL_DEPLOYMENT_NAME:-gpt-4.1-mini}"
add_env AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME "${AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME:-text-embedding-3-small}"
add_env APPLICATIONINSIGHTS_CONNECTION_STRING "${APPLICATIONINSIGHTS_CONNECTION_STRING:-}"
add_env AZURE_CLIENT_ID                        "${IDENTITY_CLIENT_ID}"
add_env HOST                                   "0.0.0.0"
add_env PORT                                   "8090"

ENV_JSON="[$(IFS=,; echo "${ENV_PAIRS[*]}")]"

APP_NAME="shopping-agent"
IMAGE_NAME="${ACR_NAME}.azurecr.io/${APP_NAME}:${IMAGE_TAG}"

echo "==> Deploying ${APP_NAME} (image: ${IMAGE_NAME})"

APP_URI=$(az deployment group create \
    --resource-group "${RESOURCE_GROUP}" \
    --name "deploy-${APP_NAME}-${IMAGE_TAG}" \
    --template-file "${REPO_ROOT}/infra/core/host/app.bicep" \
    --parameters \
        name="${APP_NAME}" \
        location="${LOCATION}" \
        containerAppsEnvironmentName="${ENVIRONMENT_NAME}" \
        containerRegistryName="${ACR_NAME}" \
        identityName="${IDENTITY_NAME}" \
        imageName="${IMAGE_NAME}" \
        targetPort=8090 \
        external=true \
        containerCpuCoreCount="1" \
        containerMemory="2.0Gi" \
        envJson="${ENV_JSON}" \
    --query "properties.outputs.uri.value" -o tsv)

echo ""
echo "shopping-agent deployed successfully."
echo "URL: ${APP_URI}"

