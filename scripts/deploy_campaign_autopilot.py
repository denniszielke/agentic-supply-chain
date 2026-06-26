"""Deploy the campaign A365 *autopilot* digital worker — end to end.

This is the wrapper around the individual step scripts in ``scripts/autopilot``
(Python ports of the Foundry ``foundry-autopilot-agent`` PowerShell scripts). It
mirrors ``post-provision.ps1`` but also provisions the autopilot infrastructure
first, and uses a REMOTE ACR build (no local Docker).

Pipeline:
  0. provision_infra            — create the MAIB (data-plane), grant the project
                                  identity roles, deploy the bot service bicep
                                  → blueprint id
  1. build_image                — az acr build the agent image (remote)
  2. create_agent               — create the hosted agent version + wire endpoint
  3. publish_digital_worker     — publish to Microsoft 365 as a digital worker
  4. create_oauth2_grants       — grant blueprint SP the MCP/APX OAuth2 scopes
  5. add_blueprint_owner        — add the current user as blueprint app owner
  6. configure_blueprint_backend (optional, --configure-backend)

Prerequisites: run ``azd up`` first so ./.env has the project/registry/etc.
outputs, and be logged in with ``az login`` (Owner on the subscription).

Usage:
  python -m scripts.deploy_campaign_autopilot
  python -m scripts.deploy_campaign_autopilot --skip-infra --blueprint-id <id>
  python -m scripts.deploy_campaign_autopilot --configure-backend
"""

from __future__ import annotations

import argparse
import os

from scripts.autopilot.add_blueprint_owner import add_blueprint_owner
from scripts.autopilot.build_image import build_image
from scripts.autopilot.common import AutopilotConfig
from scripts.autopilot.configure_blueprint_backend import configure_blueprint_backend
from scripts.autopilot.create_agent import create_agent
from scripts.autopilot.create_oauth2_grants import create_oauth2_grants
from scripts.autopilot.provision_infra import provision_infra
from scripts.autopilot.publish_digital_worker import publish_digital_worker


def _banner(title: str) -> None:
    print("\n" + "=" * 15 + f" {title} " + "=" * 15)


def deploy(args: argparse.Namespace) -> int:
    config = AutopilotConfig.from_env()

    # 0. Provision infrastructure (or reuse an existing blueprint id).
    blueprint_id = args.blueprint_id or os.getenv("AGENT_IDENTITY_BLUEPRINT_ID", "").strip()
    if args.skip_infra:
        if not blueprint_id:
            print(
                "❌ --skip-infra requires --blueprint-id or AGENT_IDENTITY_BLUEPRINT_ID."
            )
            return 1
        print(f"==> Skipping infra provisioning; using blueprint id {blueprint_id}")
    else:
        _banner("Provisioning autopilot infrastructure")
        blueprint_id = provision_infra(config)

    # 1. Build and push the agent image (remote ACR build).
    _banner("Building and pushing agent image (ACR build)")
    build_image(blueprint_id, config)

    # 2. Create the hosted agent version.
    _banner("Creating agent version")
    agent_guid = create_agent(config)

    # 3. Publish the digital worker to Microsoft 365.
    _banner("Publishing digital worker")
    publish_digital_worker(agent_guid, blueprint_id, config)

    # 4. OAuth2 grants for the blueprint service principal.
    _banner("OAuth2 grants for blueprint SP")
    create_oauth2_grants(blueprint_id)

    # 5. Add the current user as a blueprint owner.
    _banner("Adding current user as blueprint owner")
    add_blueprint_owner(blueprint_id)

    # 6. Optionally configure the blueprint backend in the Teams Developer Portal.
    if args.configure_backend:
        _banner("Configuring blueprint backend (Teams Developer Portal)")
        configure_blueprint_backend(blueprint_id)

    print("\n✅ Campaign A365 autopilot deployment finished.")
    print(f"   AGENT_IDENTITY_BLUEPRINT_ID = {blueprint_id}")
    print(f"   AGENT_NAME                  = {config.agent_name}")
    print(
        "\nNext: approve the agent blueprint in the Microsoft 365 admin center "
        "(https://admin.cloud.microsoft/?#/agents/all/requested), configure it in "
        "the Teams Developer Portal, then create agent instances in Teams."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-infra",
        action="store_true",
        help="Skip the bicep deployment and reuse an existing blueprint id.",
    )
    parser.add_argument(
        "--blueprint-id",
        default="",
        help="Existing agent identity blueprint client id (with --skip-infra).",
    )
    parser.add_argument(
        "--configure-backend",
        action="store_true",
        help="Also configure the blueprint backend in the Teams Developer Portal.",
    )
    args = parser.parse_args()
    try:
        return deploy(args)
    except Exception as ex:  # noqa: BLE001
        print(f"❌ Autopilot deployment failed: {ex}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
