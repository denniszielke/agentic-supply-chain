#!/usr/bin/env bash
# Build all service container images for agentic-supply-chain.
# Usage:
#   ./scripts/build_containers.sh [REGISTRY] [TAG]
#
# Examples:
#   ./scripts/build_containers.sh                     # local images only
#   ./scripts/build_containers.sh myregistry.azurecr.io latest

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

REGISTRY="${1:-}"
TAG="${2:-latest}"

build_image() {
    local name="$1"
    local dockerfile="$2"
    local full_tag="${name}:${TAG}"
    if [[ -n "${REGISTRY}" ]]; then
        full_tag="${REGISTRY}/${full_tag}"
    fi
    echo "==> Building ${full_tag} from ${dockerfile}"
    docker build -t "${full_tag}" -f "${REPO_ROOT}/${dockerfile}" "${REPO_ROOT}"
    if [[ -n "${REGISTRY}" ]]; then
        echo "==> Pushing ${full_tag}"
        docker push "${full_tag}"
    fi
}

build_image "shopping-chat"       "src/shopping_chat/Dockerfile"
build_image "promotion-ingestion" "src/promotion_ingestion/Dockerfile"
build_image "shopping-agent"      "src/shopping_agent/Dockerfile"

echo "All images built successfully."
