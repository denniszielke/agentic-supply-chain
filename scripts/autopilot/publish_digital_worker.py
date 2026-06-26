"""Publish the agent to Microsoft 365 as a hireable digital worker.

Python port of ``publish-digital-worker.ps1``. POSTs a Microsoft 365 publish
request to the AzureML agent-asset endpoint, registering the agent as a digital
worker bound to its agent blueprint. Treats an "already published with this
version" response as success.

Run standalone (requires AGENT_GUID and AGENT_IDENTITY_BLUEPRINT_ID):

    AGENT_GUID=<guid> AGENT_IDENTITY_BLUEPRINT_ID=<id> \
        python -m scripts.autopilot.publish_digital_worker
"""

from __future__ import annotations

import os

import requests

from .common import AI_AZURE_RESOURCE, AutopilotConfig, get_access_token


def publish_digital_worker(
    agent_guid: str,
    blueprint_id: str,
    config: AutopilotConfig | None = None,
) -> None:
    """Publish the agent as a Microsoft 365 digital worker."""
    config = config or AutopilotConfig.from_env()
    if not agent_guid:
        raise ValueError("agent_guid is required to publish the digital worker.")
    if not blueprint_id:
        raise ValueError("blueprint_id is required to publish the digital worker.")

    token = get_access_token(AI_AZURE_RESOURCE)
    workspace_name = f"{config.account_name}@{config.project_name}@AML"
    url = (
        f"https://{config.location}.api.azureml.ms/agent-asset/v2.0/subscriptions/"
        f"{config.subscription_id}/resourceGroups/{config.resource_group}/providers/"
        f"Microsoft.MachineLearningServices/workspaces/{workspace_name}/"
        "microsoft365/publish"
    )

    body = {
        "agentGuid": agent_guid,
        "botId": blueprint_id,
        "publishAsDigitalWorker": True,
        "appPublishScope": "Tenant",
        "subscriptionId": config.subscription_id,
        "agentName": config.agent_name,
        "appVersion": "1.0.0",
        "shortDescription": "Campaign planning A365 agent deployed via the autopilot pipeline.",
        "fullDescription": (
            "A retailer-side campaign-planning digital worker that designs "
            "margin-aware weekly flyers and delivers them through Microsoft 365."
        ),
        "developerName": "Agentic Supply Chain",
        "developerWebsiteUrl": "https://azure.microsoft.com",
        "privacyUrl": "https://privacy.microsoft.com",
        "termsOfUseUrl": "https://www.microsoft.com/legal/terms-of-use",
        "useAgenticUserTemplate": True,
        "agenticUserTemplate": {
            "Id": "digitalWorkerTemplate",
            "File": "agenticUserTemplateManifest.json",
            "SchemaVersion": "0.1.0-preview",
            "AgentIdentityBlueprintId": blueprint_id,
            "CommunicationProtocol": "activityProtocol",
        },
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }

    print(f"==> Publishing digital worker to: {url}")
    response = requests.post(url, headers=headers, json=body, timeout=120)
    if response.status_code >= 400:
        text = response.text or ""
        if "version already exists" in text:
            print("==> A digital worker is already published with this version. Ignoring.")
            return
        response.raise_for_status()
    print("==> Publish digital worker request completed.")


def main() -> int:
    agent_guid = os.getenv("AGENT_GUID", "").strip()
    blueprint_id = os.getenv("AGENT_IDENTITY_BLUEPRINT_ID", "").strip()
    if not agent_guid or not blueprint_id:
        print("❌ AGENT_GUID and AGENT_IDENTITY_BLUEPRINT_ID are required.")
        return 1
    try:
        publish_digital_worker(agent_guid, blueprint_id)
    except requests.HTTPError as ex:
        print(f"❌ Publish failed: {ex} — {ex.response.text if ex.response else ''}")
        return 1
    except Exception as ex:  # noqa: BLE001
        print(f"❌ Publish failed: {ex}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
