from __future__ import annotations

import json
import os
from pathlib import Path

import requests


def create_or_update_index(schema_path: Path) -> None:
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
    api_key = os.getenv("AZURE_SEARCH_ADMIN_KEY")
    if not endpoint or not api_key:
        raise RuntimeError("AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_ADMIN_KEY are required")

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    index_name = schema["name"]
    url = f"{endpoint}/indexes/{index_name}?api-version=2024-07-01"

    response = requests.put(
        url,
        headers={
            "Content-Type": "application/json",
            "api-key": api_key,
        },
        json=schema,
        timeout=30,
    )
    response.raise_for_status()
    print(f"Index '{index_name}' created/updated")


def main() -> None:
    schema_path = Path("/tmp/workspace/denniszielke/agentic-supply-chain/infra/search-schema.json")
    create_or_update_index(schema_path)


if __name__ == "__main__":
    main()
