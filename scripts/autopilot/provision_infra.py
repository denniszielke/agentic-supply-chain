"""Provision the autopilot (A365) infrastructure layer.

Three steps:
  1. Create the Managed Agent Identity Blueprint (MAIB) via the Foundry
     data-plane API (``create_maib``) → blueprint client id.
  2. Grant the existing project identity AcrPull + Cognitive Services User
     (``grant_project_roles``), tolerating assignments ``azd`` already created.
  3. Deploy ``infra/autopilot/main.bicep`` (the Azure Bot + Teams channel),
     passing the blueprint client id as the bot ``msaAppId``.

MAIB creation and role grants were moved out of bicep (see the template header)
to avoid the deployment-script storage account, which is blocked by policy in
some tenants.

Returns the agent identity blueprint (app) client id.

Run standalone:

    python -m scripts.autopilot.provision_infra
"""

from __future__ import annotations

import subprocess

from .common import AutopilotConfig, az, repo_root
from .create_maib import create_maib
from .grant_project_roles import grant_project_roles


def provision_infra(config: AutopilotConfig | None = None) -> str:
    """Create the MAIB, grant roles, deploy the bot, return the blueprint id."""
    config = config or AutopilotConfig.from_env()
    print(config.summary())

    # 1. Create (or fetch) the managed agent identity blueprint.
    blueprint_id = create_maib(config)

    # 2. Grant the project identity the roles a hosted agent needs.
    grant_project_roles(config)

    # 3. Deploy the bot service via bicep.
    template = repo_root() / "infra" / "autopilot" / "main.bicep"
    deployment_name = f"campaign-autopilot-{config.agent_name}"
    params = [
        f"accountName={config.account_name}",
        f"projectName={config.project_name}",
        f"agentName={config.agent_name}",
        f"blueprintClientId={blueprint_id}",
    ]

    print(f"==> Deploying bot service ({template})")
    az(
        "deployment", "group", "create",
        "--resource-group", config.resource_group,
        "--name", deployment_name,
        "--template-file", str(template),
        "--parameters", *params,
        capture=False,
    )

    print(f"==> Agent identity blueprint client id: {blueprint_id}")
    return blueprint_id


def main() -> int:
    try:
        blueprint_id = provision_infra()
    except subprocess.CalledProcessError as ex:
        print(f"❌ Azure CLI command failed: {ex}")
        return 1
    except Exception as ex:  # noqa: BLE001
        print(f"❌ Provisioning failed: {ex}")
        return 1
    print(f"AGENT_IDENTITY_BLUEPRINT_ID={blueprint_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
