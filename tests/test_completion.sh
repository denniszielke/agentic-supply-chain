#!/usr/bin/env bash
set -euo pipefail

# Load .env variables
set -a
source "$(dirname "$0")/../.env"
set +a

ENDPOINT="${AZURE_OPENAI_ENDPOINT%/}"
DEPLOYMENT="${AZURE_OPENAI_CHAT_DEPLOYMENT_NAME:-gpt-4.1-mini}"
API_VERSION="${OPENAI_API_VERSION:-2024-05-01-preview}"

# Acquire Entra ID bearer token via Azure CLI
echo "Acquiring Entra ID token..."
TOKEN=$(az account get-access-token \
  --resource "https://cognitiveservices.azure.com" \
  --query accessToken \
  --output tsv)

echo "Calling chat completions..."
curl -sS -X POST "${ENDPOINT}/openai/deployments/${DEPLOYMENT}/chat/completions?api-version=${API_VERSION}" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${TOKEN}" \
  -d '{
    "messages": [
      {
        "role": "system",
        "content": "You are a helpful assistant."
      },
      {
        "role": "user",
        "content": "Say hello in one sentence."
      }
    ],
    "max_completion_tokens": 256
  }' | python3 -m json.tool
