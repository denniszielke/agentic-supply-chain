"""Delete the Azure AI Search index defined in infra/search-schema.json.

Environment variables required:
  AZURE_SEARCH_ENDPOINT   - e.g. https://<service>.search.windows.net

Optional:
  AZURE_SEARCH_ADMIN_KEY  - admin API key. When omitted, DefaultAzureCredential
                            is used to obtain an AAD bearer token.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
from azure.identity import DefaultAzureCredential

SEARCH_SCOPE = "https://search.azure.com/.default"


def _auth_headers() -> dict[str, str]:
    """Return request headers using an admin key when available, otherwise an AAD token."""
    headers: dict[str, str] = {}
    api_key = os.getenv("AZURE_SEARCH_ADMIN_KEY", "").strip()
    if api_key:
        headers["api-key"] = api_key
    else:
        token = DefaultAzureCredential().get_token(SEARCH_SCOPE).token
        headers["Authorization"] = f"Bearer {token}"
    return headers


def delete_index(schema_path: Path) -> None:
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
    if not endpoint:
        print("ERROR: AZURE_SEARCH_ENDPOINT must be set.", file=sys.stderr)
        sys.exit(1)

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    index_name = schema["name"]
    url = f"{endpoint}/indexes/{index_name}?api-version=2024-07-01"

    response = requests.delete(
        url,
        headers=_auth_headers(),
        timeout=30,
    )
    if response.status_code == 404:
        print(f"Index '{index_name}' does not exist, nothing to delete.")
        return
    response.raise_for_status()
    print(f"Index '{index_name}' deleted successfully.")


if __name__ == "__main__":
    schema_path = Path(__file__).resolve().parents[1] / "infra" / "search-schema.json"
    delete_index(schema_path)
