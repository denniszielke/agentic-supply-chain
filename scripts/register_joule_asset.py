"""Optional step 3 — governed control-plane **asset** registration for the Joule agent.

This is the second, *governance* way the external Joule agent meets Foundry (in
addition to the A2A tool wired by ``register_joule_agent``). Registering Joule as a
control-plane **asset** gives it a **proxy URL** (via Azure API Management), access
control (block/unblock) and **observability** — the Purview/audit story.

Per the official docs this capability is **portal-only** today (Foundry (new)):
https://learn.microsoft.com/azure/foundry/control-plane/register-custom-agent
There is no REST/SDK/CLI to perform the registration, and it **requires an AI
gateway (Azure API Management) on the Foundry resource**. So this script does not
register anything — instead it runs the **prerequisite checks** and prints the
**exact copy-paste values** for the portal wizard, turning a fuzzy manual task into
a deterministic checklist.

Usage::

    python -m scripts.register_joule_asset

Reads the same environment variables as the other Joule scripts.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request

from scripts.register_joule_agent import AGENT_NAME, CARD_PATH, _resolve_base_url

OK, TODO, WARN = "[ OK ]", "[TODO]", "[WARN]"


def _check_endpoint(base_url: str) -> tuple[str, str]:
    if not base_url:
        return TODO, "Deploy the agent first (scripts.deploy_joule_agent) or set JOULE_AGENT_URL"
    card_url = f"{base_url.rstrip('/')}{CARD_PATH}"
    try:
        req = urllib.request.Request(card_url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 (trusted URL)
            card = json.loads(resp.read().decode("utf-8"))
        if isinstance(card, dict) and card.get("name"):
            return OK, f"reachable ({card.get('name')})"
        return WARN, f"unexpected card response at {card_url}"
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        return WARN, f"not reachable: {card_url} ({exc})"


def _check_apim(resource_group: str) -> tuple[str, str]:
    """Best-effort: is there an API Management instance in the resource group?

    An AI gateway is backed by APIM. This is a hint, not a guarantee the gateway is
    linked to the Foundry resource — confirm in the portal (Operate -> Admin -> AI Gateway).
    """
    if not resource_group:
        return TODO, "set AZURE_RESOURCE_GROUP to auto-detect APIM"
    try:
        result = subprocess.run(
            ["az", "apim", "list", "-g", resource_group, "--query", "[].name", "-o", "tsv"],
            check=True,
            capture_output=True,
            text=True,
        )
        names = [n for n in result.stdout.split() if n]
        if names:
            return OK, f"APIM found in {resource_group}: {', '.join(names)}"
        return TODO, (
            f"no APIM in {resource_group} — enable an AI gateway: "
            "https://learn.microsoft.com/azure/foundry/configuration/enable-ai-api-management-gateway-portal"
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return WARN, f"could not check APIM ({exc}); verify the AI gateway in the portal"


def _check_app_insights() -> tuple[str, str]:
    if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "").strip():
        return OK, "APPLICATIONINSIGHTS_CONNECTION_STRING is set (wire it to the project)"
    return TODO, "connect Application Insights to the project (Operate -> Admin -> Connected resources)"


def main() -> int:
    base_url = _resolve_base_url()
    resource_group = os.getenv("AZURE_RESOURCE_GROUP", "").strip()
    otel_id = os.getenv("JOULE_OTEL_AGENT_ID", AGENT_NAME)

    print("\nJoule agent - control-plane ASSET registration (portal-only, optional)")
    print("=" * 62)
    print(
        "Registering Joule as a governed asset gives it a proxy URL (via APIM),\n"
        "access control and observability. This step is performed in the Foundry\n"
        "(new) portal; there is no API. This helper checks prerequisites and prints\n"
        "the exact values to paste into the wizard.\n"
    )

    print("Prerequisites")
    print("-" * 62)
    for level, detail in (
        _check_endpoint(base_url),
        _check_apim(resource_group),
        _check_app_insights(),
    ):
        print(f"{level} {detail}")

    card_path = CARD_PATH or "/.well-known/agent-card.json"
    print("\nPortal steps  (Foundry (new) -> Operate -> Register asset)")
    print("-" * 62)
    print("1. Confirm an AI gateway is enabled: Operate -> Admin -> AI Gateway.")
    print("2. Confirm Application Insights is connected: Operate -> Admin -> Connected resources.")
    print("3. Operate -> Overview -> Register asset, then enter:")
    print()
    print("   Field                  Value")
    print("   ---------------------  ----------------------------------------------")
    print(f"   Agent URL              {base_url or '<deploy first / set JOULE_AGENT_URL>'}")
    print("   Protocol               A2A")
    print(f"   A2A agent card URL     {card_path}")
    print(f"   OpenTelemetry agent ID {otel_id}")
    print(f"   Agent name             {AGENT_NAME}")
    print("   Description            Simulated SAP Joule supply agent (external, A2A)")
    print()
    print("4. Save. Foundry issues a new proxy URL (apim-...azure-api.net/...) -")
    print("   distribute THAT url to callers; the original auth still applies.")
    print()
    print("Docs: https://learn.microsoft.com/azure/foundry/control-plane/register-custom-agent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
