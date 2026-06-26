"""Add the current signed-in user as an owner of the blueprint application.

Python port of ``add-current-user-as-blueprint-owner.ps1``. Resolves the
blueprint application's object id from its app id, then adds the current
``az login`` user as an owner via Microsoft Graph. Treats "already exist" as
success.

Run standalone (requires AGENT_IDENTITY_BLUEPRINT_ID):

    AGENT_IDENTITY_BLUEPRINT_ID=<id> \
        python -m scripts.autopilot.add_blueprint_owner
"""

from __future__ import annotations

import os

import requests

from .common import GRAPH_RESOURCE, az, get_access_token


def add_blueprint_owner(blueprint_app_id: str) -> None:
    """Add the current signed-in user as an owner of the blueprint app."""
    if not blueprint_app_id:
        raise ValueError("blueprint_app_id is required.")

    current_user_id = az("ad", "signed-in-user", "show", "--query", "id", "-o", "tsv")
    if not current_user_id:
        raise RuntimeError(
            "Failed to get the current signed-in user's object id. "
            "Make sure you are logged in via `az login`."
        )
    print(f"==> Current user object id: {current_user_id}")

    blueprint_obj_id = az(
        "ad", "app", "show", "--id", blueprint_app_id, "--query", "id", "-o", "tsv"
    )
    if not blueprint_obj_id:
        raise RuntimeError(
            f"Failed to resolve application object id for blueprint app id {blueprint_app_id}."
        )
    print(f"==> Blueprint application object id: {blueprint_obj_id}")

    graph_token = get_access_token(GRAPH_RESOURCE)
    url = f"https://graph.microsoft.com/v1.0/applications/{blueprint_obj_id}/owners/$ref"
    body = {
        "@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{current_user_id}"
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {graph_token}",
    }

    response = requests.post(url, headers=headers, json=body, timeout=60)
    if response.status_code >= 400:
        text = response.text or ""
        if "One or more added object references already exist" in text:
            print("==> Current user is already an owner of the blueprint app; ignoring.")
            return
        response.raise_for_status()
    print(f"==> Current user added as owner of blueprint application {blueprint_app_id}.")


def main() -> int:
    blueprint_id = os.getenv("AGENT_IDENTITY_BLUEPRINT_ID", "").strip()
    if not blueprint_id:
        print("❌ AGENT_IDENTITY_BLUEPRINT_ID is required.")
        return 1
    try:
        add_blueprint_owner(blueprint_id)
    except requests.HTTPError as ex:
        print(f"❌ Add owner failed: {ex} — {ex.response.text if ex.response else ''}")
        return 1
    except Exception as ex:  # noqa: BLE001
        print(f"❌ Add owner failed: {ex}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
