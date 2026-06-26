"""Configure the blueprint backend in the Teams Developer Portal.

Python port of ``configure-blueprint-backend.ps1``. PUTs a bot-based backend
configuration for the agent blueprint so it is wired to the bot (whose bot id is
the same as the blueprint id). This is optional and disabled by default in the
wrapper because it requires an interactive Teams Developer Portal token; run it
standalone after `az login --scope https://dev.teams.microsoft.com/.default`.

Run standalone (requires AGENT_IDENTITY_BLUEPRINT_ID):

    AGENT_IDENTITY_BLUEPRINT_ID=<id> \
        python -m scripts.autopilot.configure_blueprint_backend
"""

from __future__ import annotations

import os

import requests

from .common import TEAMS_DEV_RESOURCE, get_access_token


def configure_blueprint_backend(blueprint_id: str) -> None:
    """Configure the bot-based backend for the agent blueprint."""
    if not blueprint_id:
        raise ValueError("blueprint_id is required.")

    token = get_access_token(TEAMS_DEV_RESOURCE)
    url = (
        f"https://dev.teams.microsoft.com/api/v1.0/agentblueprints/"
        f"{blueprint_id}/backendConfiguration"
    )
    # Bot id is the same as the agent blueprint id (see the sample readme Step 4).
    body = {"type": "botBased", "botBased": {"botId": blueprint_id}}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }

    print(f"==> PUT {url}")
    response = requests.put(url, headers=headers, json=body, timeout=60)
    response.raise_for_status()
    print(f"==> Blueprint backend configuration completed for blueprint {blueprint_id}.")


def main() -> int:
    blueprint_id = os.getenv("AGENT_IDENTITY_BLUEPRINT_ID", "").strip()
    if not blueprint_id:
        print("❌ AGENT_IDENTITY_BLUEPRINT_ID is required.")
        return 1
    try:
        configure_blueprint_backend(blueprint_id)
    except requests.HTTPError as ex:
        print(
            "❌ Configure backend failed "
            f"({ex} — {ex.response.text if ex.response else ''}). "
            "Try: az login --scope https://dev.teams.microsoft.com/.default"
        )
        return 1
    except Exception as ex:  # noqa: BLE001
        print(f"❌ Configure backend failed: {ex}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
