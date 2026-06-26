"""Create OAuth2 permission grants for the blueprint service principal.

Python port of ``create-blueprintsp-oauth2-grants.ps1``. Grants the blueprint
service principal the delegated permissions it needs against the Prod MCP and
APX first-party applications so inheritable MCP scopes work. Treats "Permission
entry already exists" as success.

Run standalone (requires AGENT_IDENTITY_BLUEPRINT_ID):

    AGENT_IDENTITY_BLUEPRINT_ID=<id> \
        python -m scripts.autopilot.create_oauth2_grants
"""

from __future__ import annotations

import os

import requests

from .common import (
    APX_APP_ID,
    APX_OAUTH_SCOPE,
    GRAPH_RESOURCE,
    MCP_OAUTH_SCOPE,
    PROD_MCP_APP_ID,
    az,
    get_access_token,
)

_GRANTS_URL = "https://graph.microsoft.com/v1.0/oauth2PermissionGrants"


def _sp_object_id(app_id: str) -> str:
    obj_id = az("ad", "sp", "show", "--id", app_id, "--query", "id", "-o", "tsv")
    if not obj_id:
        raise RuntimeError(f"Failed to resolve service principal for app id {app_id}.")
    return obj_id


def _post_grant(graph_token: str, grant: dict[str, object]) -> None:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {graph_token}",
    }
    response = requests.post(_GRANTS_URL, headers=headers, json=grant, timeout=60)
    if response.status_code >= 400:
        text = response.text or ""
        if "Permission entry already exists" in text:
            print("==> Permission already exists — ignoring.")
            return
        response.raise_for_status()
    print("==> OAuth2 permission grant created.")


def create_oauth2_grants(blueprint_id: str) -> None:
    """Create the MCP + APX OAuth2 grants for the blueprint service principal."""
    if not blueprint_id:
        raise ValueError("blueprint_id is required to create OAuth2 grants.")

    blueprint_sp = _sp_object_id(blueprint_id)
    apx_sp = _sp_object_id(APX_APP_ID)
    prod_mcp_sp = _sp_object_id(PROD_MCP_APP_ID)
    graph_token = get_access_token(GRAPH_RESOURCE)

    print("==> Creating OAuth2 permission grants for blueprint service principal...")
    _post_grant(
        graph_token,
        {
            "clientId": blueprint_sp,
            "consentType": "AllPrincipals",
            "principalId": None,
            "resourceId": prod_mcp_sp,
            "scope": MCP_OAUTH_SCOPE,
        },
    )
    _post_grant(
        graph_token,
        {
            "clientId": blueprint_sp,
            "consentType": "AllPrincipals",
            "principalId": None,
            "resourceId": apx_sp,
            "scope": APX_OAUTH_SCOPE,
        },
    )


def main() -> int:
    blueprint_id = os.getenv("AGENT_IDENTITY_BLUEPRINT_ID", "").strip()
    if not blueprint_id:
        print("❌ AGENT_IDENTITY_BLUEPRINT_ID is required.")
        return 1
    try:
        create_oauth2_grants(blueprint_id)
    except requests.HTTPError as ex:
        print(f"❌ OAuth2 grant failed: {ex} — {ex.response.text if ex.response else ''}")
        return 1
    except Exception as ex:  # noqa: BLE001
        print(f"❌ OAuth2 grant failed: {ex}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
