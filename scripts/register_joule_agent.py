"""Step 2 of the Joule-agent pipeline — register the simulated SAP Joule agent in
**Azure AI Foundry** with an **agent identity blueprint**, and wire it for **A2A**.

The agent runs on Azure Container Apps (step 1) and is **never hosted by Foundry**,
yet it is registered in the Foundry control plane and — the key requirement — it
**uses an agent identity blueprint** (Microsoft Entra Agent ID), so it gets a
governed, auditable identity just like the in-Foundry agents.

What an agent identity blueprint is (see
https://learn.microsoft.com/azure/foundry/agents/concepts/agent-identity and
https://learn.microsoft.com/entra/agent-id/agent-blueprint): a Microsoft Entra ID
object that governs a *class* of agent identities — it carries OAuth credentials
(federated to a managed identity), enables Conditional Access / revoke / audit at
scale, and (for agents that **receive incoming requests from other agents**, like
Joule) an identifier URI + scope. You create one in the Entra admin center
(**Entra ID → Agents → Agent blueprints → New**) or via Microsoft Graph
(https://learn.microsoft.com/entra/agent-id/create-blueprint); record its
``appId`` and pass it as ``JOULE_BLUEPRINT_ID``. This is **the central input** for
the scenario.

The script attaches that blueprint via ``create_version(blueprint_reference=
ManagedAgentIdentityBlueprintReference(blueprint_id=...))`` and reaches the
external endpoint with the documented **A2A tool** pattern
(https://learn.microsoft.com/azure/foundry/agents/how-to/tools/agent-to-agent) —
an ``A2APreviewTool`` bound, preferably, to a ``RemoteA2A`` project connection
(``JOULE_A2A_CONNECTION_NAME``) that stores the endpoint target + auth (including
``AgenticIdentity`` / Entra Agent ID passthrough). When a ``RemoteA2A`` connection
is used the base URL comes from the connection, so ``JOULE_AGENT_URL`` is only
needed without a connection.

The A2A tool is **public preview** (``a2a_preview``). Run ``--dry-run`` first.

> NOTE — there is also a portal-only path to govern Joule as a control-plane
> *asset* (Operate → Register asset → Protocol = A2A), which yields a proxy URL +
> observability and **requires an AI gateway (Azure API Management) on the Foundry
> resource**. That flow is portal-driven and not performed by this script — see
> https://learn.microsoft.com/azure/foundry/control-plane/register-custom-agent.

Environment variables:
  AZURE_AI_PROJECT_ENDPOINT     Foundry project endpoint (required for live runs).
  JOULE_BLUEPRINT_ID            **Agent identity blueprint appId (Entra Agent ID).**
                                The central identity input — create the blueprint in
                                Entra (admin center or Graph) and pass its appId.
  JOULE_AGENT_NAME              Foundry agent name (default: joule-agent).
  JOULE_A2A_CONNECTION_NAME     Name of a RemoteA2A project connection (recommended;
                                resolved to its id at runtime). See the A2A docs to
                                create one (portal: Tools → Connect tool → Custom →
                                Agent2Agent (A2A); or the ARM REST PUT in the docs).
  JOULE_CONNECTION_ID           Explicit connection id (alternative to the name).
  JOULE_AGENT_URL               Public base URL of the deployed A2A agent (only
                                needed without a RemoteA2A connection). If unset it
                                is derived from the Container App FQDN.
  JOULE_AGENT_APP_NAME          Container App name to resolve the URL from
                                (default: joule-agent).
  JOULE_AGENT_CARD_PATH         Agent-card path (default: /.well-known/agent-card.json).
  JOULE_PREVIEW_FEATURES        Foundry-Features opt-in header value for the preview
                                (default: AgentEndpoints=V1Preview). Override to match
                                what your tenant has enabled, e.g. ExternalAgents=V1Preview.
                                These are PREVIEW features and are not guaranteed
                                enabled on every project/region.
  AZURE_AI_MODEL_DEPLOYMENT_NAME  Model for the prompt agent (default: gpt-4.1-mini).
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


def _resolve_connection_id(client) -> str:
    """Resolve a RemoteA2A connection name to its id (documented primary path)."""
    explicit = os.getenv("JOULE_CONNECTION_ID", "").strip()
    if explicit:
        return explicit
    name = os.getenv("JOULE_A2A_CONNECTION_NAME", "").strip()
    if name and client is not None:
        return client.connections.get(name).id
    return ""


def _build_definition(base_url: str, connection_id: str) -> PromptAgentDefinition:
    """Prompt agent whose only tool is the external Joule A2A endpoint.

    Follows the documented A2A tool shape: prefer a RemoteA2A ``project_connection_id``
    (the connection carries the endpoint target + auth); ``base_url`` is only sent
    when no connection is used (or for non-RemoteA2A connections).
    """
    tool_kwargs: dict = {"agent_card_path": CARD_PATH}
    if connection_id:
        tool_kwargs["project_connection_id"] = connection_id
    if base_url and not connection_id:
        tool_kwargs["base_url"] = base_url
    a2a_tool = A2APreviewTool(**tool_kwargs)
    return PromptAgentDefinition(
        model=MODEL,
        instructions=(
            "You are a thin proxy for the external SAP Joule supply agent. Forward "
            "supply / fulfilment questions to it over A2A and return its answer."
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
    connection_name = os.getenv("JOULE_A2A_CONNECTION_NAME", "").strip()
    connection_id = os.getenv("JOULE_CONNECTION_ID", "").strip()
    base_url = _resolve_base_url()

    # A connection (RemoteA2A) carries the endpoint; otherwise we need a base URL.
    if not connection_name and not connection_id and not base_url:
        print(
            "Cannot resolve the Joule endpoint: set JOULE_A2A_CONNECTION_NAME (a "
            "RemoteA2A connection, recommended), or JOULE_AGENT_URL, or set "
            "AZURE_RESOURCE_GROUP so the joule-agent Container App URL can be derived."
        )
        return

    blueprint_id = os.getenv("JOULE_BLUEPRINT_ID", "").strip()
    blueprint_ref = (
        ManagedAgentIdentityBlueprintReference(blueprint_id=blueprint_id)
        if blueprint_id
        else None
    )

    client = None if dry_run else get_client()
    # Live: resolve a connection name -> id. Dry-run: just echo what was provided.
    resolved_conn_id = connection_id
    if not dry_run:
        resolved_conn_id = _resolve_connection_id(client)

    definition = _build_definition(base_url, resolved_conn_id)
    endpoint_config = AgentEndpointConfig(protocols=[AgentEndpointProtocol.A2A])

    print(f"Registering Foundry A2A-tool agent '{AGENT_NAME}':")
    print(f"  A2A connection:   {connection_name or connection_id or '(none — using base URL)'}")
    print(f"  A2A base URL:     {base_url or '(from connection)'}")
    print(f"  Agent card path:  {CARD_PATH}")
    print(f"  Identity blueprint: {blueprint_id or '(none — set JOULE_BLUEPRINT_ID)'}")
    print(f"  Model:            {MODEL}")
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
            "\nWARNING: JOULE_BLUEPRINT_ID is not set — the agent identity blueprint "
            "is the central requirement for this scenario. Create one in Entra "
            "(Entra ID → Agents → Agent blueprints → New, or via Microsoft Graph: "
            "https://learn.microsoft.com/entra/agent-id/create-blueprint), then pass "
            "its appId as JOULE_BLUEPRINT_ID so the agent uses a governed identity."
        )

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

    print(f"\nFoundry agent '{AGENT_NAME}' created with an A2A tool to the Joule endpoint.")
    print(f"  External A2A endpoint: {base_url or '(from connection)'}")
    print(
        "  Other Foundry agents can now call it via the A2A tool. For governed "
        "control-plane asset registration (proxy URL + observability), register it "
        "in the portal (needs an AI gateway): "
        "https://learn.microsoft.com/azure/foundry/control-plane/register-custom-agent"
    )


if __name__ == "__main__":
    deploy(dry_run="--dry-run" in sys.argv)
