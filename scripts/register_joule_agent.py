"""Step 2 of the Joule-agent pipeline — register the simulated SAP Joule agent
in the **Azure AI Foundry control plane**.

The agent itself runs on Azure Container Apps (step 1) and is **never hosted by
Foundry**. This script registers it as a first-class agent in the Foundry control
plane so it inherits the same identity, governance and audit fabric as the
in-Foundry agents — the "agents are digital employees regardless of where they
run" story. Two complementary things are wired up:

  1. **Identity / governance** — the agent version is created with a managed
     **agent identity blueprint** (``ManagedAgentIdentityBlueprintReference``),
     and its endpoint is advertised as speaking **A2A**.

  2. **Reachability** — the externally-hosted A2A endpoint is referenced through
     an ``A2APreviewTool`` (``base_url`` + ``agent_card_path`` + optional project
     ``connection_id``), exactly as the pricing MCP server is referenced through
     an ``MCPTool``. The control-plane shell is a minimal prompt agent whose only
     tool is that external A2A endpoint.

Both rely on Foundry **preview** features (``AgentEndpoints=V1Preview`` and the
``a2a_preview`` tool), so run ``--dry-run`` first to print the exact payload, then
run live once the preview is enabled on your project.

Environment variables:
  AZURE_AI_PROJECT_ENDPOINT     Foundry project endpoint (required for live runs).
  JOULE_AGENT_NAME              Control-plane agent name (default: joule-agent).
  JOULE_AGENT_URL               Public base URL of the deployed A2A agent. If
                                unset it is derived from the Container App FQDN
                                (JOULE_AGENT_APP_NAME, AZURE_RESOURCE_GROUP).
  JOULE_AGENT_APP_NAME          Container App name to resolve the URL from
                                (default: joule-agent).
  JOULE_AGENT_CARD_PATH         Agent-card path (default: /.well-known/agent-card.json).
  JOULE_BLUEPRINT_ID            Managed agent identity blueprint id (Entra Agent
                                ID). Strongly recommended — without it the agent is
                                registered WITHOUT the identity blueprint.
  JOULE_CONNECTION_ID           Optional Foundry project connection id holding auth
                                to the A2A server (for network-restricted ingress).
  JOULE_PREVIEW_FEATURES        Foundry-Features opt-in header value for the preview
                                (default: AgentEndpoints=V1Preview). Override to match
                                what your tenant has enabled, e.g. ExternalAgents=V1Preview.
                                These are PREVIEW features and are not guaranteed
                                enabled on every project/region.
  AZURE_AI_MODEL_DEPLOYMENT_NAME  Model for the control-plane shell agent
                                (default: gpt-4.1-mini).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from azure.ai.projects.models import (
    A2APreviewTool,
    AgentCard,
    AgentCardSkill,
    AgentEndpointConfig,
    AgentEndpointProtocol,
    ManagedAgentIdentityBlueprintReference,
    PromptAgentDefinition,
)

from scripts.deploy_helpers import get_client, get_container_app_fqdn

AGENT_NAME = os.getenv("JOULE_AGENT_NAME", "joule-agent")
CARD_PATH = os.getenv("JOULE_AGENT_CARD_PATH", "/.well-known/agent-card.json")
MODEL = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini")

# Preview feature flag(s) sent as the ``Foundry-Features`` opt-in header to attach
# an external A2A endpoint to a control-plane agent. These are PREVIEW features and
# are NOT guaranteed enabled on every project/region — confirm against your project
# (a live call returns a 4xx when the flag is not honoured). Override via
# JOULE_PREVIEW_FEATURES to match what your tenant has enabled; known agent opt-in
# values include "AgentEndpoints=V1Preview" and "ExternalAgents=V1Preview".
PREVIEW_FEATURES = os.getenv("JOULE_PREVIEW_FEATURES", "AgentEndpoints=V1Preview").strip()
_PREVIEW_HEADERS = {"Foundry-Features": PREVIEW_FEATURES} if PREVIEW_FEATURES else {}

JOULE_AGENT_CARD = AgentCard(
    version="1.0",
    description=(
        "Simulated SAP Joule agent fronting ERP supply-side data. Answers whether the "
        "supply chain can fulfil a planned promotion for a given product and forecast "
        "volume, joining stock, replenishment, open purchase orders and supplier lead "
        "times. Runs outside Foundry on Azure Container Apps; reachable over A2A."
    ),
    skills=[
        AgentCardSkill(
            id="fulfilment-check",
            name="Promotion Fulfilment Check",
            description=(
                "Given a SKU and forecast weekly volume, decide whether the supply chain "
                "can fulfil it from stock, in-transit units, replenishment and open POs."
            ),
        ),
        AgentCardSkill(
            id="stock-lookup",
            name="Stock & Supplier Lookup",
            description=(
                "Return stock on hand, safety stock, in-transit units, supplier, lead "
                "time and open purchase orders for a product."
            ),
        ),
    ],
)


def _resolve_base_url() -> str:
    """Return the public A2A base URL, deriving it from the Container App if unset."""
    url = os.getenv("JOULE_AGENT_URL", "").strip()
    if url:
        return url.rstrip("/")

    resource_group = os.getenv("AZURE_RESOURCE_GROUP", "").strip()
    app_name = os.getenv("JOULE_AGENT_APP_NAME", "joule-agent")
    if resource_group:
        try:
            fqdn = get_container_app_fqdn(resource_group, app_name)
        except (subprocess.CalledProcessError, FileNotFoundError):
            fqdn = ""
        if fqdn:
            return f"https://{fqdn}"
    return ""


def _build_definition(base_url: str) -> PromptAgentDefinition:
    """Control-plane shell: a prompt agent whose only tool is the external A2A agent."""
    connection_id = os.getenv("JOULE_CONNECTION_ID", "").strip()
    a2a_tool = A2APreviewTool(
        base_url=base_url,
        agent_card_path=CARD_PATH,
        **({"project_connection_id": connection_id} if connection_id else {}),
    )
    return PromptAgentDefinition(
        model=MODEL,
        instructions=(
            "You are a thin control-plane proxy for the external SAP Joule supply "
            "agent. Forward supply / fulfilment questions to it over A2A and return "
            "its answer unchanged."
        ),
        tools=[a2a_tool],
    )


def _as_payload(obj) -> dict:
    """Best-effort JSON view of an SDK model for --dry-run printing."""
    for attr in ("as_dict",):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:  # pragma: no cover - defensive
                pass
    try:
        return dict(obj)
    except Exception:  # pragma: no cover - defensive
        return {"repr": repr(obj)}


def deploy(dry_run: bool = False) -> None:
    base_url = _resolve_base_url()
    if not base_url:
        print(
            "Cannot resolve the Joule agent URL: set JOULE_AGENT_URL, or set "
            "AZURE_RESOURCE_GROUP so the joule-agent Container App URL can be derived."
        )
        return

    blueprint_id = os.getenv("JOULE_BLUEPRINT_ID", "").strip()
    blueprint_ref = (
        ManagedAgentIdentityBlueprintReference(blueprint_id=blueprint_id)
        if blueprint_id
        else None
    )

    definition = _build_definition(base_url)
    endpoint_config = AgentEndpointConfig(protocols=[AgentEndpointProtocol.A2A])

    print(f"Registering external A2A agent '{AGENT_NAME}' in the Foundry control plane:")
    print(f"  A2A base URL:     {base_url}")
    print(f"  Agent card path:  {CARD_PATH}")
    print(f"  Identity blueprint: {blueprint_id or '(none — set JOULE_BLUEPRINT_ID)'}")
    print(f"  Shell model:      {MODEL}")
    print(f"  Preview header:   {_PREVIEW_HEADERS or '(none)'}")

    if dry_run:
        print("\n--dry-run: no Azure calls made. create_version payload:")
        payload = {
            "agent_name": AGENT_NAME,
            "definition": _as_payload(definition),
            "blueprint_reference": _as_payload(blueprint_ref) if blueprint_ref else None,
            "description": "Simulated SAP Joule supply agent (external, A2A).",
        }
        print(json.dumps(payload, indent=2, default=str))
        print("\npatch_agent_details payload:")
        print(
            json.dumps(
                {
                    "agent_name": AGENT_NAME,
                    "agent_endpoint": _as_payload(endpoint_config),
                    "agent_card": _as_payload(JOULE_AGENT_CARD),
                },
                indent=2,
                default=str,
            )
        )
        return

    if not os.getenv("AZURE_AI_PROJECT_ENDPOINT"):
        print("\nSkipping live registration: AZURE_AI_PROJECT_ENDPOINT is required.")
        return
    if blueprint_ref is None:
        print(
            "\nWARNING: JOULE_BLUEPRINT_ID is not set — registering WITHOUT a managed "
            "agent identity blueprint. Provision an Entra Agent ID blueprint and set "
            "JOULE_BLUEPRINT_ID to bind the agent's identity."
        )

    client = get_client()
    create_kwargs: dict = {
        "agent_name": AGENT_NAME,
        "definition": definition,
        "description": "Simulated SAP Joule supply agent (external, reached over A2A).",
        "metadata": {"source": "joule-agent", "hosting": "external-aca"},
        "headers": _PREVIEW_HEADERS,
    }
    if blueprint_ref is not None:
        create_kwargs["blueprint_reference"] = blueprint_ref

    client.agents.create_version(**create_kwargs)
    client.beta.agents.patch_agent_details(
        agent_name=AGENT_NAME,
        agent_endpoint=endpoint_config,
        agent_card=JOULE_AGENT_CARD,
    )

    project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "").rstrip("/")
    a2a_base = f"{project_endpoint}/agents/{AGENT_NAME}/endpoint/protocols/a2a"
    print(f"\nAgent '{AGENT_NAME}' registered in the control plane.")
    print(f"  External A2A endpoint: {base_url}")
    print(f"  Foundry A2A card:      {a2a_base}/agentCard/v0.3")


if __name__ == "__main__":
    deploy(dry_run="--dry-run" in sys.argv)
