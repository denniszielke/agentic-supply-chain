"""Create the Managed Agent Identity Blueprint (MAIB) for the project.

Python port of the ``maib-creation-script.bicep`` deployment script. Creating a
MAIB is a data-plane operation, so the original sample ran it from an
``AzurePowerShell`` deployment script. That approach requires a deployment-script
storage account with *key-based* auth, which is blocked by policy in some
tenants ("KeyBasedAuthenticationNotPermitted"). Doing the PUT directly from
Python avoids the deployment script (and its storage account) entirely.

The blueprint client id this returns is used as the bot ``msaAppId`` and by the
downstream digital-worker publish + OAuth2 grant steps.

Run standalone:

    python -m scripts.autopilot.create_maib
"""

from __future__ import annotations

import requests

from .common import (
    AGENTS_API_VERSION,
    AI_AZURE_RESOURCE,
    AutopilotConfig,
    get_access_token,
)


def _extract_client_id(payload: dict) -> str:
    """Pull the blueprint client id out of a MAIB response payload."""
    blueprint = payload.get("agentIdentityBlueprint") or payload
    client_id = (
        blueprint.get("clientId")
        or blueprint.get("client_id")
        or payload.get("clientId")
        or payload.get("client_id")
        or ""
    )
    return client_id


def create_maib(config: AutopilotConfig | None = None) -> str:
    """Create (or fetch) the MAIB and return its blueprint client id."""
    config = config or AutopilotConfig.from_env()
    token = get_access_token(AI_AZURE_RESOURCE)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    maib_url = (
        f"{config.project_endpoint}/managedagentidentityblueprints/"
        f"{config.maib_name}?api-version={AGENTS_API_VERSION}"
    )

    print(f"==> Creating managed agent identity blueprint at: {maib_url}")
    response = requests.put(maib_url, headers=headers, timeout=120)
    if response.status_code >= 400:
        response.raise_for_status()

    client_id = _extract_client_id(response.json() if response.content else {})

    # PUT may return 200/201 without echoing the blueprint on a re-run; GET it.
    if not client_id:
        get_resp = requests.get(maib_url, headers=headers, timeout=60)
        get_resp.raise_for_status()
        client_id = _extract_client_id(get_resp.json())

    if not client_id:
        raise RuntimeError(
            "MAIB creation succeeded but no blueprint client id was returned."
        )
    print(f"==> Blueprint client id: {client_id}")
    return client_id


def main() -> int:
    try:
        client_id = create_maib()
    except requests.HTTPError as ex:
        print(f"❌ MAIB creation failed: {ex} — {ex.response.text if ex.response else ''}")
        return 1
    except Exception as ex:  # noqa: BLE001
        print(f"❌ MAIB creation failed: {ex}")
        return 1
    print(f"AGENT_IDENTITY_BLUEPRINT_ID={client_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
