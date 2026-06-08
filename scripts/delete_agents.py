"""Delete all Container App services created for agentic-supply-chain.

Environment variables required:
  AZURE_RESOURCE_GROUP  - resource group containing the container apps
"""
from __future__ import annotations

import os
import subprocess
import sys

RESOURCE_GROUP = os.getenv("AZURE_RESOURCE_GROUP")

AGENT_NAMES = [
    "shopping-chat",
    "promotion-ingestion",
    "shopping-agent",
]


def run(cmd: list[str]) -> None:
    print(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, check=False)


def delete_all() -> None:
    if not RESOURCE_GROUP:
        print("ERROR: AZURE_RESOURCE_GROUP must be set.", file=sys.stderr)
        sys.exit(1)

    for name in AGENT_NAMES:
        print(f"\n==> Deleting container app '{name}'")
        run([
            "az", "containerapp", "delete",
            "--name", name,
            "--resource-group", RESOURCE_GROUP,
            "--yes",
        ])

    print("\nAll agents deleted.")


if __name__ == "__main__":
    delete_all()
