"""Create (or update) the hosted agent version and wire its endpoint.

Python port of ``agent-creation-script.ps1``. It:

  1. POSTs a new hosted agent version that references the ACR image and the
     Managed Agent Identity Blueprint, using the ``activity_protocol``.
  2. Polls the version until it reaches ``active`` (or fails).
  3. Grants the agent's default instance identity the Cognitive Services User
     role on the Foundry account so it can call the model.
  4. PATCHes the agent endpoint to use the activity protocol with
     ``BotServiceRbac`` authorization.

Returns the agent GUID for the publish step.

Run standalone:

    python -m scripts.autopilot.create_agent
"""

from __future__ import annotations

import subprocess
import time

import requests

from .common import (
    AGENTS_API_VERSION,
    AI_AZURE_RESOURCE,
    COGNITIVE_SERVICES_USER_ROLE_ID,
    AutopilotConfig,
    az,
    get_access_token,
)


def _headers(token: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "Foundry-Features": "HostedAgents=V1Preview,AgentEndpoints=V1Preview",
    }


def create_agent(config: AutopilotConfig | None = None) -> str:
    """Create the hosted agent version and return its agent GUID."""
    config = config or AutopilotConfig.from_env()
    token = get_access_token(AI_AZURE_RESOURCE)
    headers = _headers(token)

    image = f"{config.registry_login_server}/{config.image_name}:latest"
    agent_url = (
        f"{config.project_endpoint}/agents/{config.agent_name}/versions"
        f"?api-version={AGENTS_API_VERSION}"
    )
    body = {
        "definition": {
            "kind": "hosted",
            "image": image,
            "cpu": "2",
            "memory": "4Gi",
            "environment_variables": {},
            "container_protocol_versions": [
                {"protocol": "activity_protocol", "version": "v1"}
            ],
        },
        "metadata": {"enableVnextExperience": "true"},
        "description": "Campaign planning A365 digital worker.",
        "agent_endpoint": {"protocols": ["activity"]},
        "blueprint_reference": {
            "type": "ManagedAgentIdentityBlueprint",
            "blueprint_id": config.maib_name,
        },
    }

    print(f"==> Creating agent version at: {agent_url}")
    response = requests.post(agent_url, headers=headers, json=body, timeout=120)
    response.raise_for_status()
    data = response.json()

    agent_version = data.get("version")
    agent_guid = data.get("agent_guid")
    instance_client_id = (data.get("instance_identity") or {}).get("client_id")
    print(f"==> Agent GUID: {agent_guid}, version: {agent_version}")

    _poll_until_active(config, headers, agent_version)
    if instance_client_id:
        _grant_cognitive_services_user(config, instance_client_id)
    _patch_endpoint(config, headers)

    return agent_guid


def _poll_until_active(
    config: AutopilotConfig,
    headers: dict[str, str],
    agent_version: str,
    max_retries: int = 30,
    delay_seconds: int = 10,
) -> None:
    poll_url = (
        f"{config.project_endpoint}/agents/{config.agent_name}/versions/{agent_version}"
        f"?api-version={AGENTS_API_VERSION}"
    )
    status = "Unknown"
    for attempt in range(max_retries):
        try:
            poll = requests.get(poll_url, headers=headers, timeout=60)
            poll.raise_for_status()
            status = poll.json().get("status") or "Unknown"
        except requests.RequestException as ex:
            print(f"Poll failed: {ex}")
        print(f"Provisioning status: {status}")
        if status in ("active", "failed"):
            break
        time.sleep(delay_seconds)

    if status != "active":
        raise RuntimeError(
            f"Agent version provisioning status is '{status}', expected 'active'."
        )


def _grant_cognitive_services_user(config: AutopilotConfig, client_id: str) -> None:
    print(
        f"==> Granting Cognitive Services User to {client_id} on {config.account_scope}"
    )
    try:
        az(
            "role", "assignment", "create",
            "--assignee", client_id,
            "--role", COGNITIVE_SERVICES_USER_ROLE_ID,
            "--scope", config.account_scope,
            capture=False,
        )
        print("==> Cognitive Services User role assignment created.")
    except subprocess.CalledProcessError as ex:
        # The CLI returns non-zero when the assignment already exists.
        print(f"==> Role assignment may already exist (ignoring): {ex}")


def _patch_endpoint(config: AutopilotConfig, headers: dict[str, str]) -> None:
    patch_url = (
        f"{config.project_endpoint}/agents/{config.agent_name}"
        f"?api-version={AGENTS_API_VERSION}"
    )
    patch_body = {
        "agent_endpoint": {
            "protocols": ["activity"],
            "authorization_schemes": [{"type": "BotServiceRbac"}],
        }
    }
    print(f"==> Patching agent endpoint at: {patch_url}")
    response = requests.patch(patch_url, headers=headers, json=patch_body, timeout=60)
    response.raise_for_status()


def main() -> int:
    try:
        agent_guid = create_agent()
    except requests.HTTPError as ex:
        print(f"❌ Agent creation failed: {ex} — {ex.response.text if ex.response else ''}")
        return 1
    except Exception as ex:  # noqa: BLE001
        print(f"❌ Agent creation failed: {ex}")
        return 1
    print(f"AGENT_GUID={agent_guid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
