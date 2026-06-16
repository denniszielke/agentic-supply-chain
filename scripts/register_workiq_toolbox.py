"""Register the WorkIQ mail MCP server as a Foundry toolbox.

Creates (or updates) a Foundry toolbox backed by the Microsoft Agent 365 WorkIQ
mail MCP server (``mcp_MailTools``).  This makes M365 mail capabilities — reading,
searching, and sending mail on behalf of the signed-in user — available to any
hosted agent in the project via the toolbox MCP endpoint.

Run this after ``azd up`` has provisioned the Foundry project and before deploying
the campaign agent that should consume mail capabilities.

WorkIQ mail MCP server details (from the Agent 365 MCP server catalog):
  URL:      https://agent365.svc.cloud.microsoft/agents/servers/mcp_MailTools
  Scope:    McpServers.Mail.All
  Audience: ea9ffc3e-8a23-4a7d-836d-234d7c7565c1

Environment variables:
  AZURE_AI_PROJECT_ENDPOINT   Foundry project endpoint (required).
  WORKIQ_TOOLBOX_NAME         Toolbox name (default: workiq-mail-tools).
  WORKIQ_CONNECTION_ID        Foundry connection ID used to authorise calls to
                              the WorkIQ mail MCP server.  Required when the
                              Foundry project needs a named connection to pass
                              OAuth tokens to the MCP server.  If omitted the
                              MCPTool is created without a connection reference
                              and authentication must be handled externally.
"""

from __future__ import annotations

import os

from azure.ai.projects.models import MCPTool

from scripts.deploy_helpers import get_client, get_env

# Microsoft Agent 365 WorkIQ mail MCP server (from the A365 MCP server catalog).
# https://learn.microsoft.com/en-us/microsoft-agent-365/tooling-servers-overview
_WORKIQ_MAIL_URL = (
    "https://agent365.svc.cloud.microsoft/agents/servers/mcp_MailTools"
)
_WORKIQ_MAIL_SCOPE = "McpServers.Mail.All"
_WORKIQ_MAIL_AUDIENCE = "ea9ffc3e-8a23-4a7d-836d-234d7c7565c1"

TOOLBOX_NAME = os.getenv("WORKIQ_TOOLBOX_NAME", "workiq-mail-tools")


def deploy() -> None:
    if not os.getenv("AZURE_AI_PROJECT_ENDPOINT"):
        print("Skipping toolbox registration: AZURE_AI_PROJECT_ENDPOINT is required.")
        return

    connection_id = os.getenv("WORKIQ_CONNECTION_ID", "").strip()

    # Register the WorkIQ mail MCP server as a toolbox-backed MCPTool.
    # require_approval="never" lets the hosted agent invoke mail tools without a
    # human approval gate.  Set a connection_id when the Foundry project holds an
    # OAuth connection that should be forwarded to the MCP server.
    tool_kwargs: dict = {
        "server_label": "workiq-mail",
        "server_url": _WORKIQ_MAIL_URL,
        "server_description": (
            "Microsoft Agent 365 WorkIQ mail MCP server.  Provides read, search, "
            "and send capabilities for M365 mail on behalf of the signed-in user. "
            f"Required OAuth scope: {_WORKIQ_MAIL_SCOPE} "
            f"(audience: {_WORKIQ_MAIL_AUDIENCE})."
        ),
        "require_approval": "never",
    }
    if connection_id:
        tool_kwargs["project_connection_id"] = connection_id

    tool = MCPTool(**tool_kwargs)

    client = get_client()
    version = client.beta.toolboxes.create_version(
        name=TOOLBOX_NAME,
        tools=[tool],
        description=(
            "WorkIQ mail toolbox backed by the Microsoft Agent 365 mcp_MailTools "
            "MCP server.  Exposes M365 mail capabilities to hosted agents."
        ),
        metadata={
            "source": "agent365-mcp-mail",
            "mcp_server": "mcp_MailTools",
            "scope": _WORKIQ_MAIL_SCOPE,
            "audience": _WORKIQ_MAIL_AUDIENCE,
        },
    )
    client.beta.toolboxes.update(name=TOOLBOX_NAME, default_version=version.version)

    project_endpoint = get_env("AZURE_AI_PROJECT_ENDPOINT")
    consumer_endpoint = (
        f"{project_endpoint.rstrip('/')}/toolboxes/{TOOLBOX_NAME}/mcp?api-version=v1"
    )
    print(f"Toolbox '{TOOLBOX_NAME}' version '{version.version}' created.")
    print(f"  WorkIQ mail MCP server: {_WORKIQ_MAIL_URL}")
    if connection_id:
        print(f"  Connection ID: {connection_id}")
    else:
        print(
            "  Note: no WORKIQ_CONNECTION_ID set — set it to a Foundry connection "
            "that provides an OAuth token for the WorkIQ mail MCP server."
        )
    print(f"  Consumer endpoint: {consumer_endpoint}")


if __name__ == "__main__":
    deploy()
