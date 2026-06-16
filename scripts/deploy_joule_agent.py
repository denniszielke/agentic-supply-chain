"""Step 1 of the Joule-agent pipeline — deploy the **simulated SAP Joule agent**.

Deploy the standalone A2A agent (``src/joule_agent``) as an **external** Azure
Container App. It is intentionally *not* a Foundry hosted agent — Foundry never
runs it. It is reached over the open A2A protocol and, separately, registered in
the Foundry control plane (step 2) with a managed agent identity blueprint.

Usage::

    # build the image in ACR, then deploy
    python -m scripts.deploy_joule_agent --build

    # deploy only (image already in ACR)
    python -m scripts.deploy_joule_agent

The next step is:
  2. ``scripts/register_joule_agent.py`` — register this agent in the Foundry
     control plane (blueprint identity + external A2A reference).

Environment variables (all populated automatically from ``.env`` after ``azd up``):
  AZURE_RESOURCE_GROUP                   target resource group (required)
  AZURE_REGISTRY                         ACR login server (required)
  AZURE_CONTAINER_APPS_ENVIRONMENT_NAME  Container Apps environment (required)
  AZURE_IDENTITY_NAME                    user-assigned managed identity (required)
  TAG                                    image tag to deploy (default: latest)
  JOULE_AGENT_APP_NAME                   Container App name (default: joule-agent)
  JOULE_AGENT_PORT                       container port (default: 8092)
  JOULE_PUBLIC_URL                       override the public URL advertised in the
                                         agent card (default: derived from the
                                         Container App ingress FQDN)
  JOULE_AGENT_EXTERNAL                   "false" for internal ingress
                                         (default: true — A2A callers need it)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from scripts.deploy_helpers import build_image, deploy_container_app, get_env

APP_NAME = os.getenv("JOULE_AGENT_APP_NAME", "joule-agent")
PORT = int(os.getenv("JOULE_AGENT_PORT", "8092"))
_DOCKERFILE = "src/joule_agent/Dockerfile"


def build() -> str:
    """Build the joule-agent image in ACR (timestamp tag + :latest)."""
    registry = get_env("AZURE_REGISTRY")
    source_path = Path(__file__).resolve().parents[1]
    dockerfile = str(source_path / _DOCKERFILE)
    return build_image(registry, "joule-agent", source_path, dockerfile=dockerfile)


def _base_env() -> dict[str, str]:
    return {
        "JOULE_AGENT_HOST": "0.0.0.0",
        "JOULE_AGENT_PORT": str(PORT),
        "APPLICATIONINSIGHTS_CONNECTION_STRING": os.getenv(
            "APPLICATIONINSIGHTS_CONNECTION_STRING", ""
        ),
    }


def deploy(tag: str | None = None) -> None:
    external = os.getenv("JOULE_AGENT_EXTERNAL", "true").strip().lower() == "true"
    preset_url = os.getenv("JOULE_PUBLIC_URL", "").strip()

    env_vars = _base_env()
    if preset_url:
        env_vars["JOULE_PUBLIC_URL"] = preset_url

    fqdn = deploy_container_app(
        app_name=APP_NAME,
        image_name="joule-agent",
        port=PORT,
        external=external,
        env_vars=env_vars,
        tag=tag,
        readiness_probe_path="/health",
    )

    if not fqdn:
        print(
            "\nJoule agent deployed, but no ingress FQDN was returned. "
            "Set JOULE_AGENT_EXTERNAL=true or check the Container App ingress."
        )
        return

    public_url = preset_url or f"https://{fqdn}"

    # The agent card must advertise the public URL. When it was not preset we now
    # know the FQDN, so redeploy once with JOULE_PUBLIC_URL set (bicep is
    # idempotent) so the served card points at the real endpoint.
    if not preset_url:
        env_vars["JOULE_PUBLIC_URL"] = public_url
        deploy_container_app(
            app_name=APP_NAME,
            image_name="joule-agent",
            port=PORT,
            external=external,
            env_vars=env_vars,
            tag=tag,
            readiness_probe_path="/health",
        )

    card_url = f"{public_url}/.well-known/agent-card.json"
    print(f"\nJoule agent (simulated SAP Joule) deployed: {public_url}")
    print(f"  A2A agent card: {card_url}")
    print("Register it in the Foundry control plane with:")
    print(f"  JOULE_AGENT_URL={public_url} python -m scripts.register_joule_agent")


if __name__ == "__main__":
    do_build = "--build" in sys.argv
    built_tag: str | None = None
    if do_build:
        built_tag = build()
    deploy(tag=built_tag)
