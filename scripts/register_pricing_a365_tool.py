"""Print the a365 CLI command to register the pricing MCP server as a BYO tool.

Builds and outputs the ``a365 develop-mcp register-external-mcp-server`` command
from environment variables so it can be reviewed and run manually, or piped
directly to a shell.

Usage::

    # Print the command
    python -m scripts.register_pricing_a365_tool

    # Print and execute immediately
    eval $(python -m scripts.register_pricing_a365_tool)

Environment variables:
  PRICING_MCP_URL          MCP endpoint URL of the deployed pricing server.
                           If unset, derived from the ``pricing-mcp-server``
                           Container App FQDN using AZURE_RESOURCE_GROUP.
  PRICING_MCP_APP_NAME     Container App name (default: pricing-mcp-server).
  AZURE_RESOURCE_GROUP     Resource group containing the Container App.
  PRICING_MCP_SERVER_NAME  A365 server identifier -- must start with ``ext_``,
                           <= 20 chars (default: ext_pricing).
  PRICING_MCP_PUBLISHER    Publisher name in MOS package metadata
                           (default: Contoso).
  PRICING_MCP_DESCRIPTION  Server description in MOS package metadata.
  PRICING_MCP_AUTH_TYPE    EntraOAuth | ExternalOAuth | APIKey | NoAuth
                           (default: NoAuth).
  PRICING_MCP_TOOLS        Comma-separated tool names to advertise.
                           Defaults to all seven pricing tools.
  A365_DRY_RUN             Set to ``true`` to append ``--dry-run``.
"""

from __future__ import annotations

import os
import subprocess

from scripts.deploy_helpers import get_container_app_fqdn

_DEFAULT_TOOLS = (
    "list_categories,list_products,get_product_pricing,"
    "get_category_margin_forecast,get_volume_forecast,simulate_price_change,list_personas"
)


def _resolve_mcp_url() -> str:
    url = os.getenv("PRICING_MCP_URL", "").strip()
    if url:
        return url
    resource_group = os.getenv("AZURE_RESOURCE_GROUP", "").strip()
    app_name = os.getenv("PRICING_MCP_APP_NAME", "pricing-mcp-server")
    if resource_group:
        try:
            fqdn = get_container_app_fqdn(resource_group, app_name)
        except (subprocess.CalledProcessError, FileNotFoundError):
            fqdn = ""
        if fqdn:
            return f"https://{fqdn}/mcp"
    return ""


def build_command(mcp_url: str) -> list[str]:
    cmd = [
        "a365", "develop-mcp", "register-external-mcp-server",
        "--server-name", os.getenv("PRICING_MCP_SERVER_NAME", "ext_pricing").strip(),
        "--server-url",  mcp_url,
        "--publisher",   os.getenv("PRICING_MCP_PUBLISHER", "Contoso").strip(),
        "--description", os.getenv(
            "PRICING_MCP_DESCRIPTION",
            "Internal retail pricing MCP server - provides procurement cost, "
            "weekly volume forecasts, and margin data for retail categories.",
        ).strip(),
        "--auth-type",   os.getenv("PRICING_MCP_AUTH_TYPE", "NoAuth").strip(),
        "--tools",       os.getenv("PRICING_MCP_TOOLS", _DEFAULT_TOOLS).strip(),
    ]
    if os.getenv("A365_DRY_RUN", "false").strip().lower() == "true":
        cmd.append("--dry-run")
    return cmd


def _shell_quote(s: str) -> str:
    """Wrap a value in double-quotes if it contains spaces or special chars."""
    needs_quoting = any(c in s for c in (' ', '\t', '"', "'", '\\', '(', ')'))
    return f'"{s}"' if needs_quoting else s


def deploy() -> None:
    mcp_url = _resolve_mcp_url()
    if not mcp_url:
        print(
            "Error: cannot resolve pricing MCP URL.\n"
            "Set PRICING_MCP_URL, or set AZURE_RESOURCE_GROUP so the URL can be\n"
            "derived from the pricing-mcp-server Container App FQDN."
        )
        return

    cmd = build_command(mcp_url)

    # Render as a readable multi-line shell command.
    # Positional tokens (a365 / sub-commands) go on the first line;
    # each --flag value pair on its own continuation line.
    parts: list[str] = []
    i = 0
    while i < len(cmd):
        token = cmd[i]
        if token.startswith("--") and i + 1 < len(cmd) and not cmd[i + 1].startswith("--"):
            parts.append(f"{token} {_shell_quote(cmd[i + 1])}")
            i += 2
        else:
            parts.append(_shell_quote(token))
            i += 1

    print(" \\\n  ".join(parts))


if __name__ == "__main__":
    deploy()
