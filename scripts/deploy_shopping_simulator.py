"""Deploy the shopping simulator multi-agent workflow to a Container App.

Builds ``src/shopping_simulations`` into an image, deploys it as an externally
ingressed Container App serving the Agent Framework **DevUI** on the public port,
and wires the user-assigned managed identity so it can:
  * consume models from the Foundry project (Cognitive Services User), and
  * publish telemetry to Application Insights (Monitoring Metrics Publisher).

Telemetry is exported to Application Insights so the workflow can later be
registered as a Foundry **external agent**.

Usage::

    python -m scripts.deploy_shopping_simulator --build   # build in ACR, then deploy
    python -m scripts.deploy_shopping_simulator           # deploy existing image
    python -m scripts.deploy_shopping_simulator --no-logs # deploy without tailing logs

After the deployment completes the script registers the workflow as a Foundry
**external agent** (so its telemetry appears in the Foundry trace view) and then
shows the latest console log lines for the running instance
(``az containerapp logs show``). Pass ``--no-register`` to skip registration and
``--no-logs`` to skip the log tail.

Environment variables (populated by ``azd up`` into ``./.env``):
  AZURE_RESOURCE_GROUP                   target resource group (required)
  AZURE_REGISTRY                         ACR login server (required)
  AZURE_CONTAINER_APPS_ENVIRONMENT_NAME  Container Apps environment (required)
  AZURE_IDENTITY_NAME                    user-assigned managed identity (required)
  AZURE_AI_PROJECT_ENDPOINT              Foundry project endpoint (required)
  APPLICATIONINSIGHTS_CONNECTION_STRING  telemetry sink
  AGENT_NAME                             external-agent name (default: shopping-simulator)
  OTEL_AGENT_ID                          gen_ai.agent.id / otel_agent_id (default: <AGENT_NAME>-v1)
  SHOPPING_TOOLBOX_NAME                  toolbox to consume (default: shopping-tools)
  TAG                                    image tag (default: latest)
  SHOPPING_SIM_EXTERNAL                  "true" for public ingress (default: true)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from azure.ai.projects.models import ExternalAgentDefinition

from scripts.deploy_helpers import (
    build_image,
    deploy_container_app,
    get_client,
    get_env,
    shared_agent_env,
)

APP_NAME = os.getenv("SHOPPING_SIM_APP_NAME", "shopping-simulator")
PORT = int(os.getenv("SHOPPING_SIM_PORT", "8080"))
_DOCKERFILE = "src/shopping_simulations/Dockerfile"

# External-agent identity. AGENT_NAME is the Foundry registration name and
# OTEL_AGENT_ID is stamped on every span as gen_ai.agent.id; the registration's
# otel_agent_id must match it. Defined once here so the container env and the
# registration call below can't drift apart.
AGENT_NAME = os.getenv("AGENT_NAME", "shopping-simulator")
OTEL_AGENT_ID = os.getenv("OTEL_AGENT_ID", f"{AGENT_NAME}-v1")

# Built-in role definition IDs.
_COGNITIVE_SERVICES_USER = "a97b65f3-24c7-4388-baec-2e87135dc908"
_MONITORING_METRICS_PUBLISHER = "3913510d-42f4-4e42-8a64-420c390055eb"


def build() -> str:
    registry = get_env("AZURE_REGISTRY")
    source_path = Path(__file__).resolve().parents[1]
    dockerfile = str(source_path / _DOCKERFILE)
    return build_image(registry, APP_NAME, source_path, dockerfile=dockerfile)


def _identity_principal_and_client() -> tuple[str, str]:
    """Return (principalId, clientId) of the user-assigned managed identity."""
    rg = get_env("AZURE_RESOURCE_GROUP")
    name = get_env("AZURE_IDENTITY_NAME")
    out = subprocess.run(
        ["az", "identity", "show", "-g", rg, "-n", name,
         "--query", "[principalId, clientId]", "-o", "tsv"],
        check=True, capture_output=True, text=True,
    ).stdout.split()
    return out[0], out[1]


def _grant_role(principal_id: str, role_id: str, scope: str) -> None:
    """Create a role assignment, tolerating one that already exists."""
    try:
        subprocess.run(
            ["az", "role", "assignment", "create",
             "--assignee-object-id", principal_id,
             "--assignee-principal-type", "ServicePrincipal",
             "--role", role_id, "--scope", scope],
            check=True, capture_output=True, text=True,
        )
        print(f"  granted {role_id} on {scope}")
    except subprocess.CalledProcessError as exc:
        if "RoleAssignmentExists" in (exc.stderr or ""):
            print(f"  role {role_id} already assigned on {scope}")
        else:
            print(f"  WARN: could not grant {role_id}: {exc.stderr.strip()}")


def assign_identity_roles(principal_id: str) -> None:
    rg = get_env("AZURE_RESOURCE_GROUP")
    rg_scope = subprocess.run(
        ["az", "group", "show", "-n", rg, "--query", "id", "-o", "tsv"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    print("==> Assigning managed identity roles")
    _grant_role(principal_id, _COGNITIVE_SERVICES_USER, rg_scope)        # consume Foundry models
    _grant_role(principal_id, _MONITORING_METRICS_PUBLISHER, rg_scope)   # publish telemetry


def deploy(tag: str | None = None) -> None:
    project_endpoint = get_env("AZURE_AI_PROJECT_ENDPOINT")
    principal_id, client_id = _identity_principal_and_client()
    assign_identity_roles(principal_id)

    external = os.getenv("SHOPPING_SIM_EXTERNAL", "true").strip().lower() == "true"
    env_vars = {
        **shared_agent_env(project_endpoint),
        "APPLICATIONINSIGHTS_CONNECTION_STRING": os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", ""),
        # Pin the external-agent identity explicitly so the runtime
        # gen_ai.agent.id matches the Foundry registration's otel_agent_id
        # (shopping-simulator-v1) and can't drift from telemetry.py defaults.
        "AGENT_NAME": AGENT_NAME,
        "OTEL_AGENT_ID": OTEL_AGENT_ID,
        "SHOPPING_TOOLBOX_NAME": os.getenv("SHOPPING_TOOLBOX_NAME", "shopping-tools"),
        "TOOLBOX_MCP_ENDPOINT": os.getenv("TOOLBOX_MCP_ENDPOINT", ""),
        "SHOPPING_SIM_MAX_SUPPLIERS": os.getenv("SHOPPING_SIM_MAX_SUPPLIERS", "5"),
        "DEVUI_AUTH_TOKEN": os.getenv("DEVUI_AUTH_TOKEN", ""),
        "AZURE_CLIENT_ID": client_id,
        "HOST": "0.0.0.0",
        "PORT": str(PORT),
    }

    fqdn = deploy_container_app(
        app_name=APP_NAME,
        image_name=APP_NAME,
        port=PORT,
        external=external,
        env_vars=env_vars,
        tag=tag,
        min_replicas=1,
    )
    if fqdn:
        print(f"\nShopping simulator DevUI deployed: https://{fqdn}/")
    else:
        print("\nDeployed, but no ingress FQDN returned — check the Container App ingress.")


def tail_logs() -> None:
    """Show the latest 20 console log lines for the running instance."""
    rg = get_env("AZURE_RESOURCE_GROUP")
    print(f"\n==> Showing the latest 20 console log lines for '{APP_NAME}'\n")
    subprocess.run(
        ["az", "containerapp", "logs", "show",
         "--name", APP_NAME, "--resource-group", rg,
         "--type", "console", "--tail", "20"],
        check=False,
    )


def register_external_agent() -> None:
    """Register the workflow as a Foundry external agent for observability.

    Foundry matches incoming spans to this registration by
    ``gen_ai.agent.id == otel_agent_id``; we pass the same ``OTEL_AGENT_ID`` the
    container stamps on every span. ``create_version`` is idempotent for an
    existing name (it adds a revision), so re-running a deploy is safe.
    """
    print(f"\n==> Registering external agent '{AGENT_NAME}' (otel_agent_id={OTEL_AGENT_ID})\n")
    try:
        client = get_client()
        agent = client.agents.create_version(
            agent_name=AGENT_NAME,
            description="Shopping simulator multi-agent workflow (Agent Framework).",
            definition=ExternalAgentDefinition(otel_agent_id=OTEL_AGENT_ID),
        )
        resolved = agent.versions.latest.definition.otel_agent_id
        print(f"Registered external agent: {agent.name}")
        print(f"Resolved otel_agent_id: {resolved}")
    except Exception as exc:  # pragma: no cover - registration must not fail the deploy
        print(f"WARNING: external-agent registration failed ({exc}); deployment is unaffected.")


if __name__ == "__main__":
    built_tag = build() if "--build" in sys.argv else None
    deploy(tag=built_tag)
    if "--no-register" not in sys.argv:
        register_external_agent()
    if "--no-logs" not in sys.argv:
        tail_logs()
